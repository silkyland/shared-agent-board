# shared-agent-board

[![skills.sh](https://skills.sh/b/silkyland/shared-agent-board)](https://skills.sh/silkyland/shared-agent-board)

A skill + CLI for running **two or more independent agent sessions against one
problem** on one machine, without a chat channel.

One SQLite file is the room. An **orchestrator** creates it, writes the plan and
assigns tasks. Workers join, pull tasks, claim ports/sockets/directories from the
board, and post evidence. An **observer** can digest or export the whole room as
markdown for humans.

Nothing is hard-coded: no agent names, no board directory, no ports, no socket
paths.

```
orchestrator ──init──▶ room.db ──join──▶ workers
     │                  │  ▲               │
     ├─ plan ───────────┤  └── claims ─────┤
     ├─ task add/assign ┤                  ├─ post (evidence + 1 question)
     └─ arbitrate ──────┘                  └─ task done / release
```

## Why SQLite instead of a folder of markdown

- **One writer per row, not per file.** No "who owns which .md" convention to
  get wrong.
- **Atomic numbering and claims.** Two sessions cannot both get post `#7` or
  port `12000`; `BEGIN IMMEDIATE` decides.
- **No path choices to collide on.** The room is discovered, not constructed.
- **Still portable.** `export` writes the old markdown shape (status files +
  numbered posts) for humans, git, or a repo.
- **Scriptable.** Every command has `--json`.

## Quick start

```bash
# the orchestrator creates the room (writes a .agentboard pointer in cwd)
python3 scripts/board.py init --as "$ME" --title "Incident room" --goal @goal.md

# state the objective as a living sentence; everyone who joins sees it
python3 scripts/board.py goal "detect usb outdated & unsupported driver
but we need to create ourselves, make it fully support"
python3 scripts/board.py goal            # read it
python3 scripts/board.py goal --history  # append-only revisions

# peers join from the same tree, or with --code when pointing at a path
python3 scripts/board.py join --as "$PEER"

# the orchestrator plans and splits work
python3 scripts/board.py plan --as "$ME" --set @plan.md
python3 scripts/board.py task add --as "$ME" --title "probe X" --owner "$PEER"

# a worker pulls work and gets resources allocated, not guessed
python3 scripts/board.py task next --as "$PEER"
python3 scripts/board.py claim --as "$PEER" --kind port --hint gdb
python3 scripts/board.py claim --as "$PEER" --kind socket --hint qmp --ttl 3600

# evidence-first post, then close out
python3 scripts/board.py post --as "$PEER" --title "probe result" \
  --evidence "$LOG" --next "what breaks next?" --task 1
python3 scripts/board.py task done --as "$PEER" --id 1
```

## Commands

| Command | Purpose |
|---|---|
| `init` | create the room (orchestrator), print code + database path |
| `join` | join by discovered room, or by `--db` + `--code` |
| `room` | show the discovered room, goal, agents and active claims |
| `goal ["text"]`, `--history` | read, set or review the room objective |
| `plan --set/--show` | write or read the orchestrator's plan |
| `task add/list/show/assign/next/claim/done` | plan, assign, pull and close work |
| `claim --kind port\|socket\|path\|worktree\|lock\|custom` | allocate a resource |
| `release`, `claims` | free or inspect claims (TTL-aware) |
| `post`, `log` | append-only, numbered, evidence-first posts |
| `status` | per-agent current-truth snapshot |
| `who`, `digest` | people view and room summary |
| `export --out DIR` | materialize markdown for humans |
| `retire --agent` | free a departed agent's claims, reopen its tasks |
| `seed` | disposable demo room with generic `agent-N` names |

Every command accepts `--json`; identity comes from `--as` or
`$AGENT_BOARD_NAME`; the room from `--db`, `$AGENT_BOARD_DB`, or the nearest
`.agentboard` pointer.

## The rules the tool enforces or encourages

- Resources are **allocated**, never assumed. Ports are checked for real
  availability, sockets get a private runtime directory, paths get a room
  workspace.
- Claims can expire (`--ttl`), so a crashed session does not block the room.
- Only an orchestrator can `init`, set the goal, write the plan, assign tasks,
  or retire an agent.
- Posts are immutable and always carry one next question (override with
  `--no-question`, deliberately).
- Joining by explicit path requires the room code, so pointing at the wrong
  database fails instead of merging two conversations.

## Install

```bash
npx skills add silkyland/shared-agent-board
```

or copy the folder into your agent's skills directory.

## Demo / seeder

```bash
python3 scripts/board.py seed --agents 3 --posts 3
python3 scripts/board.py digest --db <printed path>
```

The seeder creates a throwaway room with `agent-1`, `agent-2`, ... so you can
see the workflow without inventing names or paths.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Stdlib only, Python 3.9+.

## License

MIT. See `LICENSE`.
