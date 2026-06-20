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
  GITHUB_TOKEN                         to pull the private repos (PRs, issues, authors)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
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
ALIAS = os.getenv("REPO_RADAR_ALIAS", "m4m")


def _stable_map_id(space_id: str, name: str) -> str:
    """deterministic per-(space, logical map) id so re-runs refresh the SAME map in place."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{space_id}:{name}"))


def build_maps(client: MantisClient) -> tuple[str, dict[str, str]]:
    """idempotently maintain ONE aliased space (/space/<ALIAS>) holding the 4 radar maps.

    re-runs reuse the same space (resolve via alias) and refresh each map in place via a stable
    map_id — no duplicate spaces, no orphan maps. returns (space_id, {name: map_id})."""
    print(f"\n=== PHASE 1: building the Mantis-on-Mantis space (/space/{ALIAS}) ===")
    space_id, created = client.aliases.resolve_or_create_space(ALIAS)
    print(f"  space {space_id} ({'new' if created else 'reused'})")

    builders = {
        "prs": lambda: sources.github_prs(),
        "issues": lambda: sources.github_issues(),
        "authors": lambda: sources.github_authors(),
        "notes": lambda: sources.meeting_notes(),
        "code": lambda: sources.github_code(),  # largest — run last so others don't queue behind it
    }
    # friendly per-map titles (without this the backend names every map "Untitled Map").
    map_titles = {
        "prs": "Open Pull Requests", "issues": "Open Issues",
        "authors": "Contributors (all-time)", "code": "Codebase",
        "notes": "Meeting Notes",
    }
    maps: dict[str, str] = {}
    cap = int(os.getenv("REPO_RADAR_CAP", "0"))  # optional row cap for bounded/demo runs
    for name, build in builders.items():
        try:
            df, data_types = build()
            if cap:
                df = df.head(cap)
            if df.empty:
                print(f"  [skip] {name}: no rows")
                continue
            map_id = _stable_map_id(space_id, name)
            print(f"  [upsert] {name}: {len(df)} points → map {map_id[:8]}…")
            handle = client.spaces.create(
                f"Mantis Radar — {name}", df, data_types,
                space_id=space_id, map_id=map_id,  # same space + stable map id ⇒ idempotent refresh
                map_name=map_titles[name],  # so the map isn't named "Untitled Map"
                show_progress=False,
                stall_timeout=1800.0,  # code map can take a while for large file sets
                on_progress=lambda p, m, t, n=name: print(f"    {n}: {p:3d}% {m or ''}", end="\r"),
            )
            maps[name] = handle.map_id
            print(f"\n  [done] {name}: map {handle.map_id}")
        except Exception as exc:  # noqa: BLE001 — one bad source must not abort the whole run
            print(f"  [fail] {name}: {exc}")

    # alias the space ONCE (on first creation). the backend fix makes re-set idempotent, but we
    # only need it on the first run; resolve_or_create told us whether this is that run.
    if created and maps:
        try:
            client.aliases.set(space_id, ALIAS)
            print(f"  [alias] /space/{ALIAS} → {space_id}")
        except MantisError as exc:
            print(f"  [alias fail] {exc}")
    return space_id, maps


# ----------------------------------------------------------- phase 2: notebook delta analysis
DELTA_CODE_TEMPLATE = """\
# delta + velocity analysis — find THIS map by id in the kernel's maps list
import collections, json
_target = '{map_id}'
_m = next((m for m in maps if str(m.map_id) == _target), None) or maps[0]
pts = _m.points
rows = [getattr(p, 'metadata', {{}}) or {{}} for p in pts]
n = len(pts)

authors = collections.Counter(r.get('author', 'unknown') for r in rows)
states  = collections.Counter(r.get('state', r.get('topic', 'n/a')) for r in rows)

print("POINTS", n)
print("TOP_AUTHORS", json.dumps(authors.most_common(8)))
print("STATE_BREAKDOWN", json.dumps(dict(states)))
"""

CHART_CODE_TEMPLATE = """\
import collections
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
_target = '{map_id}'
_m = next((m for m in maps if str(m.map_id) == _target), None) or maps[0]
rows = [getattr(p, 'metadata', {{}}) or {{}} for p in _m.points]
c = collections.Counter(r.get('author', 'unknown') for r in rows).most_common(10)
labels = [a for a, _ in c]; vals = [v for _, v in c]
plt.figure(figsize=(9, 4))
plt.bar(labels, vals)
plt.xticks(rotation=45, ha='right'); plt.ylabel('contributions'); plt.title('Top contributors')
plt.tight_layout(); plt.show()
"""


def analyze(client: MantisClient, maps_by_name: dict[str, str], state: dict) -> dict:
    """run delta analysis in each map's kernel; checkpoint for week-over-week diffs."""
    print("\n=== PHASE 2: notebook delta analysis (per-map kernel) ===")
    prev = state.get("metrics", {})
    metrics: dict = {}
    chart_png = None
    # skip code map — it rebuilds last and the kernel loads stale (0-point) data during rebuild
    notebook_maps = {k: v for k, v in maps_by_name.items() if k != "code"}
    for name, map_id in notebook_maps.items():
        try:
            nb = client.notebooks.from_map(map_id, name=f"radar-{name}",
                                           user_id=os.getenv("MANTIS_USER_EMAIL"))
            cell = nb.add_cell(DELTA_CODE_TEMPLATE.format(map_id=map_id))
            cell.execute(timeout=300)
            text = cell.text
            pts = _grab_int(text, "POINTS")
            delta = pts - prev.get(name, {}).get("points", pts)
            metrics[name] = {"points": pts, "delta_since_last_run": delta, "raw": text[:800]}
            print(f"  [{name}] points={pts}  Δ={delta:+d} since last run")

            # one chart from the authors map for the brief
            if name == "authors" and chart_png is None:
                chart = nb.add_cell(CHART_CODE_TEMPLATE.format(map_id=map_id))
                chart.execute(timeout=300)
                png = chart.image_png_bytes()
                if png:
                    chart_png = "/tmp/repo_radar_contributors.png"
                    Path(chart_png).write_bytes(png)
                    print(f"  [chart] saved {chart_png} ({len(png)} bytes)")

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

