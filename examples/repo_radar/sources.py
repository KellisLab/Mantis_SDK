"""data sources for the Mantis-on-Mantis demo (repo_radar).

pulls four real corpora into pandas DataFrames ready for client.spaces.create():
  - github_prs / github_issues  : ALL active (open) PRs/issues across both repos (GitHub REST)
  - github_authors              : everyone who ever committed to either repo (GitHub REST)
  - meeting_notes               : the Mantis meeting-notes Google Doc, one point per segment

each returns (df, data_types) so the caller just hands it to the SDK. no SDK imports here —
this is plain ingestion so it can be tested / run standalone."""
from __future__ import annotations

import os
import re
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
def github_prs(repos: list[str] = REPOS, state: str = "open") -> tuple[pd.DataFrame, dict]:
    """ALL currently-active (open) PRs across the repos, each with its open/update dates.
    title is semantic; repo/author/labels categoric; created_at/updated_at are date facets."""
    rows = []
    for repo in repos:
        for pr in _paginate(f"{_API}/repos/{repo}/pulls", {"state": state, "sort": "created", "direction": "desc"}):
            body = (pr.get("body") or "").strip()
            # the semantic field must never be empty (the backend needs content to embed); many
            # PRs have no body, so fall back to the title — which is the richest signal anyway.
            summary = body or pr.get("title", "")
            rows.append({
                "title": f"#{pr['number']} {pr['title']}",
                "summary": summary[:2000],
                "repo": repo.split("/")[-1],
                "author": pr["user"]["login"] if pr.get("user") else "unknown",
                "state": "draft" if pr.get("draft") else "open",
                "labels": ", ".join(lbl["name"] for lbl in pr.get("labels", [])) or "none",
                "created_at": (pr.get("created_at") or "")[:10],
                "updated_at": (pr.get("updated_at") or "")[:10],
                "url": pr.get("html_url", ""),
            })
    df = pd.DataFrame(rows)
    types = {
        "title": "title", "summary": "semantic", "repo": "categoric", "author": "categoric",
        "state": "categoric", "labels": "categoric",
        "created_at": "date", "updated_at": "date", "url": "links",
    }
    return df, types


# --------------------------------------------------------------------------- github issues
def github_issues(repos: list[str] = REPOS, state: str = "open") -> tuple[pd.DataFrame, dict]:
    """ALL currently-active (open) issues across the repos, each with its open/update dates.
    the issues endpoint returns PRs too; we filter them out."""
    rows = []
    for repo in repos:
        for it in _paginate(f"{_API}/repos/{repo}/issues", {"state": state, "sort": "created", "direction": "desc"}):
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
                "updated_at": (it.get("updated_at") or "")[:10],
                "url": it.get("html_url", ""),
            })
    df = pd.DataFrame(rows)
    types = {
        "title": "title", "summary": "semantic", "repo": "categoric", "author": "categoric",
        "state": "categoric", "labels": "categoric", "comments": "numeric",
        "created_at": "date", "updated_at": "date", "url": "links",
    }
    return df, types


# ------------------------------------------------------------------------- git author rollup
def github_authors(repos: list[str] = REPOS, max_commit_pages: int = 10) -> tuple[pd.DataFrame, dict]:
    """EVERYONE who has ever committed to either repo, via the GitHub API (no local clone).

    the /contributors endpoint is the authoritative all-time roster (so the list is complete);
    a bounded pass over /commits enriches each author with what they actually worked on and
    their latest commit date. authors past the commit cap still appear (from /contributors),
    just with a generic summary — we log when that truncation happens so it isn't silent."""
    agg: dict[str, dict] = {}

    def _row(login: str) -> dict:
        return agg.setdefault(login, {"commits": 0, "repos": set(), "subjects": [], "last": ""})

    # 1) authoritative all-time roster + total contribution counts.
    for repo in repos:
        for c in _paginate(f"{_API}/repos/{repo}/contributors", {}):
            login = c.get("login") or "unknown"
            r = _row(login)
            r["commits"] += int(c.get("contributions", 0))
            r["repos"].add(repo.split("/")[-1])

    # 2) enrich with commit subjects + latest date (bounded; union of authors is already complete).
    enriched = set()
    for repo in repos:
        commits = _paginate(f"{_API}/repos/{repo}/commits", {}, max_pages=max_commit_pages)
        if len(commits) >= max_commit_pages * 100:
            print(f"  [authors] note: /commits for {repo} hit the {max_commit_pages*100}-commit "
                  f"cap — older subjects omitted (roster stays complete via /contributors)")
        for c in commits:
            login = (c.get("author") or {}).get("login") or (c.get("commit", {}).get("author", {}).get("name") or "unknown")
            r = _row(login)
            msg = (c.get("commit", {}).get("message") or "").splitlines()[0] if c.get("commit") else ""
            if msg and len(r["subjects"]) < 40:
                r["subjects"].append(msg)
            date = (c.get("commit", {}).get("author", {}).get("date") or "")[:10]
            if date and date > r["last"]:
                r["last"] = date
            enriched.add(login)

    rows = []
    for login, d in agg.items():
        repos_str = ", ".join(sorted(d["repos"])) or "—"
        summary = " • ".join(d["subjects"]) if d["subjects"] else f"{login} — {d['commits']} commits to {repos_str}"
        rows.append({
            "title": login,
            "summary": summary,
            "commits": d["commits"],
            "repos": repos_str,
            "author": login,
            "last_commit": d["last"],
        })
    df = pd.DataFrame(rows).sort_values("commits", ascending=False) if rows else pd.DataFrame(rows)
    types = {"title": "title", "summary": "semantic", "commits": "numeric",
             "repos": "categoric", "author": "categoric", "last_commit": "date"}
    return df, types


