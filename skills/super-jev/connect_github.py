#!/usr/bin/env python3
"""GitHub connector: make a repo's history searchable as a Super Jev source.

    superjev connect-github OWNER/REPO --pointer NAME --principal BOT [--refresh]
    python3 connect_github.py OWNER/REPO --pointer NAME --principal BOT [--refresh]

Uses the gh CLI you already signed in with (it never prints or reads a token itself)
to export one markdown file per item:
    prs/pr-<n>.md        title, state, dates, body, reviews, inline review comments, comments
    issues/issue-<n>.md  title, state, labels, body, comments
    commits/commit-<sha7>.md  full commit message
    releases/release-<tag>.md release notes
Any line that trips the shared secret scan (prepare_bulk.has_secret) is dropped
before it is written. Files land in $SUPERJEV_STATE_DIR/<principal>/github/<owner>-<repo>/
(or --out) and are then connected through the normal prepare_bulk.py path.

--refresh fetches only items updated since the last successful export (recorded in
.github-sync.json in the output folder) and re-prepares the pointer with --refresh;
run it after each merge. A first run with --refresh falls back to a full export.
--no-connect exports only. Writer flags (--writer, --writer-model, --writer-command)
pass through to prepare_bulk.py.
"""
import argparse, json, os, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SYNC_FILE = ".github-sync.json"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def gh(args: list) -> str:
    """Run gh and return stdout. Tests replace this function with a stub."""
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh {args[0]} {args[1] if len(args) > 1 else ''} failed: "
                           f"{(r.stderr or '').strip()[:200]}")
    return r.stdout


def gh_json(args: list):
    out = gh(args)
    return json.loads(out) if out.strip() else []


def gh_paginated(path: str) -> list:
    """gh api --paginate --slurp returns a list of pages; flatten it."""
    pages = gh_json(["api", path, "--paginate", "--slurp"])
    items = []
    for page in pages:
        items.extend(page if isinstance(page, list) else [page])
    return items


def _state_dir(principal: str) -> Path:
    root = os.environ.get("SUPERJEV_STATE_DIR")
    base = Path(root).expanduser() if root else Path.home() / ".local/state/super-jev"
    return base / principal


def scrub(text: str) -> tuple[str, int]:
    from prepare_bulk import has_secret
    kept, dropped = [], 0
    for line in text.splitlines():
        if line.strip() and has_secret(line):
            dropped += 1
            continue
        kept.append(line)
    return "\n".join(kept).rstrip() + "\n", dropped


def _who(obj) -> str:
    return ((obj or {}).get("login") or "unknown") if isinstance(obj, dict) else "unknown"


def _block(title: str, rows: list, fmt) -> list:
    out = [f"## {title}", ""]
    out += [fmt(r) for r in rows] if rows else ["_none_"]
    return out + [""]


def render_pr(pr: dict, inline: list, repo: str) -> str:
    md = [f"# PR #{pr['number']}: {pr.get('title') or ''}", "",
          f"- Repo: {repo}", f"- State: {pr.get('state')}",
          f"- Author: {_who(pr.get('author'))}",
          f"- Created: {pr.get('createdAt') or 'unknown'}",
          f"- Merged: {pr.get('mergedAt') or 'not merged'}",
          f"- Branch: {pr.get('headRefName') or 'unknown'}",
          f"- URL: {pr.get('url') or ''}", "", "## Description", "",
          (pr.get("body") or "").strip() or "_empty_", ""]
    md += _block("Reviews", [r for r in pr.get("reviews") or [] if (r.get("body") or "").strip()],
                 lambda r: f"- {_who(r.get('author'))} [{r.get('state')}]: {r.get('body').strip()}\n")
    md += _block("Inline review comments", inline,
                 lambda c: f"- {_who(c.get('user'))} on {c.get('path')}: {(c.get('body') or '').strip()}\n")
    md += _block("Comments", pr.get("comments") or [],
                 lambda c: f"- {_who(c.get('author'))}: {(c.get('body') or '').strip()}\n")
    return "\n".join(md)


def render_issue(it: dict, repo: str) -> str:
    labels = ", ".join(l.get("name", "") for l in it.get("labels") or []) or "none"
    md = [f"# Issue #{it['number']}: {it.get('title') or ''}", "",
          f"- Repo: {repo}", f"- State: {it.get('state')}", f"- Labels: {labels}",
          f"- Author: {_who(it.get('author'))}",
          f"- Created: {it.get('createdAt') or 'unknown'}",
          f"- Closed: {it.get('closedAt') or 'open'}",
          f"- URL: {it.get('url') or ''}", "", "## Description", "",
          (it.get("body") or "").strip() or "_empty_", ""]
    md += _block("Comments", it.get("comments") or [],
                 lambda c: f"- {_who(c.get('author'))}: {(c.get('body') or '').strip()}\n")
    return "\n".join(md)


def render_commit(c: dict, repo: str) -> str:
    msg = ((c.get("commit") or {}).get("message") or "").strip()
    subject, _, rest = msg.partition("\n")
    author = (c.get("commit") or {}).get("author") or {}
    return "\n".join([f"# Commit {c['sha'][:7]}: {subject}", "", f"- Repo: {repo}",
                      f"- SHA: {c['sha']}", f"- Author: {author.get('name') or 'unknown'}",
                      f"- Date: {author.get('date') or 'unknown'}", "", "## Message", "",
                      rest.strip() or "_subject only_", ""])


