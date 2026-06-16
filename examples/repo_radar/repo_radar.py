"""Repo Radar — Mantis mapping Mantis.

A standing intelligence officer for the Mantis project itself: it builds a *portfolio* of maps
from the project's own GitHub (KellisLab/MantisAPI + KellisLab/Mantis) and meeting notes, runs
notebook delta analysis (what's new since last run), then turns a cross-space agent loose to
synthesize "what moved, what's the risk, where do the notes and the code disagree" — and writes
a weekly briefing.

Why this can't be done in the Mantis UI:
  - it spans FOUR maps at once (UI is single-space);
  - it diffs against last week via kernel checkpoints (no UI for that);
  - it's one headless, scheduled, scriptable run (no batch/automation UI).

Phases are independent and degrade gracefully — if the kernel or agent stack is down, the
earlier phases still produce maps + a partial brief, and the failure is reported, not fatal.

Env:
  MANTIS_HOST / MANTIS_BACKEND_HOST   (default localhost:3000 / :8000)
  MANTIS_COOKIE                        session cookie
  MANTIS_USER_EMAIL                    (for the agent runtime)
  GITHUB_TOKEN                         to pull the private repos
  MANTISAPI_PATH / MANTIS_PATH         local clones (for git author rollup)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sources  # noqa: E402

from mantis_sdk import ConfigurationManager, MantisClient, Provider  # noqa: E402
from mantis_sdk.exceptions import MantisError  # noqa: E402

STATE_FILE = Path(os.getenv("REPO_RADAR_STATE", "/tmp/repo_radar_state.json"))


# ----------------------------------------------------------------------------- client
def make_client() -> MantisClient:
    # base_url defaults to "" (talk straight to the backend) — set MANTIS_BASE_URL=/api/proxy/
    # to route through the Next.js proxy instead. host defaults to the backend for the same reason.
    backend = os.getenv("MANTIS_BACKEND_HOST", "http://localhost:8000")
    config = ConfigurationManager().update({
        "host": os.getenv("MANTIS_HOST", backend),
        "backend_host": backend,
        "user_email": os.getenv("MANTIS_USER_EMAIL"),
        "request_timeout": float(os.getenv("MANTIS_REQUEST_TIMEOUT", "60")),
    })
    cookie = os.environ.get("MANTIS_COOKIE")
    if not cookie and (cp := os.getenv("MANTIS_COOKIE_FILE")):
        cookie = Path(cp).read_text().strip()
    return MantisClient(os.getenv("MANTIS_BASE_URL", ""), cookie=cookie, config=config)


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ----------------------------------------------------------------------------- phase 1: maps
def build_maps(client: MantisClient) -> dict[str, str]:
    """create the portfolio of maps from the project's own data. returns {name: space_id}."""
    print("\n=== PHASE 1: building the Mantis-on-Mantis map portfolio ===")
    builders = {
        "prs": lambda: sources.github_prs(),
        "issues": lambda: sources.github_issues(),
        "authors": lambda: sources.github_authors(os.getenv("MANTISAPI_PATH", ".")),
        "notes": lambda: sources.meeting_notes(),
    }
    spaces: dict[str, str] = {}
    for name, build in builders.items():
        try:
            df, data_types = build()
            cap = int(os.getenv("REPO_RADAR_CAP", "0"))  # optional row cap for bounded/demo runs
            if cap:
                df = df.head(cap)
            if df.empty:
                print(f"  [skip] {name}: no rows")
                continue
            print(f"  [create] {name}: {len(df)} points → submitting...")
            handle = client.spaces.create(
                f"Mantis Radar — {name}", df, data_types,
                show_progress=False,
                on_progress=lambda p, m, t, n=name: print(f"    {n}: {p:3d}% {m or ''}", end="\r"),
            )
            spaces[name] = handle.space_id
            print(f"\n  [done] {name}: space {handle.space_id} (map {handle.map_id})")
        except MantisError as exc:
            print(f"  [fail] {name}: {exc}")
    return spaces


# ----------------------------------------------------------- phase 2: notebook delta analysis
DELTA_CODE = """
# delta + velocity analysis over this map's points (runs inside the map's kernel)
import collections, json
pts = maps[0].points
rows = [getattr(p, 'metadata', {}) or {} for p in pts]
n = len(pts)

# velocity by author and state, derived from the point metadata
authors = collections.Counter(r.get('author', 'unknown') for r in rows)
states  = collections.Counter(r.get('state', r.get('topic', 'n/a')) for r in rows)

print("POINTS", n)
print("TOP_AUTHORS", json.dumps(authors.most_common(8)))
print("STATE_BREAKDOWN", json.dumps(dict(states)))
"""

CHART_CODE = """
import collections
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
rows = [getattr(p, 'metadata', {}) or {} for p in maps[0].points]
c = collections.Counter(r.get('author', 'unknown') for r in rows).most_common(10)
labels = [a for a, _ in c]; vals = [v for _, v in c]
plt.figure(figsize=(9, 4))
plt.bar(labels, vals)
plt.xticks(rotation=45, ha='right'); plt.ylabel('contributions'); plt.title('Top contributors')
plt.tight_layout(); plt.show()
"""


