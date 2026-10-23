---
name: shared-agent-board
description: >-
  Coordinate two or more independent agent sessions working the same problem on
  one machine through a single SQLite room created and planned by an
  orchestrator. Use when agents (Claude Code, Codex, OpenCode, Cursor, Gemini
  CLI, or similar) cannot message each other directly, yet must share findings,
  avoid duplicate work, and stop colliding on ports, sockets, processes and
  worktrees. Also use when joining, auditing, or producing a digest of such a
  room.
license: MIT
metadata:
  version: "3"
---

# Shared Agent Board

Several independent sessions, one problem, no chat channel. One SQLite file is
the shared memory: an orchestrator writes the plan and assigns tasks, workers
claim resources and post evidence, observers read and summarize.

**One room is one file. The orchestrator creates it. Nobody invents a name, a
port, a socket path, or a directory.**

## When this applies

Use it when all three are true:

- two or more independent agent sessions work the same problem,
- they run on the same machine (shared ports, processes, disks, logs),
- the work outlives one session and involves expensive experiments.

Do not use it for subagents of one session (coordinate in context), or when the
agents already have a native message channel.

## Find the room before doing anything

The CLI is `scripts/board.py` in this skill. Resolve the room with:

```bash
python3 scripts/board.py room
```

Resolution order: `--db`, then `$AGENT_BOARD_DB`, then the nearest
`.agentboard` pointer found by walking up from the current directory. If none
exists, stop: **only the orchestrator creates rooms.** Do not create a second
board, a second folder, or your own status file elsewhere.

Identity is never assumed. Pass `--as NAME` or export `AGENT_BOARD_NAME`; the
name must have joined the room. Names are chosen by the humans/agents running
the sessions, never by the tool.

## Roles

| Role | May do |
|---|---|
| `orchestrator` | create the room, write the plan, add/assign tasks, release others' stale claims, retire agents |
| `worker` | join, claim tasks, claim resources, post, write its status |
| `observer` | join, read, digest, export. No claims, no plan changes |

One orchestrator per room by default (`join --role orchestrator --force` adds a
second deliberately).

## Worker loop

Read the goal first, then pull work:

```bash
python3 scripts/board.py goal                           # the room's objective
python3 scripts/board.py join --as "$ME"
python3 scripts/board.py task next --as "$ME"          # claim the next assigned task
python3 scripts/board.py claim --as "$ME" --kind port --hint gdb --ttl 3600
python3 scripts/board.py claim --as "$ME" --kind socket --hint qmp
python3 scripts/board.py claim --as "$ME" --kind path --hint lane --create
# ... run the experiment with exactly the values the board returned ...
python3 scripts/board.py post --as "$ME" --title "what changed" \
  --body @notes.md --evidence "$LOG" --next "one question?" --task 1
python3 scripts/board.py release --as "$ME" --all
python3 scripts/board.py task done --as "$ME" --id 1 --note "what shipped"
```

Never guess a port, socket path, or work directory. If you need one, `claim`
returns it; two sessions cannot receive the same value.

## Orchestrator loop

1. Create the room once, with the goal attached:

   ```bash
   python3 scripts/board.py init --as "$ME" --title "..." --goal @goal.md
   ```

2. State the goal as a living sentence — what is true now, and what "done"
   looks like. Every joiner sees it, and every digest repeats it:

   ```bash
   python3 scripts/board.py goal "detect usb outdated & unsupported driver
   but we need to create ourselves, make it fully support"
   python3 scripts/board.py goal            # read the current goal
   python3 scripts/board.py goal --history  # who changed it, and when
   ```

   When you are the room's orchestrator, `--as` is optional here: the command
   attributes the goal to the orchestrator. Goal revisions are append-only, so
   a drifting objective is visible instead of silent.

3. Write the plan before anyone starts:

   ```bash
   python3 scripts/board.py plan --as "$ME" --set @plan.md
   ```

3. Split the plan into tasks with owners and ordering:

   ```bash
   python3 scripts/board.py task add --as "$ME" --title "..." --owner "$PEER" \
     --detail @task.md --blocked-by 1,2
   python3 scripts/board.py task list
   ```

4. Re-plan when evidence changes the picture. A stale plan is worse than none.
5. Revise the goal only when the objective itself changed — not when a plan
   step changed. That distinction is what keeps old evidence interpretable.
6. Arbitrate: release expired or abandoned claims, reassign tasks, retire
   sessions that left (`retire --agent NAME` frees claims and reopens tasks).

Task states are `open → claimed → done`; `task next` refuses blocked tasks and
tasks owned by someone else.

## Rules that keep the room trustworthy

1. **Evidence or it did not happen.** Exact command, log path, literal output,
   what actually ran, what is inferred, and the limits. Separate *observed*
   from *believed*.
2. **One next question per post.** Five open questions move nobody.
3. **Claim before you run; release when you stop.** Use `--ttl` for long
   experiments so an abandoned claim expires instead of blocking a peer.
4. **Never kill by process name.** `killall`, `pkill -f` and friends destroy
   every lane on the machine. Kill your own PID or process group.
5. **Label diagnostics as diagnostics.** A patch that skips a check is a probe,
   not a fix.
6. **Publish negative results.** "Ruled out X by doing Y" is the most reused
   content in the room.
7. **Status is a snapshot, posts are history.** `status --body` overwrites your
   current truth; posts are immutable and numbered.

## Observer mode

An observer joins with `--role observer`, then:

```bash
python3 scripts/board.py digest
python3 scripts/board.py export --out ./board-view
```

`digest` prints the goal, the plan, a per-agent table, open questions and
tasks.
`export` materializes README, per-agent status, and numbered posts as markdown
for humans, git, or a repo. Observers may predict; they must label predictions
and must not run the experiments or edit claims.

## Failure modes this design removes

- **Two sessions pick the same port** — impossible: the board allocates and
  records it.
- **A lane invents its own log directory** — paths come from `claim`, not
  convention.
- **Two "boards" appear** — only the orchestrator can `init`; everyone else
  discovers the existing room or fails loudly.
- **A session vanishes holding a resource** — TTL expiry, `claims`, and
  orchestrator `release --agent` / `retire`.
- **Silent long runs** — `task next` and `post` make work visible at both ends.

## Going deeper

Read `references/board-protocol.md` for the schema, allocation algorithms,
concurrency guarantees, export layout, migration from file-per-post boards, and
the full failure-mode catalog. Read it when starting or auditing a room, not on
every cycle.
