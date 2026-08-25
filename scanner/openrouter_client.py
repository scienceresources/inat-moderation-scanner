"""
Thin client for OpenRouter's chat completions endpoint
(https://openrouter.ai/api/v1/chat/completions), used to run the
guideline-grounded thread review in llm_review.py.

Why OpenRouter's free tier specifically: it's genuinely $0 per token on
any model ID ending in ":free" -- no card required, ever. The catch is
request-based rate limits, not token limits: 20 requests/minute always,
plus a daily cap of 50 requests/day on an account with no credits ever
purchased, or 1,000/day once you've bought $10+ in credits at any point
(that higher cap sticks permanently, even if the balance drops back to
$0 -- the $10 is a one-time unlock, not something spent per call, since
:free models never cost anything regardless). See README for how this
shapes the review queue's pacing.

Default model is openai/gpt-oss-120b:free -- OpenAI's open-weight
120B-parameter MoE model, free on OpenRouter, strong enough at
instruction-following/structured-JSON-output for this kind of
guideline-application task. Override with OPENROUTER_MODEL if OpenRouter
rotates it out of the free tier (they do this to free models sometimes
with little notice -- check https://openrouter.ai/models?max_price=0 if
this one starts erroring).
"""

import os
import time
import json
import requests

API_URL = "https://openrouter.ai/api/v1/chat/completions"


def _env_or_default(name, default, cast=str):
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return cast(val)


MODEL = _env_or_default("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")
# Kept a little under OpenRouter's hard 20/minute cap so we don't trip it
# on timing jitter.
MIN_INTERVAL_SECONDS = 60.0 / _env_or_default("LLM_REQUESTS_PER_MINUTE", 15, float)


class OpenRouterClient:
    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.model = model or MODEL
        self._last_call = 0.0
        # Like perspective.py's circuit breaker: consecutive overload-like
        # failures (timeouts, exhausted 429/5xx retries) *this run*, not
        # content issues -- stop hammering a struggling API for the rest
        # of this run rather than retrying forever.
        self.consecutive_failures = 0
        self.failure_threshold = int(os.environ.get("LLM_FAILURE_THRESHOLD", "5"))

    @property
    def enabled(self):
        return bool(self.api_key)

    @property
    def circuit_open(self):
        return self.consecutive_failures >= self.failure_threshold

    def _throttle(self):
        elapsed = time.time() - self._last_call
        if elapsed < MIN_INTERVAL_SECONDS:
            time.sleep(MIN_INTERVAL_SECONDS - elapsed)

    def chat_json(self, system_prompt, user_prompt, max_retries=3, max_tokens=500):
        """
        Sends a chat completion request asking for a strict JSON object
        back. Returns the parsed dict on success, or None if the call
        failed, was rate-limited past its retries, or didn't return valid
        JSON -- callers should treat None as "skip this item, try again
        next run" rather than "not flagged".
        """
        if not self.enabled or self.circuit_open:
            return None

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # Optional but polite/recommended by OpenRouter for free-tier
            # traffic attribution -- doesn't affect quota.
            "HTTP-Referer": "https://github.com/",
            "X-Title": "iNat Comment Watch (guideline review)",
        }

        overload_like = False

        for attempt in range(max_retries):
            self._throttle()
            self._last_call = time.time()
            try:
                resp = requests.post(API_URL, headers=headers, json=body, timeout=60)
            except requests.exceptions.RequestException as e:
                overload_like = True
                print(f"    WARNING: OpenRouter request failed ({e}); retrying...")
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 200:
                self.consecutive_failures = 0
                try:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    return _parse_json_object(content)
                except (KeyError, IndexError, ValueError) as e:
                    print(f"    WARNING: couldn't parse OpenRouter response ({e}); skipping.")
                    return None

            if resp.status_code == 429:
                # Could be the 20/min cap (transient -- back off and
                # retry) or the 50-or-1000/day cap (won't recover this
                # run). Either way, back off; the daily-budget tracker in
                # rate_budget.py is what actually stops us from calling
                # again once the day's quota is spent.
                overload_like = True
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else 2 ** (attempt + 2)
                print(f"    OpenRouter rate-limited (429); waiting {wait:.0f}s...")
                time.sleep(wait)
                continue

            if 400 <= resp.status_code < 500:
                # Bad request / model unavailable / etc -- not an overload,
                # not worth retrying this exact call.
                print(f"    WARNING: OpenRouter returned {resp.status_code}: {resp.text[:300]}")
                return None

            # 5xx -- overload-like.
            overload_like = True
            time.sleep(1.5 ** attempt)

        if overload_like:
            self.consecutive_failures += 1
        return None


def _parse_json_object(content):
    """Models occasionally wrap JSON in ```json fences despite
    response_format -- strip those before parsing."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)
