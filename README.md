# iNat Comment Watch

An open-source scanner for iNaturalist curators and moderators. It scans
recent comment activity across iNaturalist, flags likely profanity,
insults, slurs, and toxic comments for human review, and — optionally —
sends each touched observation's full comment + identification thread to
an LLM for a guideline-grounded review that can catch spam patterns and
guideline violations a single isolated comment can't show (see
"Guideline-based full-thread review" below). It publishes a backlog to a
simple web dashboard. It never takes action on its own — it's a triage
aid, not an auto-moderator.

**Live pieces:**
- `scanner/` — a Python job that scans iNat and writes results to `docs/data/backlog.json`
- `.github/workflows/scan.yml` — runs the job on a schedule via GitHub Actions and commits the results
- `docs/` — a static dashboard (GitHub Pages) that reads that JSON directly, no backend required

## How it works

1. **Scanning.** The job asks the iNaturalist API for observations updated
   since the last run (`updated_since`), pulls their comments *and*
   identifications, and scores any comment or identification it hasn't
   seen before.
2. **Scoring.** Each new comment, **and each new identification's own
   optional note/remarks field** (the free-text box next to the taxon
   picker when you add an ID), is scored by a local toxicity classifier
   ([`martin-ha/toxic-comment-model`](https://huggingface.co/martin-ha/toxic-comment-model),
   a small DistilBERT model) that runs entirely inside the GitHub Actions
   job — no API key, no account, no billing, no third-party service. Unlike
   a wordlist, it scores based on the meaning/tone of a comment, so it can
   catch context-dependent harassment and insults that don't contain any
   profanity or slurs at all. If the model can't be loaded (e.g. no network
   access on a fresh runner) or fails on a specific comment/identification,
   it falls back to an offline keyword check using the maintained
   [`better-profanity`](https://pypi.org/project/better-profanity/)
   wordlist. Anything over threshold gets added to the backlog as `pending`.
   Identifications with no note are skipped (nothing to score) rather than
   burning CPU on empty strings — most identifications have none.
   **Why identifications matter, not just comments:** this is the same box
   a bad-faith user can type an off-topic message, a joke, or an insult
   into while it *looks* like a normal identification at a glance — e.g.
   part of a "Gerald"-style thread takeover (see `scanner/guidelines.py`)
   hiding in the ID list instead of the comment list. Before this, that
   text was only ever seen by the *optional*, budget-limited LLM pass
   below — it never got the same fast/free/always-on scoring every comment
   gets, so with no `OPENROUTER_API_KEY` set it went completely unscanned.
   **Honest limitation:** no free, fully-automated tool reliably catches
   very dry, understated passive-aggression that carries no hostile
   vocabulary — the model does meaningfully better than a wordlist here,
   but it's still an imperfect classifier, not a guarantee. Every flag is
   "worth a human look," never a verdict.
3. **Auto-resolving.** Every run, every still-`pending` item's observation
   is re-checked. If the flagged comment or identification is no longer
   there (deleted, edited, or the observation itself removed), it's
   automatically marked `resolved` — no manual bookkeeping needed for the
   common case of "a curator already handled it on iNat itself."
4. **Manual dismissal.** For false positives you want to clear without
   touching anything on iNat, add the item's ID (shown on the dashboard) to
   `docs/data/manual_resolved.json` (a plain JSON array) and it'll be
   marked resolved on the next run. The ID format depends on what was
   flagged: a comment uses its plain numeric ID (unchanged, for backward
   compatibility with existing `manual_resolved.json` files); an
   identification uses `ident-<id>`; a whole-thread guideline flag uses
   `llm-obs-<id>`.
5. **Dashboard.** `docs/index.html` fetches `docs/data/backlog.json`
   directly and renders Pending / Resolved lists with links straight to
   each observation. No database, no server.

## Guideline-based full-thread review (optional)

The per-comment classifier above is fast and free, but it only ever sees
one comment at a time, out of context. It can't tell "someone posted the
same promotional link on 40 unrelated observations" from "someone posted
one promotional link once" -- and it can't apply iNat's actual community
guidelines, only a generic toxicity score.

If you set an `OPENROUTER_API_KEY`, the scanner adds a second, independent
pass: every observation touched during a run gets its **entire** current
comment + identification thread queued up and sent to an LLM (via
[OpenRouter](https://openrouter.ai)'s free tier, `openai/gpt-oss-120b:free`
by default) along with a summary of iNat's actual
[Community Guidelines](https://www.inaturalist.org/pages/community+guidelines)
(`scanner/guidelines.py`), and asked to flag only things that would
genuinely be worth a curator's attention under those guidelines -- spam
patterns, harassment, bad-faith/malicious IDs, sockpuppet-looking
behavior -- while explicitly *not* flagging ordinary ID disagreement,
bluntness, or beginners being wrong. See `scanner/llm_review.py` for the
exact prompt.

**This is entirely optional.** No key set -> this whole pass is skipped
and the scanner behaves exactly as before.

### Why a queue instead of reviewing everything immediately

OpenRouter's free (`:free`) models cost $0 per token, but are rate-limited
by request count, not tokens: **20 requests/minute always**, and a daily
cap of **50 requests/day** on an account that's never purchased credits,
or **1,000 requests/day** once you've bought $10+ in credits at any point
in the account's history (that higher cap is a permanent, one-time
unlock -- it isn't "spending" the $10 on calls, since `:free` models
always cost nothing regardless of account status). Verify current numbers
at OpenRouter's own rate-limit docs before relying on them, since free-tier
terms shift.

That's nowhere near enough to review thousands of observations the moment
they're touched. So instead of an immediate call-per-observation, the
scanner works in two decoupled phases, each checkpointed to disk
(`scanner/review_queue.py`, `scanner/rate_budget.py`):

1. **Fetch** (network-bound, same loop as today): for every observation
   touched this run, snapshot its full current thread into
   `docs/data/review_queue.json` -- cheap, and independent of how fast the
   LLM can be called.
2. **Classify** (rate-limited): drain that queue, oldest-queued-first, up
   to however many calls are left in today's budget
   (`docs/data/llm_budget.json`, resets at UTC midnight). Whatever doesn't
   fit stays in the queue file for the next run -- nothing is dropped, it
   just waits its turn. Once an item is classified it's removed from the
   queue file, so the file only ever holds the current backlog, not a
   growing history.

This also means a slow/rate-limited LLM call can never block or slow down
the iNat fetch loop, and a crash or timeout mid-classify loses at most the
one call in flight, not the whole run's progress.

### Setting expectations for backfill volume

A large first-ever backfill (or an unscoped, whole-site run) can queue up
far more observations than the free tier can review in a day. At the
default 50/day, working through a 7,000-observation backlog would take on
the order of months, not hours. Two ways to make this actually converge:

- **Narrow the scope.** `INAT_PLACE_ID` / `INAT_PROJECT_ID` (see Setup
  below) cut the volume touched per run dramatically -- do this first,
  it's the highest-leverage change.
- **Unlock the 1,000/day tier.** A one-time $10 credit purchase on
  OpenRouter (never spent on `:free` calls -- it just raises the daily
  cap permanently) is ~20x the headroom. Entirely your call; the scanner
  works fine at 50/day, it just churns through a large backlog more
  slowly. Set `LLM_DAILY_BUDGET` to match whichever tier you're on (a
  little under the real cap, to leave slack for timing jitter).

Ongoing, steady-state volume (just new activity between 6-hourly runs, not
a historical backfill) should be far smaller and should keep the queue
from growing unbounded once the initial backlog is drained.

### About the "last four months" window

Re-downloading iNaturalist's *entire* global comment history for the
trailing four months on every run isn't practical — it'd mean re-scoring
huge amounts of unchanged data every few hours and burning through API
quota. Instead:

- The **first ever run** backfills the full lookback window (default 4
  months, set via `LOOKBACK_MONTHS`).
- **Every run after that** is incremental — it only looks at what changed
  since the last completed run — but the *backlog's coverage* stays a
  rolling 4-month window, since old resolved/irrelevant items just age out.

If you want to force a full rescan, delete `docs/data/state.json` and
`docs/data/seen_comments.json` before a run.

## Kill switch

If the iNat API is struggling, there are three layers of protection, from
instant/blunt to graceful/self-protecting:

1. **Instant, zero-cost stop.** Set the repo variable `SCANNER_ENABLED` to
   `false` (Settings → Secrets and variables → Actions → Variables). Every
   future scheduled run is skipped before it even checks out the repo — no
   commit needed, no API calls made. This is the fastest option if
   something's actively overloaded right now. Set it back to `true` (or
   delete it) to resume.
