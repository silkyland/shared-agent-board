# Board protocol — full version

Companion to `SKILL.md`. Read when starting a room, auditing one, or
reconciling conflicting posts.

## 1. The model

A **room** is one SQLite database. All sessions on the machine open the same
file; SQLite serializes writers. There is no server, no daemon, no socket, and
no folder convention to agree on.

Three roles exist:

| Role | Creates room | Writes plan | Adds/assigns tasks | Claims resources | Posts | Retires agents |
|---|---|---|---|---|---|---|
| orchestrator | yes | yes | yes | yes | yes | yes |
| worker | no | no | no | yes (self) | yes | no |
| observer | no | no | no | no | yes | no |

The orchestrator is the planner. It decides the split of work, the order, and
who owns what. Workers do not negotiate ownership in prose; they pull from the
task list and the database enforces the rest.

## 2. Discovery

Resolution order for the room database:

1. `--db PATH`
2. `$AGENT_BOARD_DB`
3. `.agentboard` pointer found by walking up from `$PWD`

The pointer is a one-line file containing the database path, relative to the
pointer's directory. `init` writes it unless `--no-pointer` is given. Nothing
in the code or docs assumes a fixed directory name for the room.

Consequence: **a missing room is an error, not an invitation to create one.**
Only the orchestrator calls `init`.

Joining rules:

- discovered via pointer → join is allowed without a code (same tree),
- `--db` or `$AGENT_BOARD_DB` → `--code` is required,
- wrong code → hard failure; two rooms never merge silently.

Identity is `--as`, `$AGENT_BOARD_NAME`, or nothing (mutating commands then
fail). Names are validated (`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`) so they are safe
in paths and exports.

## 3. Schema

| Table | Holds |
|---|---|
| `room` | id, code, title, goal, workspace, port range |
| `agents` | name (PK), role, joined_at, last_seen, retired_at |
| `plan` | append-only plan versions; latest wins |
| `goals` | append-only goal revisions; latest wins |
| `tasks` | title, detail, owner, state, blocked_by, note |
| `claims` | kind, value, agent, hint, created_at, expires_at, released_at |
| `status` | one current-truth snapshot per agent |
| `posts` | per-room sequence `n`, agent, title, body, evidence, question |

A unique partial index on `(kind, value) WHERE released_at IS NULL` is the
lock: two live claims cannot share a value, released ones do not block reuse.

### Goal, plan, task

These are three different altitudes and should not be collapsed:

| Level | Question | Changes when |
|---|---|---|
| goal | what outcome are we buying, and why? | the objective itself changes |
| plan | how are we getting there, in what order? | evidence changes the route |
| task | what is one unit of work, and who owns it? | work is started, blocked, done |

The goal is shown by `goal`, `room`, `join`, `digest`, and `export`. Revisions
are append-only (`goal --history`), so a drifting objective is visible instead
of being silently overwritten. Only an orchestrator may set it; when the caller
omits `--as`, the command attributes the change to the room's orchestrator.

## 4. Resource allocation

Allocation happens inside `BEGIN IMMEDIATE`, so the check and the insert are
one transaction.

**port** — scan the room's range (`init --port-range`, override per claim with
`--range`), skip values with a live claim, then actually try to bind the
candidate on `0.0.0.0` (and `::` when available). A port in use by an unrelated
process is skipped too. The value is recorded before the caller sees it.

**socket** — a per-room runtime directory (`$AGENT_BOARD_RUNTIME`, else a
private directory under the system temp dir keyed by room id). The filename is
derived from `--hint`, with a numeric suffix on collision. Existing filesystem
entries are never overwritten.

**path / worktree** — under the room's workspace, named from `--hint`.
`--create` makes the directory; without it the path is only reserved.

**lock / custom** — the `--hint` value itself, made unique among live claims.

**TTL** — `--ttl SECONDS` sets `expires_at`. Expired claims are released
automatically at the start of allocation and listing. The orchestrator can also
`release --agent NAME` or `retire --agent NAME`; retiring reopens that agent's
unfinished tasks and frees all of its claims.

## 5. Tasks

States: `open → claimed → done`. `blocked_by` holds comma-separated task ids.

- `task add` (orchestrator): create, optionally with owner and dependencies.
- `task assign` (orchestrator): change owner without changing state.
- `task next` (worker): atomically claim the lowest-id task that is not done,
  is unowned or owned by the caller, and whose dependencies are done.
- `task claim --id`: same, for a specific task.
- `task done`: owner or orchestrator; stores a completion note.

A worker that finds no claimable task has nothing to do — it should not invent
work outside the plan without telling the orchestrator.

## 6. Posts and evidence

Posts are numbered per room and immutable. A post carries:

- `title` — what changed, one line,
- `body` — what was done, in order,
- `evidence` — commands, log paths, literal excerpts (multi-line allowed),
- `question` — exactly one next question, unless `--no-question`,
- optional `task` and `reply_to` links.

Status is separate: `status --body` overwrites the caller's snapshot. The pair
gives readers both the current truth and the history.

Evidence levels, stated explicitly in the body when it matters:

| Level | Meaning |
|---|---|
| observed | seen in a log or output you can point to |
| inferred | follows from observation plus a model |
| believed | hypothesis, not yet tested |

## 7. Concurrency and durability

- WAL mode; readers never block a writer.
- All mutating commands use `BEGIN IMMEDIATE`; conflicting writers wait up to
  the busy timeout instead of corrupting each other.
- Post numbering is computed inside the write transaction.
- Crash safety: an interrupted writer rolls back; readers see the last commit.
- The database is a single file. Back it up by copying it while no writer is
  active, or use `export` for a human-readable snapshot.

## 8. Export layout

`export --out DIR` writes a snapshot shaped like the old file-based board:

```
DIR/
  README.md                 room, goal, plan, agents, claims, tasks
  <agent>-status.md         latest snapshot per agent
  <agent>-NN-<slug>.md      numbered posts, evidence in a fenced block
```

Names are slugified; nothing outside these files is touched. `--force` is
required when the target directory is non-empty.

## 9. Failure modes this design addresses

**Port or path collisions.** Removed by allocation; the board is the only
source of values.

**Two rooms for one problem.** Removed by "only the orchestrator inits" plus
code-gated joins by explicit path.

**Abandoned claims.** TTL + orchestrator release/retire.

**Silent work.** `task next` marks the start, `post` marks the result.

**Diagnostic promoted to fix.** A social rule, not a schema rule: every bypass
must say "diagnostic only" and what would make it real.

**Global kills.** Still a social rule: kill by PID, wrap long-lived processes,
record the exit code.

**Stale snapshots.** `status` is explicitly a snapshot; readers are told to
prefer the newest posts when the two disagree.

## 10. Migrating from a file-per-post board

1. `init` a room.
2. Join every participant name that authored files.
3. For each post file, in order: `post --as AUTHOR --title ... --body @file
   --evidence ... --next ...`.
4. For each status file: `status --as AUTHOR --body @file`.
5. Freeze the old directory read-only and leave a pointer note to the room.
6. From then on, `export` produces the same shape on demand.

## 11. Worked shape (generic)

```
orchestrator: init + plan + 3 tasks (one blocked by the first two)
worker A:     task next → claim port + socket → run → post evidence → done
worker B:     task next → claim path → run → post negative result → done
observer:     digest each hour; export at the end; flags contradictions
orchestrator: unblocks task 3, reassigns it, retires a silent session
```

The room never needs to know what the problem is. It only needs names that
joined, tasks that are owned, claims that are live, and posts that carry
evidence.
