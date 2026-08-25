"""
The staging queue between the two scan phases:

  1. FETCH (network-bound): page through the iNat API, and for every
     observation touched since the last run, snapshot its *entire*
     current comment + identification thread into this queue -- not just
     what's new, since the whole point of the LLM review is judging
     spam/guideline patterns in context, which a single new comment or ID
     can't show on its own.
  2. CLASSIFY (LLM-call-bound, budget-limited): drain this queue, oldest
     first, up to however many calls today's OpenRouter budget allows.
     Whatever doesn't get classified today stays queued for the next run
     -- nothing is lost, it just waits its turn.

This is the "download to a local file, classify from the file, then the
file empties back out" structure -- the queue file grows during fetch and
shrinks during classify, rather than a single loop trying to do both a
slow network walk and a rate-limited LLM call per item at once.

If the same observation gets touched again before it's been classified,
its snapshot is refreshed in place (latest thread contents) but its
original `queued_at` is kept, so it doesn't keep getting bumped to the
back of the line by its own new activity.
"""

import os
import json
from datetime import datetime, timezone


def load_queue(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_queue(path, queue):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def snapshot_observation(obs):
    """Build the compact record we actually need for review from a full
    iNat observation payload -- comments and identifications, each
    reduced to what the LLM needs to judge the thread."""
    comments = [
        {
            "id": c.get("id"),
            "user": (c.get("user") or {}).get("login", "?"),
            "created_at": c.get("created_at", ""),
            "body": c.get("body", "") or "",
        }
        for c in (obs.get("comments") or [])
    ]
    identifications = [
        {
            "id": i.get("id"),
            "user": (i.get("user") or {}).get("login", "?"),
            "created_at": i.get("created_at", ""),
            "taxon": (i.get("taxon") or {}).get("name", "?"),
            "body": i.get("body", "") or "",
            "category": i.get("category", ""),  # e.g. "leading"/"improving"/"maverick"
        }
        for i in (obs.get("identifications") or [])
    ]
    return {
        "observation_id": obs.get("id"),
        "observation_uri": obs.get("uri", f"https://www.inaturalist.org/observations/{obs.get('id')}"),
        "species_guess": obs.get("species_guess") or "(no species guess)",
        "observation_owner": (obs.get("user") or {}).get("login", ""),
        "updated_at": obs.get("updated_at", ""),
        "comments": comments,
        "identifications": identifications,
    }


def enqueue(queue, obs):
    """Add or refresh an observation's snapshot in the queue."""
    oid = str(obs.get("id"))
    existing = queue.get(oid)
    snap = snapshot_observation(obs)
    snap["queued_at"] = (existing or {}).get("queued_at") or datetime.now(timezone.utc).isoformat()
    queue[oid] = snap


def pop_oldest(queue, n):
    """Remove and return up to n items, oldest-queued first, WITHOUT
    saving -- caller saves after successfully processing (or explicitly
    re-adds an item it couldn't finish, e.g. on an API failure)."""
    ordered = sorted(queue.items(), key=lambda kv: kv[1].get("queued_at") or "")
    batch = ordered[:n]
    for oid, _ in batch:
        del queue[oid]
    return [item for _, item in batch]
