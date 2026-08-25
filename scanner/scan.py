"""
iNat Comment Moderation Scanner
================================

Run by the GitHub Actions workflow on a schedule. Scans iNaturalist comment
activity, flags likely profanity/insults/slurs/toxicity for human review,
and writes results to docs/data/backlog.json for the static dashboard.

HOW THE "LAST FOUR MONTHS, EVERY RUN" WINDOW ACTUALLY WORKS
    Literally re-fetching iNat's full global comment history for the
    trailing 4 months on every run would mean re-downloading a huge and
    mostly-unchanged dataset every few hours, re-scoring comments already
    scored last time. Instead:
      - First run ever: backfill the full LOOKBACK_MONTHS window.
      - Every run after that: fetch only observations updated since the
        last completed run (with a small overlap buffer), so the backlog's
        *coverage* stays a rolling 4-month window, but each run's work is
        proportional to what's new, not to the whole window.
    If you genuinely want a full rescan on some cadence (e.g. to catch
    anything the incremental approach might have missed), delete
    docs/data/state.json before a run to force a fresh backfill.

SCALE WARNING
    "All comment activity on iNaturalist" is a lot of observations. The
    INAT_PLACE_ID / INAT_PROJECT_ID env vars narrow the scan to a place or
    project (e.g. your organization's iNat project) instead of the whole
    site -- worth doing, since local-model scoring on CPU is slower than a
    hosted API call was.

THIS IS A TRIAGE TOOL, NOT AN ENFORCEMENT TOOL
    Toxicity classifiers produce false positives and false negatives. Every
    flagged item needs a human curator/moderator to look at the actual
    observation and comment before any action is taken.

TWO INDEPENDENT REVIEW PASSES
    1. Fast/free/unlimited: every new comment is still scored in isolation
       by the local toxicity model (or Perspective/wordlist fallback) as
       before -- see score_comment().
    2. Slower/budget-limited: every observation touched this run also gets
       its *entire* comment + identification thread queued for a guideline
       -grounded LLM review (llm_review.py), via OpenRouter's free tier.
       This is what catches things a single comment can't show on its own
       -- spam patterns repeated across a thread, ID pile-ons, bad-faith
       identification behavior. It only runs if OPENROUTER_API_KEY is set.
       Because OpenRouter's free tier caps how many calls we can make per
       day (see rate_budget.py), this pass drains a persistent queue
       (review_queue.py) a bit at a time across runs rather than trying to
       review everything in one go -- see README for the actual numbers
       and what narrowing INAT_PLACE_ID/INAT_PROJECT_ID does to keep the
       queue from outpacing the daily budget.
"""

import os
import json
import sys
import requests
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
from inat_api import iter_updated_observations, fetch_observation  # noqa: E402
import local_model  # noqa: E402
from local_filter import local_flag  # noqa: E402
from perspective import PerspectiveClient  # noqa: E402
from openrouter_client import OpenRouterClient  # noqa: E402
import llm_review  # noqa: E402
import review_queue  # noqa: E402
from rate_budget import DailyBudget  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
BACKLOG_PATH = os.path.join(DATA_DIR, "backlog.json")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
SEEN_PATH = os.path.join(DATA_DIR, "seen_comments.json")
MANUAL_RESOLVED_PATH = os.path.join(DATA_DIR, "manual_resolved.json")
CONTROL_PATH = os.path.join(DATA_DIR, "control.json")
REVIEW_QUEUE_PATH = os.path.join(DATA_DIR, "review_queue.json")
LLM_BUDGET_PATH = os.path.join(DATA_DIR, "llm_budget.json")

def env_or_default(name, default, cast=str):
    """Like os.environ.get(name, default), but also falls back to default
    when the env var is set to an empty string -- which is what a GitHub
    Actions repo variable becomes (${{ vars.X }} -> "") when it's left
    unset, rather than the env var being absent entirely."""
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return cast(val)


LOOKBACK_MONTHS = env_or_default("LOOKBACK_MONTHS", 4, int)
PLACE_ID = os.environ.get("INAT_PLACE_ID") or None
PROJECT_ID = os.environ.get("INAT_PROJECT_ID") or None
REQUEST_DELAY = env_or_default("INAT_REQUEST_DELAY", 1.0, float)
OVERLAP_MINUTES = 15  # re-check a small buffer around the last run's cutoff
SNIPPET_MAX_CHARS = 400

