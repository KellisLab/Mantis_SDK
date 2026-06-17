"""data sources for the Mantis-on-Mantis demo (repo_radar).

pulls four real corpora into pandas DataFrames ready for client.spaces.create():
  - github_prs / github_issues  : open + recent PRs/issues across both repos (GitHub REST)
  - github_authors              : per-author contribution rollup from local git history
  - meeting_notes               : the Mantis meeting-notes Google Doc, split into dated entries

each returns (df, data_types) so the caller just hands it to the SDK. no SDK imports here —
this is plain ingestion so it can be tested / run standalone."""
from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Any

import pandas as pd
import requests

REPOS = ["KellisLab/MantisAPI", "KellisLab/Mantis"]
GDOC_ID = "1phoQhus36Dc3SEsFCE4V18YDjxEadwXekn-VCZu0d3o"
_API = "https://api.github.com"


def _gh_headers() -> dict[str, str]:
    tok = os.environ.get("GITHUB_TOKEN", "")
    if not tok:
        raise RuntimeError("set GITHUB_TOKEN to pull issues/PRs from the private repos")
    return {"Authorization": f"token {tok}", "Accept": "application/vnd.github+json"}


def _paginate(url: str, params: dict[str, Any], max_pages: int = 5) -> list[dict]:
    """page through a GitHub list endpoint (capped, with a friendly log)."""
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        resp = requests.get(url, headers=_gh_headers(), params={**params, "page": page, "per_page": 100}, timeout=30)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 100:
            break
        time.sleep(0.2)  # be polite to the API
    return out


# ----------------------------------------------------------------------------- github PRs
def github_prs(repos: list[str] = REPOS, state: str = "all") -> tuple[pd.DataFrame, dict]:
    """open + recent PRs across the repos. title is semantic; state/repo/author categoric."""
    rows = []
    for repo in repos:
        for pr in _paginate(f"{_API}/repos/{repo}/pulls", {"state": state, "sort": "updated"}):
            body = (pr.get("body") or "").strip()
            # the semantic field must never be empty (the backend needs content to embed); many
            # PRs have no body, so fall back to the title — which is the richest signal anyway.
            summary = body or pr.get("title", "")
            rows.append({
                "title": f"#{pr['number']} {pr['title']}",
                "summary": summary[:2000],
                "repo": repo.split("/")[-1],
                "author": pr["user"]["login"] if pr.get("user") else "unknown",
                "state": "merged" if pr.get("merged_at") else pr.get("state", "open"),
                "draft": str(bool(pr.get("draft"))),
                "labels": ", ".join(lbl["name"] for lbl in pr.get("labels", [])) or "none",
                "created_at": (pr.get("created_at") or "")[:10],
                "url": pr.get("html_url", ""),
            })
    df = pd.DataFrame(rows)
    types = {
        "title": "title", "summary": "semantic", "repo": "categoric", "author": "categoric",
        "state": "categoric", "draft": "categoric", "labels": "categoric",
        "created_at": "date", "url": "links",
    }
    return df, types


# --------------------------------------------------------------------------- github issues
def github_issues(repos: list[str] = REPOS, state: str = "all") -> tuple[pd.DataFrame, dict]:
    """issues only (the issues endpoint returns PRs too; we filter them out)."""
    rows = []
    for repo in repos:
        for it in _paginate(f"{_API}/repos/{repo}/issues", {"state": state, "sort": "updated"}):
            if "pull_request" in it:  # the issues endpoint includes PRs — drop them
                continue
            body = (it.get("body") or "").strip()
            summary = body or it.get("title", "")  # semantic field must be non-empty (see github_prs)
            rows.append({
                "title": f"#{it['number']} {it['title']}",
                "summary": summary[:2000],
                "repo": repo.split("/")[-1],
                "author": it["user"]["login"] if it.get("user") else "unknown",
                "state": it.get("state", "open"),
                "labels": ", ".join(lbl["name"] for lbl in it.get("labels", [])) or "none",
                "comments": it.get("comments", 0),
                "created_at": (it.get("created_at") or "")[:10],
                "url": it.get("html_url", ""),
            })
    df = pd.DataFrame(rows)
    types = {
        "title": "title", "summary": "semantic", "repo": "categoric", "author": "categoric",
        "state": "categoric", "labels": "categoric", "comments": "numeric",
        "created_at": "date", "url": "links",
    }
    return df, types