# ----------------------------------------------------------------------------- meeting notes
# a meeting heading like "2026.06.10 Wed5pm Mantis Leadership".
_MEETING_HEADING = re.compile(r"^(20\d\d\.\d\d\.\d\d)\s+\S+\s+(.*)$", re.MULTILINE)
# a numbered segment line: "7. Topic Title - Speaker A, Speaker B (0:14:19): body…".
_SEGMENT = re.compile(
    r"^\s*\d+\.\s+(?P<title>.+?)\s+-\s+(?P<speakers>[^()]+?)\s+\((?P<ts>\d+:\d{2}(?::\d{2})?)\):\s+(?P<body>.+)$",
    re.MULTILINE,
)


def meeting_notes(gdoc_id: str = GDOC_ID) -> tuple[pd.DataFrame, dict]:
    """the Mantis meeting-notes Google Doc as ONE POINT PER SPEAKER SEGMENT.

    each meeting is a dated heading followed by numbered segments of the form
    'N. Title - Speakers (timestamp): discussion…'. we emit one row per segment so the map
    captures who said what, when — the body is semantic; speakers/meeting/date are facets."""
    url = f"https://docs.google.com/document/d/{gdoc_id}/export?format=txt"
    text = requests.get(url, timeout=30).text

    meetings = list(_MEETING_HEADING.finditer(text))
    rows = []
    for i, m in enumerate(meetings):
        start, end = m.end(), (meetings[i + 1].start() if i + 1 < len(meetings) else len(text))
        date_dot, meeting = m.group(1), m.group(2).strip()
        date = date_dot.replace(".", "-")
        for seg in _SEGMENT.finditer(text[start:end]):
            speakers = ", ".join(s.strip() for s in seg.group("speakers").split(","))
            primary = speakers.split(",")[0].strip() if speakers else "unknown"
            rows.append({
                "title": f"{date} · {seg.group('title').strip()}"[:120],
                "summary": seg.group("body").strip()[:4000],
                "speakers": speakers,
                "primary_speaker": primary,
                "meeting": meeting,
                "timestamp": seg.group("ts"),
                "date": date,
            })
    df = pd.DataFrame(rows)
    types = {"title": "title", "summary": "semantic", "speakers": "categoric",
             "primary_speaker": "categoric", "meeting": "categoric",
             "timestamp": "categoric", "date": "date"}
    return df, types


# ----------------------------------------------------------------------------- code files
_CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}
_SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", "venv", "dist", "build", ".next",
    "migrations", "tests", "test", "__tests__", "fixtures", ".pytest_cache",
}
_MAX_SNIPPET = 1200  # chars of file content to embed
_MAX_FILES = 800  # cap total files to keep embedding time reasonable