# How many consecutive full RUNS can fail to reach the iNat API before the
# scanner auto-pauses itself (via control.json) instead of quietly retrying
# against something that's down/overloaded every single cron tick.
RUN_FAILURE_THRESHOLD = env_or_default("RUN_FAILURE_THRESHOLD", 3, int)

DEFAULT_CONTROL = {
    "enabled": True,
    "paused_at": None,
    "paused_by": None,
    "note": None,
    "consecutive_run_failures": 0,
}


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def months_ago(months):
    # Simple approximation (30 days/month) -- fine for a rolling window.
    return datetime.now(timezone.utc) - timedelta(days=30 * months)


def parse_iso(ts):
    """Robustly parse an iNat API timestamp. datetime.fromisoformat() only
    learned to accept a trailing 'Z' (as in '2026-08-23T04:58:02.390Z',
    which is what the API actually returns) on Python 3.11+, so normalize
    it ourselves -- this behaves the same on every Python version instead
    of silently depending on which one happens to be running. Always
    returns a tz-aware datetime (assumes UTC if the string has no offset).
    Returns None if the string is empty or genuinely unparseable."""
    if not ts:
        return None
    ts = ts.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def determine_since(state):
    floor = months_ago(LOOKBACK_MONTHS)
    last_run = state.get("last_run_completed_at")
    if not last_run:
        return floor
    last_run_dt = parse_iso(last_run)
    if last_run_dt is None:
        return floor
    since = last_run_dt - timedelta(minutes=OVERLAP_MINUTES)
    return max(since, floor)


def make_backlog_item(obs, item, reasons, max_score, kind="comment"):
    """Builds a backlog item from a single scored comment OR identification
    (an identification's own optional note/remarks field is scored exactly
    like a comment body -- see the main loop in run()). `comment_id`
    doubles as the composite dict key used everywhere (items_by_id, the
    `seen` cache, manual_resolved.json): plain numeric for a comment
    (unchanged, for backward compatibility with existing
    manual_resolved.json files), `ident-<id>` for an identification --
    mirroring the existing `llm-obs-<id>` scheme for whole-thread flags --
    so the two id spaces (comments and identifications are separate iNat
    resources with separate counters) can never collide."""
    raw_id = item.get("id")
    composite_id = raw_id if kind == "comment" else f"ident-{raw_id}"
    body = (item.get("body") or "")
    if kind == "identification":
        # Distinguishing a real ID attempt from a "miscreant" hiding
        # off-topic text in the ID note field is exactly the point here --
        # keep the claimed taxon visible right alongside the flagged text.
        taxon = (item.get("taxon") or {}).get("name") or "?"
        body = f"[identified as {taxon}] {body}".strip()
    return {
        "kind": kind,  # "comment" or "identification" -- see recheck loop in run()
        "comment_id": composite_id,
        "observation_id": obs.get("id"),
        "observation_uri": obs.get("uri", f"https://www.inaturalist.org/observations/{obs.get('id')}"),
        "species_guess": obs.get("species_guess") or "(no species guess)",
        "observation_owner": (obs.get("user") or {}).get("login", ""),
        "commenter": (item.get("user") or {}).get("login", ""),
        "body_snippet": body[:SNIPPET_MAX_CHARS],
        "comment_created_at": item.get("created_at", ""),
        "flagged_at": datetime.now(timezone.utc).isoformat(),
        "reasons": reasons,
        "max_score": max_score,
        "status": "pending",
        "resolved_at": None,
        "resolved_reason": None,
    }