def analyze(client: MantisClient, spaces: dict[str, str], state: dict) -> dict:
    """run delta analysis in each map's kernel; checkpoint for week-over-week diffs."""
    print("\n=== PHASE 2: notebook delta analysis (per-map kernel) ===")
    prev = state.get("metrics", {})
    metrics: dict = {}
    chart_png = None
    for name, space_id in spaces.items():
        try:
            maps = client.maps.list(space_id)
            map_id = maps[0]["id"] if maps else space_id
            nb = client.notebooks.from_map(map_id, name=f"radar-{name}",
                                           user_id=os.getenv("MANTIS_USER_EMAIL"))
            cell = nb.add_cell(DELTA_CODE)
            cell.execute(timeout=120)
            text = cell.text
            pts = _grab_int(text, "POINTS")
            delta = pts - prev.get(name, {}).get("points", pts)
            metrics[name] = {"points": pts, "delta_since_last_run": delta, "raw": text[:800]}
            print(f"  [{name}] points={pts}  Δ={delta:+d} since last run")

            # one chart from the authors map for the brief
            if name == "authors" and chart_png is None:
                chart = nb.add_cell(CHART_CODE)
                chart.execute(timeout=120)
                png = chart.image_png_bytes()
                if png:
                    chart_png = "/tmp/repo_radar_contributors.png"
                    Path(chart_png).write_bytes(png)
                    print(f"  [chart] saved {chart_png} ({len(png)} bytes)")

            nb.checkpoint(f"radar-{name}-{int(time.time())}")
        except MantisError as exc:
            print(f"  [fail] {name}: {exc}  (needs the notebook kernel stack)")
    metrics["_chart"] = chart_png
    return metrics


def _grab_int(text: str, key: str) -> int:
    for line in text.splitlines():
        if line.startswith(key):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return 0
    return 0


# ------------------------------------------------------------- phase 3: agent synthesis
# A per-space agent run (the verified provider path). all_spaces composer mode also exists
# (client.agents.session(all_spaces=True, ...)) for true cross-map reasoning, but its replies
# are delivered over a channel-layer group broadcast that doesn't reach a headless socket on
# every deployment — so the robust demo path runs an agent against the richest single map and
# the cross-map synthesis is done from the per-map metrics. Flip USE_ALL_SPACES to try it.
USE_ALL_SPACES = os.getenv("REPO_RADAR_ALL_SPACES") == "1"

SYNTHESIS_PROMPT = (
    "You are Repo Radar, an intelligence officer for the Mantis project. Looking at this map of "
    "the project's pull requests, give a tight briefing: (1) the single biggest theme of work, "
    "citing specific PRs; (2) one concrete risk or gap; (3) one recommended priority for next "
    "week. Be concise and cite items by title."
)


async def synthesize(client: MantisClient, spaces: dict[str, str], provider: Provider) -> str:
    """run an agent to synthesize a briefing. provider-scoped (opencode default, claude_code
    opt-in). degrades to a clear note if the agent runtime/credentials are unavailable."""
    print(f"\n=== PHASE 3: agent synthesis (provider={provider}, all_spaces={USE_ALL_SPACES}) ===")
    email = os.environ["MANTIS_USER_EMAIL"]
    # anchor on the PRs map (richest signal); None space_id when all_spaces.
    space_id = None if USE_ALL_SPACES else spaces.get("prs") or next(iter(spaces.values()))
    try:
        async with client.agents.session(space_id, all_spaces=USE_ALL_SPACES, provider=provider,
                                         user_email=email, timeout=90) as analyst:
            chunks: list[str] = []
            async for ev in analyst.ask(SYNTHESIS_PROMPT):
                if ev.type == "text":
                    chunks.append(ev.text)
                    print(ev.text, end="", flush=True)
                elif ev.type == "tool_use":
                    print(f"\n  [agent → {ev.tool_name}]", flush=True)
                elif ev.type == "fail":
                    print(f"\n  [agent run reported failure] {ev.text}", flush=True)
            print()
            return analyst.result().text or "".join(chunks) or "_(agent returned no text — check model credentials)_"
    except MantisError as exc:
        return f"_(agent synthesis unavailable: {exc})_"


# ----------------------------------------------------------------------------- phase 4: brief
def write_brief(spaces: dict[str, str], metrics: dict, synthesis: str) -> str:
    """assemble the weekly markdown briefing."""
    lines = [
        "# 🐜 Repo Radar — Weekly Mantis Briefing",
        f"_generated {time.strftime('%Y-%m-%d %H:%M')}_\n",
        "## Portfolio",
    ]
    for name, sid in spaces.items():
        m = metrics.get(name, {})
        d = m.get("delta_since_last_run", 0)
        lines.append(f"- **{name}** — {m.get('points', '?')} points "
                     f"({d:+d} since last run) · `{sid}`")
    lines += ["", "## Cross-space synthesis", synthesis or "_n/a_"]
    chart = metrics.get("_chart")
    if chart:
        lines += ["", f"![contributors]({chart})"]
    brief = "\n".join(lines)
    out = Path(os.getenv("REPO_RADAR_BRIEF", "/tmp/repo_radar_brief.md"))
    out.write_text(brief)
    print(f"\n=== BRIEF written → {out} ===")
    return brief


# ----------------------------------------------------------------------------- main
async def main():
    provider = Provider(os.getenv("MANTIS_PROVIDER", "opencode"))
    client = make_client()
    state = _load_state()

    spaces = build_maps(client)
    if not spaces:
        print("no maps created — aborting.")
        return
    state["spaces"] = spaces

    metrics = analyze(client, spaces, state)
    state["metrics"] = {k: v for k, v in metrics.items() if not k.startswith("_")}

    synthesis = await synthesize(client, spaces, provider)

    brief = write_brief(spaces, metrics, synthesis)
    _save_state(state)
    print("\n" + "=" * 60 + "\n" + brief[:600])


if __name__ == "__main__":
    asyncio.run(main())