def _build_file_commit_stats(repos: list[str], max_commits: int = 50) -> dict[str, dict]:
    """Build per-file stats (top_author, last_author, last_updated) from recent commit details.

    Fetches the last `max_commits` commits per repo with their file lists (one API call each)
    to build author/date metadata per file path."""
    from collections import Counter
    file_authors: dict[str, list[str]] = {}
    file_dates: dict[str, str] = {}

    for repo in repos:
        repo_short = repo.split("/")[-1]
        # get recent commit SHAs
        commits = _paginate(f"{_API}/repos/{repo}/commits", {}, max_pages=1)[:max_commits]
        for commit in commits:
            sha = commit.get("sha")
            login = (commit.get("author") or {}).get("login") or "unknown"
            date = (commit.get("commit", {}).get("author", {}).get("date") or "")[:10]
            if not sha:
                continue
            # fetch detail to get files list
            try:
                detail = requests.get(
                    f"{_API}/repos/{repo}/commits/{sha}",
                    headers=_gh_headers(), timeout=15,
                ).json()
            except Exception:
                continue
            for f in detail.get("files", []):
                path = f.get("filename", "")
                if not path:
                    continue
                key = f"{repo_short}/{path}"
                file_authors.setdefault(key, []).append(login)
                if date and (not file_dates.get(key) or date > file_dates[key]):
                    file_dates[key] = date
            time.sleep(0.05)

    stats = {}
    for key, authors in file_authors.items():
        c = Counter(authors)
        stats[key] = {
            "top_author": c.most_common(1)[0][0],
            "last_author": authors[0],
            "last_updated": file_dates.get(key, ""),
        }
    return stats


def github_code(repos: list[str] = REPOS, branch: str = "main") -> tuple[pd.DataFrame, dict]:
    """Source-tree walk of both repos via the Git Trees API — one point per source file.

    Focuses on Python/TS/JS source files (not configs), sorted by size descending and capped
    at _MAX_FILES to keep embedding time under ~10 minutes. Each point's semantic content is
    the first ~1200 chars (imports + top-level signatures). Enriched with commit metadata
    (most frequent author, last author, last updated date)."""
    # pre-fetch commit stats for author/date enrichment
    file_stats = _build_file_commit_stats(repos)

    candidates = []
    for repo in repos:
        tree_url = f"{_API}/repos/{repo}/git/trees/{branch}?recursive=1"
        resp = requests.get(tree_url, headers=_gh_headers(), timeout=30)
        resp.raise_for_status()
        tree = resp.json().get("tree", [])

        for entry in tree:
            if entry.get("type") != "blob":
                continue
            path = entry["path"]
            parts = path.split("/")
            if any(d in _SKIP_DIRS for d in parts[:-1]):
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext not in _CODE_EXTENSIONS:
                continue
            # skip test files by name
            basename = parts[-1].lower()
            if basename.startswith("test_") or basename.endswith("_test.py") or basename.endswith(".test.ts") or basename.endswith(".test.tsx"):
                continue
            candidates.append((repo, path, parts, ext, entry.get("size", 0)))

    # prioritize larger files (more substance) and cap
    candidates.sort(key=lambda c: c[4], reverse=True)
    candidates = candidates[:_MAX_FILES]

    rows = []
    for repo, path, parts, ext, size in candidates:
        raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
        try:
            raw_resp = requests.get(raw_url, headers=_gh_headers(), timeout=10)
            raw_resp.raise_for_status()
            snippet = raw_resp.text[:_MAX_SNIPPET]
        except Exception:
            snippet = f"(file at {path})"
        time.sleep(0.02)

        directory = "/".join(parts[:-1]) or "."
        repo_short = repo.split("/")[-1]
        stats = file_stats.get(f"{repo_short}/{path}", {})
        rows.append({
            "title": path,
            "content": snippet,
            "repo": repo_short,
            "directory": directory,
            "extension": ext,
            "size": size,
            "top_author": stats.get("top_author", "unknown"),
            "last_author": stats.get("last_author", "unknown"),
            "last_updated": stats.get("last_updated", ""),
        })
    df = pd.DataFrame(rows)
    types = {
        "title": "title", "content": "semantic", "repo": "categoric",
        "directory": "categoric", "extension": "categoric", "size": "numeric",
        "top_author": "categoric", "last_author": "categoric", "last_updated": "date",
    }
    return df, types


if __name__ == "__main__":
    # standalone smoke: print row counts for each source (proves the pullers work).
    nb, _ = meeting_notes()
    print(f"meeting_notes: {len(nb)} speaker segments")
    if os.environ.get("GITHUB_TOKEN"):
        prs, _ = github_prs()
        iss, _ = github_issues()
        auth, _ = github_authors()
        code, _ = github_code()
        print(f"github_prs(open): {len(prs)} | github_issues(open): {len(iss)} | "
              f"authors(all-time): {len(auth)} | code(files): {len(code)}")
    else:
        print("set GITHUB_TOKEN to smoke-test the github pullers")
