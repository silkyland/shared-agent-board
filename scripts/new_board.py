#!/usr/bin/env python3
"""Scaffold a shared agent board.

Usage:
    python3 new_board.py --dir ./board --agents codex,kestrel,devin
    python3 new_board.py --dir ./board --agents a,b --goal "boot macOS in QEMU"
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

README = """# Shared agent board

Shared memory for {n} independent agent sessions working one problem.

**Files are messages. Claims are locks. Evidence is currency.**

## Rules

1. One writer per file. Never edit another agent's file; reply with your own post.
2. Read every status file and the last few posts before any major new experiment.
3. Post a one-line lane claim before long runs; post evidence after.
4. Evidence: exact command, log path, literal output, scope limits.
5. End every post with exactly one next question.

## Agents

{agents}

## Lane registry

Claim your worktree, ports, sockets and log paths here before launching.

| Lane | Agent | Worktree | GDB/port | Socket | Logs |
|------|-------|----------|----------|--------|------|
|lane-1|<agent>|<worktree>|<port>|<socket>|<logs>|

## Shared goal

{goal}

## Board conventions

- `<agent>-status.md` — current truth, owner-edited snapshot.
- `<agent>-NN-slug.md` — immutable numbered posts.
- Retire lanes in writing when you leave.
"""

STATUS = """# {agent} status — <lane name>

Updated: {ts}

## Current truth
<3-6 lines: what is true right now, not a history>

## Lane
- Worktree: `<path>`
- Ports / sockets: `<port>`, `<socket path>`
- Logs: `<paths>`

## What works (verified)
- <claim> — evidence: `<path>`

## What is broken / open
- <blocker> — last attempt: `<what you tried>`

## Files others may reuse
- `<path>` — <what it is>

## Next question
<one question>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, help="board directory to create")
    ap.add_argument("--agents", required=True, help="comma-separated agent names")
    ap.add_argument("--goal", default="<one sentence: the problem every lane is attacking>")
    ap.add_argument("--force", action="store_true", help="write into a non-empty directory")
    a = ap.parse_args()

    board = Path(a.dir).expanduser().resolve()
    agents = [x.strip() for x in a.agents.split(",") if x.strip()]
    if not agents:
        print("error: --agents is empty", file=sys.stderr)
        return 2
    bad = [x for x in agents if not all(c.isalnum() or c in "-_." for c in x)]
    if bad:
        print(f"error: invalid agent name(s): {bad}", file=sys.stderr)
        return 2

    board.mkdir(parents=True, exist_ok=True)
    if any(board.iterdir()) and not a.force:
        print(f"error: {board} is not empty (use --force)", file=sys.stderr)
        return 1

    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    readme = board / "README.md"
    if not readme.exists():
        readme.write_text(
            README.format(
                n=len(agents),
                agents="\n".join(f"- **{x}** — `<lane>`" for x in agents),
                goal=a.goal,
            )
        )

    created = []
    for agent in agents:
        f = board / f"{agent}-status.md"
        if not f.exists():
            f.write_text(STATUS.format(agent=agent.capitalize(), ts=ts))
            created.append(f.name)

    print(f"board: {board}")
    print(f"agents: {', '.join(agents)}")
    if created:
        print("created: " + ", ".join(created))
    print("next: each agent fills its status, claims a lane in README.md, then posts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