2. **Collaborative, documented pause.** Edit `docs/data/control.json`
   directly on GitHub (any collaborator with write access can do this, no
   Settings access needed) and set `"enabled": false`. Optionally fill in
   `"note"` with why. This is checked first thing inside the scanner, before
   any API calls, and shows as a banner on the dashboard so the whole team
   can see scanning is paused and why. Set `"enabled": true` and commit to
   resume.
3. **Automatic circuit breaker.** If the iNat API itself fails, the run
   stops immediately and cleanly (progress already made is saved). After
   `RUN_FAILURE_THRESHOLD` consecutive failed runs (default 3), it
   auto-pauses itself via `docs/data/control.json` with
   `"paused_by": "auto"` and a note explaining why — so it doesn't keep
   retrying against a down API every 6 hours forever, unattended. A human
   needs to flip it back on once things have recovered. (If the local
   toxicity model itself fails to load, that's not a kill-switch condition
   — the scanner just logs a warning and falls back to the wordlist filter
   for the whole run, since it's not calling any external service that
   could need protecting from being hammered.)

## Setup

1. **Fork this repo** (or use it as a template).
2. **Nothing to sign up for.** Scoring is done by a local model that runs
   inside the GitHub Actions job — no API key, no account, no billing.
3. **(Optional) narrow the scope.** Global iNat comment volume is large,
   and local-model scoring on a CI runner's CPU is slower than a hosted API
   call was. Set these as repo **variables** (Settings → Secrets and
   variables → Actions → Variables) if you only care about a specific place
   or project:
   - `INAT_PLACE_ID` — an iNat place ID
   - `INAT_PROJECT_ID` — an iNat project ID
   - `LOOKBACK_MONTHS` — defaults to 4
   - `TOXIC_THRESHOLD` — defaults to 0.65 (0–1; lower catches more, with more false positives)
