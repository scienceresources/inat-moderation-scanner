"""
Wrapper around Google's Perspective API (https://perspectiveapi.com/), a
free, purpose-built toxicity/insult/profanity/identity-attack classifier.
Using this instead of a hand-maintained slur list means the detection
quality and coverage is maintained upstream, not by this repo.

Get a free key at https://developers.perspectiveapi.com/s/docs-get-started
and set it as the PERSPECTIVE_API_KEY environment variable / repo secret.

STATUS (as of mid-2026): Google/Jigsaw is sunsetting Perspective API.
New usage/key requests stopped being accepted in February 2026, and the
service itself shuts down entirely at the end of 2026
(https://developers.perspectiveapi.com/s/docs). If you already have a
key from before the cutoff it'll keep working until then; if not, this
client will just stay disabled (no key -> .enabled is False -> scan.py
falls back to the local model) and there's currently no way to get a new
one. Don't spend time troubleshooting "why can't I sign up" -- that's
expected. Kept in the codebase for existing keyholders and in case a
Perspective-shaped replacement API shows up before this fully shuts off.

Default free-tier quota is modest (around 1 request/second). If your scan
scope is large, request a quota increase from the same dashboard, or narrow
the scan with INAT_PLACE_ID / INAT_PROJECT_ID.
"""

import os
import time
import requests

ANALYZE_URL = "https://commentanalyzer.googleapis.com/v1alpha1/comments:analyze"

# These are Perspective's "production-ready" attributes -- stable enough
# to rely on. IDENTITY_ATTACK is the closest attribute to catching slurs.
ATTRIBUTES = ["TOXICITY", "SEVERE_TOXICITY", "INSULT", "PROFANITY", "IDENTITY_ATTACK", "THREAT"]

# Perspective is more confident about some attributes than others at the
# same score, so identity attacks / threats get a lower bar.
DEFAULT_THRESHOLDS = {
    "TOXICITY": 0.80,
    "SEVERE_TOXICITY": 0.60,
    "INSULT": 0.80,
    "PROFANITY": 0.85,
    "IDENTITY_ATTACK": 0.60,
    "THREAT": 0.60,
}


class PerspectiveClient:
    def __init__(self, api_key=None, qps=1.0, thresholds=None, failure_threshold=None):
        self.api_key = api_key or os.environ.get("PERSPECTIVE_API_KEY")
        self.min_interval = 1.0 / max(qps, 0.1)
        self.thresholds = thresholds or DEFAULT_THRESHOLDS
        self._last_call = 0.0
        # Tracks *this run's* consecutive overload-like failures (timeouts,
        # exhausted 429/5xx retries) -- NOT content issues like an
        # unsupported language, which aren't a sign the API is struggling.
        self.consecutive_failures = 0
        self.failure_threshold = failure_threshold or int(
            os.environ.get("PERSPECTIVE_FAILURE_THRESHOLD", "5")
        )

    @property
    def enabled(self):
        return bool(self.api_key)

    @property
    def circuit_open(self):
        """True once this run has seen enough consecutive overload-like
        failures that we should stop hammering Perspective and fall back
        to the local filter for the rest of the run."""
        return self.consecutive_failures >= self.failure_threshold

    def _throttle(self):
        elapsed = time.time() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    def score(self, text, max_retries=3):
        """
        Returns a dict of {attribute: score} on success, or None if scoring
        failed (missing key, unsupported language, persistent error) --
        callers should fall back to the local keyword filter in that case
        rather than treating None as "clean".
        """
        if not self.enabled or not text.strip():
            return None

        if self.circuit_open:
            # Already decided this run that Perspective looks overloaded --
            # don't add more load to it, just fail fast.
            return None

        body = {
            "comment": {"text": text[:3000]},  # Perspective has a size limit
            "requestedAttributes": {attr: {} for attr in ATTRIBUTES},
            "doNotStore": True,
        }

        overload_like = False  # set True if every retry failed for an overload reason

        for attempt in range(max_retries):
            self._throttle()
            self._last_call = time.time()
            try:
                resp = requests.post(
                    ANALYZE_URL, params={"key": self.api_key}, json=body, timeout=15
                )
            except requests.exceptions.RequestException:
                overload_like = True
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 200:
                data = resp.json()
                scores = {}
                for attr, val in data.get("attributeScores", {}).items():
                    scores[attr] = val["summaryScore"]["value"]
                self.consecutive_failures = 0
                return scores

            if resp.status_code == 429:
                overload_like = True
                time.sleep(2 ** (attempt + 1))
                continue

            if resp.status_code == 400:
                # Usually an unsupported language for one of the requested
                # attributes -- a content issue, not a sign of overload.
                # Not worth retrying, and doesn't count against the circuit
                # breaker.
                return None

            # Other server-side error (5xx etc): treat as overload-like.
            overload_like = True
            time.sleep(1.5 ** attempt)

        if overload_like:
            self.consecutive_failures += 1
        return None

    def flagged_reasons(self, scores):
        """Given a scores dict, return a list of 'ATTR=0.87' strings for
        every attribute over its threshold, or [] if none crossed."""
        if not scores:
            return []
        reasons = []
        for attr, val in scores.items():
            threshold = self.thresholds.get(attr)
            if threshold is not None and val >= threshold:
                reasons.append(f"{attr}={val:.2f}")
        return reasons