def make_backlog_item_from_llm(snapshot, verdict):
    """Builds a backlog item from a full-thread LLM verdict (see
    llm_review.py). Uses a synthetic string comment_id (llm-obs-<id>) so it
    can't collide with a real numeric comment id in items_by_id, since this
    flag is about the whole thread, not one comment."""
    reasons = [f"guideline:{c}" for c in verdict["categories"]] or ["guideline:flagged"]
    return {
        "kind": "llm_thread",  # whole-thread flag -- see recheck loop in run()
        "comment_id": f"llm-obs-{snapshot['observation_id']}",
        "observation_id": snapshot["observation_id"],
        "observation_uri": snapshot["observation_uri"],
        "species_guess": snapshot["species_guess"],
        "observation_owner": snapshot["observation_owner"],
        "commenter": verdict["flagged_user"] or "(whole thread)",
        "body_snippet": verdict["reasoning"][:SNIPPET_MAX_CHARS],
        "comment_created_at": snapshot.get("updated_at", ""),
        "flagged_at": datetime.now(timezone.utc).isoformat(),
        "reasons": reasons,
        "max_score": verdict["score"],
        "status": "pending",
        "resolved_at": None,
        "resolved_reason": None,
    }


_perspective = PerspectiveClient()  # no-op / .enabled=False unless PERSPECTIVE_API_KEY is set


def score_comment(text):
    """Returns (reasons, max_score). Prefers Perspective when a
    PERSPECTIVE_API_KEY is configured (see perspective.py for why that's
    unlikely unless you got a key before Feb 2026 -- Perspective is being
    sunsetted and isn't accepting new sign-ups); falls back to the local
    toxicity model, then to the local wordlist filter, in that order,
    whenever the previous stage is unavailable or fails on this
    particular comment.

    The local model is unitary/unbiased-toxic-roberta, trained specifically
    to avoid flagging ordinary identity/descriptor words (male, female,
    black, white, etc.) as toxic -- see local_model.py for why that
    matters for a nature-ID site. Still, treat every flag as "worth a
    human look", not a verdict.
    """
    if _perspective.enabled:
        scores = _perspective.score(text)
        if scores is not None:
            reasons = _perspective.flagged_reasons(scores)
            max_score = max(scores.values()) if scores else 0.0
            return reasons, max_score
        # Perspective unavailable for this comment (unsupported language,
        # or its circuit breaker tripped for this run) -- fall through
        # instead of silently treating it as clean.

    scores = None
    if local_model.enabled():
        scores = local_model.score(text)

    if scores is not None:
        reasons = local_model.flagged_reasons(scores)
        max_score = max(scores.values()) if scores else 0.0
        return reasons, max_score

    # Fallback path (model unavailable, or this comment failed to score)
    if local_flag(text):
        return ["local-wordlist-match"], 1.0
    return [], 0.0


def pause_scanning(control, reason, by="auto"):
    control["enabled"] = False
    control["paused_at"] = datetime.now(timezone.utc).isoformat()
    control["paused_by"] = by
    control["note"] = reason
    save_json(CONTROL_PATH, control)
    print(f"\n*** SCANNING AUTO-PAUSED: {reason} ***")
    print("Fix the underlying issue, then set \"enabled\": true in "
          "docs/data/control.json to resume.")


