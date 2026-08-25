"""
Tracks how many OpenRouter free-tier calls we've made today, persisted to
disk so the count survives across the workflow's separate 6-hourly runs
(OpenRouter's own daily cap resets on their clock, not ours, but a
same-UTC-day tracker on our side is a close enough match and errs safely
-- worst case we stop a little early, never late).

Defaults are set a bit under OpenRouter's actual caps (50/day with no
credits ever purchased, 1,000/day after a one-time $10 top-up) so normal
timing jitter doesn't trip their limit and lose the rest of the day's
calls to 429s. See README for the $10 top-up tradeoff -- it's a one-time
unlock, not a per-call cost, since :free models are always $0.
"""

import os
import json
from datetime import datetime, timezone


def _env_or_default(name, default, cast=str):
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return cast(val)


DAILY_BUDGET = _env_or_default("LLM_DAILY_BUDGET", 45, int)


def _today():
    return datetime.now(timezone.utc).date().isoformat()


class DailyBudget:
    def __init__(self, path, daily_budget=None):
        self.path = path
        self.daily_budget = daily_budget if daily_budget is not None else DAILY_BUDGET
        self._state = self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                state = json.load(f)
        else:
            state = {}
        if state.get("date") != _today():
            state = {"date": _today(), "used": 0}
        return state

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)
        os.replace(tmp, self.path)

    @property
    def used(self):
        return self._state["used"]

    @property
    def remaining(self):
        return max(0, self.daily_budget - self._state["used"])

    def record_call(self):
        self._state["used"] += 1
        self.save()
