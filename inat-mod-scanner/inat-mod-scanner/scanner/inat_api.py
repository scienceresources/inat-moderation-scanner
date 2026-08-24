"""
Thin wrapper around the public iNaturalist API (no auth needed for reads).

Two things this needs to do that a simple per-taxon crawl doesn't:
  1. Find observations that have changed RECENTLY (new comment, edit, etc)
     rather than crawling a whole taxon, so incremental runs stay cheap.
  2. Re-check a specific observation on demand, so we can tell whether a
     previously-flagged comment has since been deleted/edited (our
     auto-resolve signal).

NOTE: `updated_since` is a documented iNat search param used by their own
mobile app for sync, but iNat's API has changed shape before. If this
stops returning what you expect, check https://api.inaturalist.org/v1/docs/
for the current parameter name before assuming the scanner is broken.
"""

import time
import requests

BASE_URL = "https://api.inaturalist.org/v1/observations"
OBS_FIELDS = "id,uri,species_guess,observed_on,updated_at,user,comments"


def iter_updated_observations(since_iso, place_id=None, project_id=None,
                               per_page=200, request_delay=1.0, session=None):
    """
    Yields (batch, total_results) for every observation updated on/after
    `since_iso` (an ISO 8601 timestamp string), oldest-id-first.

    Uses id-based pagination (id_above) rather than page-number pagination,
    since the latter hard-caps at 10,000 results no matter how the results
    are filtered.
    """
    sess = session or requests
    id_above = 0

    while True:
        params = {
            "updated_since": since_iso,
            "per_page": per_page,
            "order_by": "id",
            "order": "asc",
            "id_above": id_above,
            "fields": OBS_FIELDS,
        }
        if place_id:
            params["place_id"] = place_id
        if project_id:
            params["project_id"] = project_id

        resp = sess.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            return

        yield results, data.get("total_results", 0)

        id_above = results[-1]["id"]
        if len(results) < per_page:
            return

        time.sleep(request_delay)


def fetch_observation(obs_id, session=None):
    """Re-fetch a single observation by id (used to check if a flagged
    comment is still present). Returns None if the observation is gone
    entirely (e.g. deleted)."""
    sess = session or requests
    resp = sess.get(f"{BASE_URL}/{obs_id}", params={"fields": OBS_FIELDS}, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0] if results else None