def run():
    control = {**DEFAULT_CONTROL, **load_json(CONTROL_PATH, {})}

    # --- Kill switch check #1: has a human or a previous run paused this? ---
    if not control.get("enabled", True):
        print("Scanning is paused (docs/data/control.json has \"enabled\": false).")
        if control.get("note"):
            print(f"Reason: {control['note']}")
        print("No API calls will be made. Set \"enabled\": true in that file "
              "(and commit it) to resume.")
        return

    state = load_json(STATE_PATH, {})
    backlog = load_json(BACKLOG_PATH, {"generated_at": None, "lookback_months": LOOKBACK_MONTHS, "items": []})
    seen = load_json(SEEN_PATH, {})  # comment_id (str) -> created_at
    manual_resolved = set(load_json(MANUAL_RESOLVED_PATH, []))
    llm_queue = review_queue.load_queue(REVIEW_QUEUE_PATH)  # observation_id (str) -> thread snapshot

    items_by_id = {str(item["comment_id"]): item for item in backlog["items"]}
    for item in items_by_id.values():
        item.setdefault("kind", "comment")  # backfill items written before this field existed

    llm_client = OpenRouterClient()
    if llm_client.enabled:
        print(f"Guideline LLM review enabled (model={llm_client.model}). "
              f"{len(llm_queue)} observation(s) already queued for review.")
    else:
        print("OPENROUTER_API_KEY not set -- skipping full-thread guideline "
              "review this run (comment-level scoring still runs as usual).")

    since = determine_since(state)
    since_iso = since.isoformat()
    comment_floor = months_ago(LOOKBACK_MONTHS)  # strict per-comment age cutoff
    print(f"Scanning observations updated since {since_iso} "
          f"(place_id={PLACE_ID}, project_id={PROJECT_ID})")

    print("Loading local toxicity model (first run downloads it, ~270MB)...", flush=True)
    local_model._get_pipeline()  # force the load now, not on the first comment
    if not local_model.enabled():
        print("WARNING: local toxicity model unavailable -- falling back to "
              "the local wordlist filter only. This will miss a lot more. "
              "See README.", flush=True)

    new_flags = 0
    scanned_comments = 0
    scanned_identifications = 0
    inat_failed = False

    try:
        for batch, total in iter_updated_observations(
            since_iso, place_id=PLACE_ID, project_id=PROJECT_ID, request_delay=REQUEST_DELAY
        ):
            for obs in batch:
                # Queue the observation's FULL current thread (all comments +
                # all identifications, not just what's new) for guideline
                # review, regardless of what the per-comment pass below finds
                # -- spam/harassment patterns often only show up across the
                # whole thread. Only bother building/storing this if the LLM
                # review is actually configured.
                if llm_client.enabled:
                    review_queue.enqueue(llm_queue, obs)

                for comment in obs.get("comments", []) or []:
                    cid = str(comment.get("id"))
                    if not cid or cid == "None":
                        continue
                    if cid in seen:
                        continue  # already scored in a previous run

                    created_at = comment.get("created_at", "")
                    created_dt = parse_iso(created_at)
                    if created_dt is None:
                        # Can't confirm this comment's age -- don't default
                        # to scanning it. Deliberately NOT marked "seen"
                        # either, so a later run can retry it.
                        print(
                            f"  WARNING: comment {cid} on obs {obs.get('id')} "
                            f"has an unparseable created_at ({created_at!r}); "
                            f"skipping.", flush=True,
                        )
                        continue
                    if created_dt < comment_floor:
                        seen[cid] = created_at  # don't keep re-checking it
                        continue  # comment itself predates the lookback window

                    scanned_comments += 1
                    seen[cid] = comment.get("created_at", "")

                    reasons, max_score = score_comment(comment.get("body", "") or "")
                    if reasons:
                        items_by_id[cid] = make_backlog_item(obs, comment, reasons, max_score)
                        new_flags += 1
                        # Print this hit the moment it's found, not batched up
                        # for later -- makes the Actions log a live feed.
                        snippet = (comment.get("body", "") or "").replace("\n", " ").strip()
                        if len(snippet) > 120:
                            snippet = snippet[:117] + "..."
                        commenter = (comment.get("user") or {}).get("login", "?")
                        print(
                            f"  [FLAG] comment {cid} by {commenter} on obs "
                            f"{obs.get('id')} -- {', '.join(reasons)} -- \"{snippet}\"",
                            flush=True,
                        )

                # Identifications carry their own optional note/remarks
                # field (what a user types into the "add an identification"
                # box), which is free text exactly like a comment body --
                # and it's exactly where a "Gerald"-style off-topic
                # takeover or a bad-faith user can hide a message while
                # looking, at a glance, like a real identification. Without
                # this, that text was only ever seen by the *optional*,
                # budget-limited LLM pass (llm_review.py) -- it never got
                # the same fast/free/always-on scoring every comment gets.
                # Scored the same way, with its own `ident-<id>`-prefixed
                # seen-cache/backlog keys so it can never collide with a
                # same-numbered comment id (see make_backlog_item).
                for ident in obs.get("identifications", []) or []:
                    raw_iid = ident.get("id")
                    iid = str(raw_iid)
                    if not iid or iid == "None":
                        continue
                    seen_key = f"ident-{iid}"
                    if seen_key in seen:
                        continue  # already scored in a previous run

                    created_at = ident.get("created_at", "")
                    created_dt = parse_iso(created_at)
                    if created_dt is None:
                        print(
                            f"  WARNING: identification {iid} on obs "
                            f"{obs.get('id')} has an unparseable "
                            f"created_at ({created_at!r}); skipping.",
                            flush=True,
                        )
                        continue
                    if created_dt < comment_floor:
                        seen[seen_key] = created_at
                        continue  # predates the lookback window

                    scanned_identifications += 1
                    seen[seen_key] = created_at

                    note = (ident.get("body") or "").strip()
                    if not note:
                        # The overwhelming majority of identifications have
                        # no note at all -- skip the model call entirely
                        # rather than burning CPU scoring empty strings.
                        continue

                    reasons, max_score = score_comment(note)
                    if reasons:
                        composite_id = f"ident-{iid}"
                        items_by_id[composite_id] = make_backlog_item(
                            obs, ident, reasons, max_score, kind="identification"
                        )
                        new_flags += 1
                        snippet = note.replace("\n", " ").strip()
                        if len(snippet) > 120:
                            snippet = snippet[:117] + "..."
                        identifier = (ident.get("user") or {}).get("login", "?")
                        taxon = (ident.get("taxon") or {}).get("name") or "?"
                        print(
                            f"  [FLAG] identification {iid} by {identifier} "
                            f"(as {taxon}) on obs {obs.get('id')} -- "
                            f"{', '.join(reasons)} -- \"{snippet}\"",
                            flush=True,
                        )

            print(f"  ...{scanned_comments} new comment(s) and "
                  f"{scanned_identifications} new identification(s) scanned "
                  f"so far, {new_flags} flagged", flush=True)
            # Checkpoint after every batch, so a crash/timeout mid-run doesn't
            # lose progress -- comments already scored won't be re-scored.
            save_json(SEEN_PATH, seen)
            save_json(BACKLOG_PATH, {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "lookback_months": LOOKBACK_MONTHS,
                "items": list(items_by_id.values()),
            })
            if llm_client.enabled:
                review_queue.save_queue(REVIEW_QUEUE_PATH, llm_queue)
    except (requests.exceptions.RequestException,) as e:
        inat_failed = True
        print(f"\niNat API request failed: {e}")
        print("Stopping this run early. Progress made so far is saved and "
              "won't be re-processed next run.")

    if inat_failed:
        control["consecutive_run_failures"] = control.get("consecutive_run_failures", 0) + 1
        print(f"Consecutive failed run count: {control['consecutive_run_failures']} "
              f"(auto-pause threshold: {RUN_FAILURE_THRESHOLD})")
        if control["consecutive_run_failures"] >= RUN_FAILURE_THRESHOLD:
            pause_scanning(
                control,
                f"Auto-paused after {RUN_FAILURE_THRESHOLD} consecutive runs "
                f"failed to reach the iNaturalist API -- it may be down or "
                f"rate-limiting this scanner.",
            )
        else:
            save_json(CONTROL_PATH, control)
        # Deliberately do NOT advance state.json's last_run_completed_at --
        # next run should retry the same window rather than skip past it.
        return

    # A fully successful fetch phase resets the run-failure counter.
    if control.get("consecutive_run_failures"):
        control["consecutive_run_failures"] = 0
        save_json(CONTROL_PATH, control)

    # --- Re-check every still-pending item to see if it's been resolved ---
    # (comment deleted/edited by the author, or removed by a curator) since
    # we flagged it. This is what lets the backlog clear itself without any
    # manual bookkeeping.
    pending = [it for it in items_by_id.values() if it["status"] == "pending"]
    print(f"Re-checking {len(pending)} pending item(s) for auto-resolution...")

    recheck_failures = 0
    for item in pending:
        cid = str(item["comment_id"])
        if cid in manual_resolved:
            item["status"] = "resolved"
            item["resolved_at"] = datetime.now(timezone.utc).isoformat()
            item["resolved_reason"] = "dismissed by moderator"
            continue

        if recheck_failures >= RUN_FAILURE_THRESHOLD:
            # The re-check API calls themselves look like they're failing
            # repeatedly -- stop hammering it and pick this back up next run.
            break

        try:
            obs = fetch_observation(item["observation_id"])
        except requests.exceptions.RequestException:
            recheck_failures += 1
            continue

        if item.get("kind") == "llm_thread":
            # A whole-thread flag isn't tied to one comment id, so we can't
            # check "is this exact comment still there". Re-running the LLM
            # on every pending item every run would also burn through the
            # daily budget fast. Cheap, safe middle ground: only auto-resolve
            # if the observation itself is gone; otherwise it stays pending
            # for a human to clear (or add to manual_resolved.json).
            if obs is None:
                item["status"] = "resolved"
                item["resolved_at"] = datetime.now(timezone.utc).isoformat()
                item["resolved_reason"] = "observation no longer exists"
            continue

        still_present = False
        if obs:
            if item.get("kind") == "identification":
                # cid is the composite "ident-<id>" key -- match against
                # the raw numeric identification id.
                raw_iid = cid[len("ident-"):] if cid.startswith("ident-") else cid
                for i in obs.get("identifications", []) or []:
                    if str(i.get("id")) == raw_iid:
                        still_present = True
                        break
            else:
                for c in obs.get("comments", []) or []:
                    if str(c.get("id")) == cid:
                        still_present = True
                        break

        if not still_present:
            item["status"] = "resolved"
            item["resolved_at"] = datetime.now(timezone.utc).isoformat()
            noun = "identification" if item.get("kind") == "identification" else "comment"
            item["resolved_reason"] = (
                f"{noun} no longer present (removed, edited, or observation deleted)"
                if obs is not None else "observation no longer exists"
            )

    # --- Drain the guideline-review queue against today's remaining
    # OpenRouter free-tier budget. Whatever doesn't fit stays queued for
    # the next run -- see review_queue.py / rate_budget.py.
    llm_flags = 0
    llm_reviewed = 0
    if llm_client.enabled:
        budget = DailyBudget(LLM_BUDGET_PATH)
        to_review = review_queue.pop_oldest(llm_queue, budget.remaining)
        print(f"Draining guideline review queue: {len(to_review)} of "
              f"{len(to_review) + len(llm_queue)} queued item(s) fit in "
              f"today's remaining budget ({budget.remaining}/{budget.daily_budget}).")
        for idx, snapshot in enumerate(to_review):
            if llm_client.circuit_open:
                # OpenRouter looks like it's struggling this run -- stop
                # calling it and put everything we haven't gotten to yet
                # (this one and the rest of the batch) back in the queue
                # for next time, instead of silently losing them.
                for remaining in to_review[idx:]:
                    llm_queue[str(remaining["observation_id"])] = remaining
                break

            verdict = llm_review.review_thread(llm_client, snapshot)
            if verdict is None:
                # Call failed/unusable -- put it back for next run rather
                # than silently dropping it or counting it against budget.
                llm_queue[str(snapshot["observation_id"])] = snapshot
                continue

            budget.record_call()
            llm_reviewed += 1
            if verdict["flag"]:
                cid = f"llm-obs-{snapshot['observation_id']}"
                items_by_id[cid] = make_backlog_item_from_llm(snapshot, verdict)
                llm_flags += 1
                print(f"  [LLM FLAG] obs {snapshot['observation_id']} -- "
                      f"{', '.join(verdict['categories'])} -- {verdict['reasoning']}",
                      flush=True)

        review_queue.save_queue(REVIEW_QUEUE_PATH, llm_queue)

    save_json(BACKLOG_PATH, {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_months": LOOKBACK_MONTHS,
        "items": list(items_by_id.values()),
    })

    # Prune the "seen" cache of anything older than the lookback window so
    # it doesn't grow forever.
    cutoff = months_ago(LOOKBACK_MONTHS).isoformat()
    seen = {cid: ts for cid, ts in seen.items() if not ts or ts >= cutoff}
    save_json(SEEN_PATH, seen)

    save_json(STATE_PATH, {"last_run_completed_at": datetime.now(timezone.utc).isoformat()})

    print(f"Done. Scanned {scanned_comments} new comment(s) and "
          f"{scanned_identifications} new identification(s), {new_flags} newly "
          f"flagged by comment/identification-level scoring. "
          + (f"Guideline LLM reviewed {llm_reviewed} thread(s), {llm_flags} newly flagged, "
             f"{len(llm_queue)} still queued for a future run. "
             if llm_client.enabled else "")
          + f"Backlog now has {len(items_by_id)} total item(s).")


if __name__ == "__main__":
    run()