4. **(Optional) turn on guideline-based full-thread review.** Sign up free
   at [openrouter.ai](https://openrouter.ai/keys) (email or GitHub, no
   card needed) and generate an API key. Set it as the repo **secret**
   `OPENROUTER_API_KEY` (Settings → Secrets and variables → Actions →
   Secrets). Optional repo **variables** to tune it:
   - `OPENROUTER_MODEL` — defaults to `openai/gpt-oss-120b:free`
   - `LLM_DAILY_BUDGET` — defaults to 45 (a little under OpenRouter's
     50/day no-credits-purchased cap; raise to ~950 if you've unlocked
     the 1,000/day tier -- see "Guideline-based full-thread review" above)
   - `LLM_REQUESTS_PER_MINUTE` — defaults to 15 (a little under
     OpenRouter's fixed 20/minute cap)
   Leave `OPENROUTER_API_KEY` unset to skip this pass entirely.
5. **Enable GitHub Pages**: Settings → Pages → Source: "Deploy from a
   branch" → branch `main`, folder `/docs`. Your dashboard will be live at
   `https://<you>.github.io/<repo>/`.
6. **Enable Actions** if you forked this (Actions tab → "I understand my
   workflows, go ahead and enable them"). The workflow runs every 6 hours
   by default (`cron: "0 */6 * * *"` in `.github/workflows/scan.yml`) and
   can also be triggered manually from the Actions tab.
7. The first run will download the toxicity model (~270MB) and backfill
   the full lookback window, which can take a while depending on scope —
   this is normal. The workflow caches the model afterward so future runs
   don't re-download it. If guideline review is on, that first backfill
   also queues up for LLM review and drains gradually over subsequent
   runs — see "Setting expectations for backfill volume" above.

## Running locally

```bash
pip install -r requirements.txt
# optional: export INAT_PLACE_ID=..., INAT_PROJECT_ID=..., TOXIC_THRESHOLD=..., etc.
# optional: export OPENROUTER_API_KEY=... to also enable guideline review locally
python scanner/scan.py
```

The first local run downloads the toxicity model (~270MB) to `~/.cache/huggingface`
and reuses it after that.

Then open `docs/index.html` with a local static server (e.g. `python -m
http.server` from the `docs/` folder) — opening the HTML file directly via
`file://` will block the `fetch()` call in most browsers.

## Limitations, worth knowing before you rely on this

- **This is a heuristic triage tool, not a moderation decision-maker.**
  Toxicity classifiers produce false positives and false negatives, across
  languages and dialects unevenly. Always look at the actual comment and
  observation before acting.
- **The local model is English-focused** and, per its own model card,
  performs worse on comments that reference specific identity subgroups.
  It's also stronger on comments with clearly hostile language than on
  very dry, understated passive-aggression with no hostile vocabulary at
  all — that's a hard case for any automated classifier, not just this one.
- **Global scope is genuinely large.** If you're not narrowing by place or
  project, expect meaningful CPU runtime scoring every comment locally.
  Start narrow and widen once you've seen how it performs.
- **`updated_since` is an iNat API behavior**, not a guaranteed contract —
  if a future API change breaks it, the scanner will need a small update
  to `scanner/inat_api.py`. Check https://api.inaturalist.org/v1/docs/ if
  scans stop finding anything new.
- Flagged comment text is stored in `docs/data/backlog.json` (public, since
  the repo and Pages site are public) so moderators have context without
  clicking through. If an author deletes their account or the comment, the
  snippet remains in the resolved history as an audit trail — prune it
  manually if that's a concern for your project.
- **Guideline review (if enabled) is an LLM's judgment call, not a rules
  engine.** It's given the real guidelines and told explicitly not to flag
  ordinary disagreement or bluntness, but it can still misjudge tone or
  miss/over-flag edge cases, same as the toxicity classifier. Same rule as
  everywhere else in this tool: every flag is "worth a human look."
- **Guideline review auto-resolves conservatively.** A whole-thread flag
  isn't tied to one comment ID, so the scanner can't cheaply re-check
  "is the exact thing that was flagged still there" the way it does for
  single comments — it only auto-clears if the observation itself gets
  deleted. Otherwise a curator needs to add it to `manual_resolved.json`
  once handled (its `comment_id` is `llm-obs-<observation_id>`, shown on
  the dashboard). If that same thread later gets new activity that also
  looks like a violation, it can be re-flagged with a fresh reasoning
  note even if the earlier flag was dismissed — treat that as "there's
  new activity worth a look," not a bug.

## License

MIT — see `LICENSE`.