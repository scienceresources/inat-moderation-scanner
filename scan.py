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

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
BACKLOG_PATH = os.path.join(DATA_DIR, "backlog.json")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
SEEN_PATH = os.path.join(DATA_DIR, "seen_comments.json")
MANUAL_RESOLVED_PATH = os.path.join(DATA_DIR, "manual_resolved.json")
CONTROL_PATH = os.path.join(DATA_DIR, "control.json")

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


def determine_since(state):
    floor = months_ago(LOOKBACK_MONTHS)
    last_run = state.get("last_run_completed_at")
    if not last_run:
        return floor
    last_run_dt = datetime.fromisoformat(last_run)
    since = last_run_dt - timedelta(minutes=OVERLAP_MINUTES)
    return max(since, floor)


def make_backlog_item(obs, comment, reasons, max_score):
    return {
        "comment_id": comment.get("id"),
        "observation_id": obs.get("id"),
        "observation_uri": obs.get("uri", f"https://www.inaturalist.org/observations/{obs.get('id')}"),
        "species_guess": obs.get("species_guess") or "(no species guess)",
        "observation_owner": (obs.get("user") or {}).get("login", ""),
        "commenter": (comment.get("user") or {}).get("login", ""),
        "body_snippet": (comment.get("body") or "")[:SNIPPET_MAX_CHARS],
        "comment_created_at": comment.get("created_at", ""),
        "flagged_at": datetime.now(timezone.utc).isoformat(),
        "reasons": reasons,
        "max_score": max_score,
        "status": "pending",
        "resolved_at": None,
        "resolved_reason": None,
    }


def score_comment(text):
    """Returns (reasons, max_score). Tries the local toxicity model first
    (unless it failed to load earlier this run), falls back to the local
    wordlist filter if the model is unavailable or fails on this
    particular comment."""
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

    items_by_id = {str(item["comment_id"]): item for item in backlog["items"]}

    since = determine_since(state)
    since_iso = since.isoformat()
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
    inat_failed = False

    try:
        for batch, total in iter_updated_observations(
            since_iso, place_id=PLACE_ID, project_id=PROJECT_ID, request_delay=REQUEST_DELAY
        ):
            for obs in batch:
                for comment in obs.get("comments", []) or []:
                    cid = str(comment.get("id"))
                    if not cid or cid == "None":
                        continue
                    if cid in seen:
                        continue  # already scored in a previous run

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

            print(f"  ...{scanned_comments} new comment(s) scanned so far, {new_flags} flagged", flush=True)
            # Checkpoint after every batch, so a crash/timeout mid-run doesn't
            # lose progress -- comments already scored won't be re-scored.
            save_json(SEEN_PATH, seen)
            save_json(BACKLOG_PATH, {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "lookback_months": LOOKBACK_MONTHS,
                "items": list(items_by_id.values()),
            })
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

        still_present = False
        if obs:
            for c in obs.get("comments", []) or []:
                if str(c.get("id")) == cid:
                    still_present = True
                    break

        if not still_present:
            item["status"] = "resolved"
            item["resolved_at"] = datetime.now(timezone.utc).isoformat()
            item["resolved_reason"] = (
                "comment no longer present (removed, edited, or observation deleted)"
                if obs is not None else "observation no longer exists"
            )

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

    print(f"Done. Scanned {scanned_comments} new comment(s), {new_flags} newly flagged. "
          f"Backlog now has {len(items_by_id)} total item(s).")


if __name__ == "__main__":
    run()