SYNTHESIS_PROMPT = """\
You are Repo Radar, a weekly intelligence digest for the Mantis engineering team.

Using the PRs, issues, contributors, and codebase maps in this space, write a concise team-readable briefing. Format it exactly like this:

## What shipped / is shipping
2-4 bullet points on the biggest themes of merged or near-merge work this week. Name the PRs and who owns them.

## Who's doing what
A short table or list: for each active contributor, what area they're focused on (1 line each, max 8 people).

## Risks & blockers
2-3 bullet points on concrete risks: stale PRs, security gaps, unreviewed dependencies, broken tests, or resource bottlenecks.

## Recommended priorities (next week)
2-3 actionable bullets the team lead can act on Monday morning.

Keep it scannable — short sentences, bold the PR titles, use people's GitHub handles. No preamble, no sign-off.\
"""


async def synthesize(client: MantisClient, space_id: str, provider: Provider) -> tuple[str, str | None]:
    """run an agent to synthesize a briefing. returns (text, chat_id).

    provider-scoped (opencode default, claude_code opt-in). degrades gracefully if the agent
    runtime/credentials are unavailable.

    the agent is scoped to the one m4m space (it auto-mints a space-state so its MCP tools can
    inspect the maps). all_spaces mode would reason across every accessible space instead."""
    print(f"\n=== PHASE 3: agent synthesis (provider={provider}, all_spaces={USE_ALL_SPACES}) ===")
    email = os.environ["MANTIS_USER_EMAIL"]
    target = None if USE_ALL_SPACES else space_id
    try:
        async with client.agents.session(target, all_spaces=USE_ALL_SPACES, provider=provider,
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
            text = analyst.result().text or "".join(chunks) or "_(agent returned no text — check model credentials)_"
            # build message list for persistence
            messages = [
                {"role": "user", "content": SYNTHESIS_PROMPT},
                {"role": "assistant", "content": text},
            ]
            return text, analyst.server_chat_id or analyst.chat_id, messages
    except Exception as exc:
        return f"_(agent synthesis unavailable: {type(exc).__name__}: {exc})_", None, None


# ----------------------------------------------------------------------------- phase 4: brief
def write_brief(space_id: str, maps_by_name: dict[str, str], metrics: dict, synthesis: str) -> str:
    """assemble the weekly markdown briefing."""
    host = os.getenv("MANTIS_HOST", os.getenv("MANTIS_BACKEND_HOST", ""))
    lines = [
        "# 🐜 Repo Radar — Weekly Mantis Briefing",
        f"_generated {time.strftime('%Y-%m-%d %H:%M')}_\n",
        f"**Space:** [/space/{ALIAS}]({host}/space/{ALIAS}) · `{space_id}`\n",
        "## Maps",
    ]
    for name in maps_by_name:
        m = metrics.get(name, {})
        d = m.get("delta_since_last_run", 0)
        lines.append(f"- **{name}** — {m.get('points', '?')} points ({d:+d} since last run)")
    lines += ["", "## Synthesis", synthesis or "_n/a_"]
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

    space_id, maps_by_name = build_maps(client)
    if not maps_by_name:
        print("no maps created — aborting.")
        return
    state["space_id"] = space_id
    state["maps"] = maps_by_name

    metrics = analyze(client, maps_by_name, state)
    state["metrics"] = {k: v for k, v in metrics.items() if not k.startswith("_")}

    synthesis, chat_id, chat_messages = await synthesize(client, space_id, provider)

    # pin the synthesis conversation so visitors see it by default
    if chat_id:
        try:
            client.featured_chat.set(space_id, chat_id, messages=chat_messages,
                                     title="Repo Radar Briefing")
            print(f"  [featured] pinned chat {chat_id} to space")
        except Exception as exc:
            print(f"  [featured] failed to pin: {exc}")

    brief = write_brief(space_id, maps_by_name, metrics, synthesis)
    _save_state(state)
    print("\n" + "=" * 60 + "\n" + brief[:600])


if __name__ == "__main__":
    asyncio.run(main())
