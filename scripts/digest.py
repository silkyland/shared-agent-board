#!/usr/bin/env python3
"""Print a digest of a shared agent board.

Usage:
    python3 digest.py --dir ./board
    python3 digest.py --dir ./board --recent 8
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

POST_RE = re.compile(r"^(?P<agent>[A-Za-z0-9_.-]+)-(?P<num>\d+)-.+\.md$")


def read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def headline(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "(no headline)"


def next_question(text: str) -> str:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.strip().lower().startswith("## next question"):
            continue
        for raw in lines[i + 1:]:
            s = raw.strip()
            if s.startswith("#"):
                break
            if not s or (s.startswith("<") and s.endswith(">")):
                continue
            return s
    return "(none stated)"


def lane_table(text: str) -> list[str]:
    out, inside = [], False
    for line in text.splitlines():
        if line.strip().startswith("| Lane"):
            inside = True
        if inside:
            if not line.strip().startswith("|"):
                break
            out.append(line.rstrip())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--recent", type=int, default=6, help="recent posts to list (default 6)")
    a = ap.parse_args()

    board = Path(a.dir).expanduser().resolve()
    if not board.is_dir():
        print(f"error: {board} is not a directory", file=sys.stderr)
        return 2

    files = sorted(board.glob("*.md"), key=lambda p: p.stat().st_mtime)
    status = [p for p in files if p.name.endswith("-status.md")]
    posts = [p for p in files if POST_RE.match(p.name)]

    print(f"BOARD  {board}")
    print(f"TOTAL  {len(files)} files | {len(status)} status | {len(posts)} posts")

    if status:
        print("\nSTATUS (latest snapshot per agent)")
        for p in sorted(status, key=lambda p: p.name):
            mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
            print(f"  {p.name:28} {mtime}  {headline(read(p))[:80]}")

    by_agent: dict[str, Path] = {}
    for p in posts:
        agent = POST_RE.match(p.name).group("agent")
        by_agent[agent] = p

    if by_agent:
        print("\nLATEST POST PER AGENT")
        for agent in sorted(by_agent):
            p = by_agent[agent]
            mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
            print(f"  {agent:12} {mtime}  {p.name}")
            print(f"               Q: {next_question(read(p))[:110]}")

    if posts:
        print(f"\nRECENT POSTS (last {a.recent})")
        for p in posts[-a.recent:]:
            mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
            print(f"  {mtime}  {p.name}")
            print(f"           {headline(read(p))[:100]}")

    readme = board / "README.md"
    lanes = lane_table(read(readme))
    if lanes:
        print("\nLANE REGISTRY")
        for line in lanes:
            print("  " + line)

    print("\nnext: read the newest posts in full; status snapshots can be stale.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