def render_release(r: dict, repo: str) -> str:
    return "\n".join([f"# Release {r.get('tagName')}: {r.get('name') or r.get('tagName')}", "",
                      f"- Repo: {repo}", f"- Published: {r.get('publishedAt') or 'unknown'}",
                      f"- URL: {r.get('url') or ''}", "", "## Release notes", "",
                      (r.get("body") or "").strip() or "_empty_", ""])


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "item"


def export(repo: str, out: Path, since: str | None) -> dict:
    """Write one file per item into out/; only items updated at/after `since` when given."""
    counts = {"prs": 0, "issues": 0, "commits": 0, "releases": 0, "dropped_lines": 0}

    def write(rel: str, text: str):
        cleaned, dropped = scrub(text)
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(cleaned)
        counts["dropped_lines"] += dropped

    search = ["--search", f"updated:>={since}"] if since else []
    prs = gh_json(["pr", "list", "-R", repo, "--state", "all", "--limit", "5000",
                   "--json", "number", *search])
    for n in sorted(p["number"] for p in prs):
        pr = gh_json(["pr", "view", str(n), "-R", repo, "--json",
                      "number,title,state,author,createdAt,mergedAt,headRefName,url,body,reviews,comments"])
        inline = gh_paginated(f"repos/{repo}/pulls/{n}/comments?per_page=100")
        write(f"prs/pr-{n}.md", render_pr(pr, inline, repo))
        counts["prs"] += 1

    issues = gh_json(["issue", "list", "-R", repo, "--state", "all", "--limit", "5000",
                      "--json", "number", *search])
    for n in sorted(i["number"] for i in issues):
        it = gh_json(["issue", "view", str(n), "-R", repo, "--json",
                      "number,title,state,author,createdAt,closedAt,labels,url,body,comments"])
        write(f"issues/issue-{n}.md", render_issue(it, repo))
        counts["issues"] += 1

    path = f"repos/{repo}/commits?per_page=100" + (f"&since={since}" if since else "")
    for c in gh_paginated(path):
        write(f"commits/commit-{c['sha'][:7]}.md", render_commit(c, repo))
        counts["commits"] += 1

    rels = gh_json(["release", "list", "-R", repo, "--limit", "1000",
                    "--json", "tagName,publishedAt"])
    for r in rels:
        if since and (r.get("publishedAt") or "") < since:
            continue
        full = gh_json(["release", "view", r["tagName"], "-R", repo, "--json",
                        "tagName,name,body,publishedAt,url"])
        write(f"releases/release-{_safe(r['tagName'])}.md", render_release(full, repo))
        counts["releases"] += 1
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="superjev connect-github",
                                 description="Connect a GitHub repo's PR/issue/commit/release history.")
    ap.add_argument("repo", help="OWNER/REPO")
    ap.add_argument("--pointer", required=True)
    ap.add_argument("--principal", required=True)
    ap.add_argument("--out", help="export folder (default: state dir/<principal>/github/<owner>-<repo>)")
    ap.add_argument("--refresh", action="store_true", help="only items updated since the last run")
    ap.add_argument("--no-connect", action="store_true", help="export only; skip prepare_bulk")
    ap.add_argument("--writer", choices=["auto", "claude", "builtin"])
    ap.add_argument("--writer-model"); ap.add_argument("--writer-command")
    a = ap.parse_args(argv)
    if not REPO_RE.match(a.repo):
        print("REFUSED: repo must look like OWNER/REPO"); return 2

    out = Path(a.out).expanduser() if a.out else _state_dir(a.principal) / "github" / _safe(a.repo.replace("/", "-"))
    out.mkdir(parents=True, exist_ok=True)
    sync_path = out / SYNC_FILE
    sync = json.loads(sync_path.read_text()) if sync_path.is_file() else {}
    since = sync.get("lastExport") if a.refresh and sync.get("repo") == a.repo else None
    if a.refresh and not since:
        print("refresh: no earlier export recorded; doing a full export")
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        counts = export(a.repo, out, since)
    except (RuntimeError, ValueError, KeyError) as e:
        print(f"ERROR: export failed: {e}"); return 1
    sync_path.write_text(json.dumps({"repo": a.repo, "lastExport": started}, indent=1) + "\n")
    print(f"exported {a.repo}{' since ' + since if since else ''}: {counts['prs']} PRs, "
          f"{counts['issues']} issues, {counts['commits']} commits, {counts['releases']} releases; "
          f"{counts['dropped_lines']} secret-looking line(s) dropped -> {out}")
    if a.no_connect:
        return 0
    total = sum(1 for _ in out.rglob("*.md"))
    if since and not any(counts[k] for k in ("prs", "issues", "commits", "releases")):
        print("nothing changed since the last export; pointer left as is")
        return 0
    cmd = [sys.executable, str(HERE / "prepare_bulk.py"), "--root", str(out), "--pointer", a.pointer,
           "--principal", a.principal, "--max-files", str(max(250, total + 10))]
    if (HERE / "prepare-cache" / f"{a.pointer}.json").is_file():
        cmd.append("--refresh")
    for flag in ("writer", "writer_model", "writer_command"):
        if getattr(a, flag):
            cmd += [f"--{flag.replace('_', '-')}", getattr(a, flag)]
    return subprocess.run(cmd, cwd=HERE).returncode


if __name__ == "__main__":
    sys.exit(main())