# ------------------------------------------------------------------------- git author rollup
def github_authors(repo_path: str, since_days: int = 90) -> tuple[pd.DataFrame, dict]:
    """per-author contribution rollup from the LOCAL git clone (no API needed).
    one point per author; commit count is numeric so the map can size/color by activity.

    raises ValueError (not CalledProcessError) if repo_path isn't a git repo, so the caller's
    'one source failed, skip it' handling kicks in cleanly."""
    import os.path

    fmt = "%an\t%s"
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        raise ValueError(
            f"MANTISAPI_PATH={repo_path!r} is not a git checkout — set it to your local "
            f"MantisAPI clone (e.g. ~/Mantis/MantisAPI)."
        )
    proc = subprocess.run(
        ["git", "-C", repo_path, "log", f"--since={since_days} days ago", f"--format={fmt}"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise ValueError(f"git log failed in {repo_path!r}: {proc.stderr.strip()[:200]}")
    out = proc.stdout.strip().splitlines()

    agg: dict[str, dict] = {}
    for line in out:
        if "\t" not in line:
            continue
        author, subject = line.split("\t", 1)
        a = agg.setdefault(author, {"commits": 0, "subjects": []})
        a["commits"] += 1
        a["subjects"].append(subject)

    rows = [
        {
            "title": author,
            "summary": " • ".join(d["subjects"][:40]),  # what they actually worked on
            "commits": d["commits"],
            "author": author,
        }
        for author, d in agg.items()
    ]
    df = pd.DataFrame(rows).sort_values("commits", ascending=False) if rows else pd.DataFrame(rows)
    types = {"title": "title", "summary": "semantic", "commits": "numeric", "author": "categoric"}
    return df, types


# ----------------------------------------------------------------------------- meeting notes
def meeting_notes(gdoc_id: str = GDOC_ID) -> tuple[pd.DataFrame, dict]:
    """the Mantis meeting-notes Google Doc, split into one point per dated meeting.

    the doc uses headings like '2026.06.10 Wed5pm Mantis Leadership' — we split on those
    and treat the body as the semantic field, the topic + date as facets."""
    url = f"https://docs.google.com/document/d/{gdoc_id}/export?format=txt"
    text = requests.get(url, timeout=30).text

    # split on date-prefixed headings: 2026.06.10 ...
    heading = re.compile(r"^(20\d\d\.\d\d\.\d\d)\s+(\w+)\s+(.*)$", re.MULTILINE)
    matches = list(heading.finditer(text))
    rows = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        date_dot, topic = m.group(1), m.group(3).strip()  # group(2) is the weekday, unused
        # topic looks like "Mantis Leadership" / "Mantis Agents Embeddings" — last word is a useful tag
        tag = topic.replace("Mantis", "").strip().split()[0] if topic.replace("Mantis", "").strip() else "General"
        rows.append({
            "title": f"{date_dot} {topic}"[:120],
            "summary": body[:4000],
            "topic": tag,
            "meeting": topic,
            "date": date_dot.replace(".", "-"),
        })
    df = pd.DataFrame(rows)
    types = {"title": "title", "summary": "semantic", "topic": "categoric",
             "meeting": "categoric", "date": "date"}
    return df, types


if __name__ == "__main__":
    # standalone smoke: print row counts for each source (proves the pullers work).
    os.environ.setdefault("GITHUB_TOKEN", "")
    nb, _ = meeting_notes()
    print(f"meeting_notes: {len(nb)} dated entries")
    if os.environ.get("GITHUB_TOKEN"):
        prs, _ = github_prs()
        iss, _ = github_issues()
        print(f"github_prs: {len(prs)} | github_issues: {len(iss)}")
    auth, _ = github_authors(os.environ.get("MANTISAPI_PATH", "."))
    print(f"github_authors: {len(auth)} contributors")
