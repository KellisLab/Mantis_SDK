# Repo Radar — Mantis mapping Mantis

A standing intelligence officer for the Mantis project itself. One headless, scriptable run
that exercises most of the platform end-to-end — and does things the Mantis UI structurally
cannot.

## What it does (4 phases, each degrades gracefully)

1. **Build a portfolio of maps** from the project's *own* data — open PRs + issues across
   `KellisLab/MantisAPI` and `KellisLab/Mantis` (GitHub REST), a contributor rollup from local
   git history, and the team's meeting-notes Google Doc. Each becomes a Mantis space via
   `client.spaces.create(...)`.
2. **Notebook delta analysis** — for each map, run Python *in the map's kernel* (`points` in
   scope) to compute velocity/breakdowns, render a contributor chart, and `checkpoint()` so the
   next run diffs week-over-week.
3. **Agent synthesis** — a provider-scoped agent (`client.agents`, opencode default /
   claude_code opt-in) reads the richest map and writes the briefing's analysis. Set
   `REPO_RADAR_ALL_SPACES=1` to try the cross-map composer mode.
4. **Briefing** — assemble `repo_radar_brief.md` (portfolio + deltas + synthesis + chart).

## Why this can't be done in the Mantis UI
- It spans **four maps at once** (the UI is single-space).
- It **diffs against last week** via kernel checkpoints (no UI for that).
- It's **headless + scriptable + schedulable** (no batch/automation UI) — drop it in cron for a
  weekly briefing.

## Run it

```bash
export GITHUB_TOKEN=...                      # to pull the private repos
export MANTIS_BACKEND_HOST=http://localhost:8000
export MANTIS_COOKIE='next-auth.session-token=...'   # or MANTIS_COOKIE_FILE=/path
export MANTIS_USER_EMAIL=you@example.com     # the agent runtime keys on email
export MANTISAPI_PATH=/path/to/MantisAPI     # local clone, for git author rollup
export MANTIS_PROVIDER=claude_code           # or opencode (default)
# optional: REPO_RADAR_CAP=20 (bounded run), REPO_RADAR_ALL_SPACES=1, REPO_RADAR_STATE=...

python examples/repo_radar/repo_radar.py
```

By default it talks **straight to the backend** (`base_url=""`); set `MANTIS_BASE_URL=/api/proxy/`
to route through the Next.js proxy instead.

## Requirements / honest caveats
- **Map creation** needs the synthesis celery worker; **notebooks** need the kernel docker stack;
  **agents** need the runtime + a real model credential (Bedrock for `claude_code`, OpenAI for
  `opencode`). Phases are independent — if one stack is down, the others still produce output and
  the failure is reported in the brief, not fatal.
- `sources.py` is plain ingestion (no SDK import) and can be run standalone to sanity-check the
  pullers: `python examples/repo_radar/sources.py`.
