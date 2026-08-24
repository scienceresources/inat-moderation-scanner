# iNat Comment Watch

An open-source scanner for iNaturalist curators and moderators. It scans
recent comment activity across iNaturalist, flags likely profanity,
insults, slurs, and toxic comments for human review, and publishes a
backlog to a simple web dashboard. It never takes action on its own —
it's a triage aid, not an auto-moderator.

**Live pieces:**
- `scanner/` — a Python job that scans iNat and writes results to `docs/data/backlog.json`
- `.github/workflows/scan.yml` — runs the job on a schedule via GitHub Actions and commits the results
- `docs/` — a static dashboard (GitHub Pages) that reads that JSON directly, no backend required

## How it works

1. **Scanning.** The job asks the iNaturalist API for observations updated
   since the last run (`updated_since`), pulls their comments, and scores
   any comment it hasn't seen before.
2. **Scoring.** Each new comment is sent to [Perspective
   API](https://perspectiveapi.com/) (Google's free toxicity classifier) for
   TOXICITY / SEVERE_TOXICITY / INSULT / PROFANITY / IDENTITY_ATTACK /
   THREAT scores. If Perspective isn't configured or fails on a comment
   (e.g. an unsupported language), it falls back to an offline keyword
   check using the maintained [`better-profanity`](https://pypi.org/project/better-profanity/)
   wordlist. Anything over threshold gets added to the backlog as `pending`.
3. **Auto-resolving.** Every run, every still-`pending` item's observation
   is re-checked. If the flagged comment is no longer there (deleted,
   edited, or the observation itself removed), it's automatically marked
   `resolved` — no manual bookkeeping needed for the common case of "a
   curator already handled it on iNat itself."
4. **Manual dismissal.** For false positives you want to clear without
   touching the comment on iNat, add the comment's numeric ID to
   `docs/data/manual_resolved.json` (a plain JSON array) and it'll be
   marked resolved on the next run.
5. **Dashboard.** `docs/index.html` fetches `docs/data/backlog.json`
   directly and renders Pending / Resolved lists with links straight to
   each observation. No database, no server.

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

If the iNat API or Perspective API is struggling, there are three layers of
protection, from instant/blunt to graceful/self-protecting:

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
3. **Automatic circuit breaker.** The scanner protects itself without any
   human intervention:
   - If Perspective API calls start failing repeatedly *within* a run
     (timeouts, exhausted retries on 429/5xx), it stops calling Perspective
     for the rest of that run and falls back to the local wordlist filter —
     it doesn't just keep hammering a struggling API.
   - If the iNat API itself fails, the run stops immediately and cleanly
     (progress already made is saved). After `RUN_FAILURE_THRESHOLD`
     consecutive failed runs (default 3), it auto-pauses itself via
     `docs/data/control.json` with `"paused_by": "auto"` and a note
     explaining why — so it doesn't keep retrying against a down API every
     6 hours forever, unattended. A human needs to flip it back on once
     things have recovered.

## Setup

1. **Fork this repo** (or use it as a template).
2. **Get a free Perspective API key**: follow [Perspective's quickstart
   guide](https://developers.perspectiveapi.com/s/docs-get-started), then
   add it as a repo secret: **Settings → Secrets and variables → Actions →
   New repository secret** named `PERSPECTIVE_API_KEY`.
   - Without this, the scanner still runs but only uses the much weaker
     offline keyword filter — you'll want the real key.
3. **(Optional) narrow the scope.** Global iNat comment volume is large,
   and the Perspective free tier defaults to roughly 1 request/second. Set
   these as repo **variables** (Settings → Secrets and variables → Actions
   → Variables) if you only care about a specific place or project:
   - `INAT_PLACE_ID` — an iNat place ID
   - `INAT_PROJECT_ID` — an iNat project ID
   - `PERSPECTIVE_QPS` — raise this if you've requested a Perspective quota increase
   - `LOOKBACK_MONTHS` — defaults to 4
4. **Enable GitHub Pages**: Settings → Pages → Source: "Deploy from a
   branch" → branch `main`, folder `/docs`. Your dashboard will be live at
   `https://<you>.github.io/<repo>/`.
5. **Enable Actions** if you forked this (Actions tab → "I understand my
   workflows, go ahead and enable them"). The workflow runs every 6 hours
   by default (`cron: "0 */6 * * *"` in `.github/workflows/scan.yml`) and
   can also be triggered manually from the Actions tab.
6. The first run will backfill the full lookback window, which can take a
   while depending on scope and Perspective quota — this is normal.

## Running locally

```bash
pip install -r requirements.txt
export PERSPECTIVE_API_KEY=your-key-here
# optional: export INAT_PLACE_ID=..., INAT_PROJECT_ID=..., etc.
python scanner/scan.py
```

Then open `docs/index.html` with a local static server (e.g. `python -m
http.server` from the `docs/` folder) — opening the HTML file directly via
`file://` will block the `fetch()` call in most browsers.

## Limitations, worth knowing before you rely on this

- **This is a heuristic triage tool, not a moderation decision-maker.**
  Toxicity classifiers produce false positives and false negatives, across
  languages and dialects unevenly. Always look at the actual comment and
  observation before acting.
- **Global scope is genuinely large.** If you're not narrowing by place or
  project, expect meaningful runtime and Perspective quota usage. Start
  narrow and widen once you've seen how it performs.
- **`updated_since` is an iNat API behavior**, not a guaranteed contract —
  if a future API change breaks it, the scanner will need a small update
  to `scanner/inat_api.py`. Check https://api.inaturalist.org/v1/docs/ if
  scans stop finding anything new.
- Flagged comment text is stored in `docs/data/backlog.json` (public, since
  the repo and Pages site are public) so moderators have context without
  clicking through. If an author deletes their account or the comment, the
  snippet remains in the resolved history as an audit trail — prune it
  manually if that's a concern for your project.

## License

MIT — see `LICENSE`.