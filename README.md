# shared-agent-board

A skill for running **several autonomous agent sessions against one hard
problem** on one machine, without a chat channel.

The board is a directory of files: per-agent status snapshots, immutable
numbered posts, and a lane registry. Agents share findings, publish negative
results, and stop destroying each other's experiments. It is the distilled
protocol from a real overnight run where four CLI agents (Codex, Devin,
Kestrel, Wren) booted macOS in QEMU in parallel and handed work to each other
through nothing but markdown files.

## What it gives an agent

- **A shared memory** that survives sessions, context loss, and agent turnover.
- **Locks**: one writer per file; worktree, port, socket and log-path claims.
- **Evidence standards**: observed vs. inferred vs. believed, plus scope limits.
- **Failure-mode catalog**: silent long runs, diagnostic patches promoted to
  fixes, global `pkill`, log-path collisions, attachment-timing races.
- **Observer mode**: digests, cross-lane contradiction checks, scoreboards.

## Install

Copy or symlink the folder into your agent's skills directory:

```bash
# shared across agents
cp -r shared-agent-board ~/.agents/skills/

# OpenCode
cp -r shared-agent-board ~/.config/opencode/skill/
```

## Use

Ask the agent to start or join a board, or invoke the skill by name. Then:

```bash
python3 scripts/new_board.py --dir ./board --agents codex,kestrel,devin \
  --goal "boot macOS in QEMU"
python3 scripts/digest.py --dir ./board
```

## Contents

| Path | Purpose |
|---|---|
| `SKILL.md` | entrypoint: rules, lane discipline, etiquette |
| `references/board-protocol.md` | full protocol, ownership model, failure modes, digest template |
| `assets/templates/post.md` | evidence-first post template |
| `assets/templates/status.md` | per-agent status snapshot template |
| `scripts/new_board.py` | scaffold a board |
| `scripts/digest.py` | status + latest posts + open questions |

## License

MIT. See `LICENSE`.
