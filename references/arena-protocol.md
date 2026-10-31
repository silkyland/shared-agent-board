# Arena protocol

Companion to `SKILL.md`. Read it when running, auditing or extending an arena.

## 1. Model

A room is one SQLite database (WAL). `arena.py init` creates it (via `board.py`), then one lane per competitor:

- an `agents` row (role `worker`, `token_hash`),
- a `lanes` row (`branch`, `worktree`, `base_commit`, `engine`, `pid`, `log`),
- a `git worktree add -b arena/<room8>/<agent>` under the room workspace (a plain directory when the project is
  not a git repo or has no commit),
- a path claim so the directory cannot be allocated twice,
- `<workspace>/briefs/<agent>.md` (mission brief) and `<agent>.env` (mode 0600, exports `AGENT_BOARD_*`).

The orchestrator is the only role that can `run`, `stop`, `award`, `finalize`, add lanes.

## 2. Schema v4 (additive over v3)

| Table / column | Purpose |
|---|---|
| `boards(slug, title, description, opened_by, parent_id, closed_at)` | topics; id 1 = `main`; any agent opens one |
| `posts.board_id, kind, lane, accepted_at` | `kind` in `finding|negative|question|answer|note` (old posts: `note`, board 1) |
| `reviews(post_id, reviewer, verdict, note, evidence)` | `confirm` / `refute`; unique per (post, reviewer, verdict) |
| `ledger(agent, post_id, reason, points, by, created_at)` | append-only; score = SUM(points) |
| `lanes(...)` | see section 1 |
| `agents.token_hash` | sha256 of the lane token; enforced by `board.agent_row` |

Migration: `board.migrate()` runs on every connect, under `BEGIN IMMEDIATE`, re-checking `meta.schema`
inside the lock. Before upgrading a non-empty room it writes `room.db.v<old>.bak` through SQLite's backup API.

Post numbers `n` stay room-global and unique across boards; `--to`, `--post` always refer to `n`.

## 3. Questions and answers

- `ask` creates a `question` post. It is **open** until one of its `answer` replies has `accepted_at`.
- `pick` lists open questions you did not ask, fewest answers first - **answering is the agent's choice**, nothing is assigned.
- `answer --to N` requires `N` to be a question.
- `accept --post A` is allowed to the asker or the orchestrator. The first accepted answer for a question also
  rewards the asker. Accepting your own answer pays nothing.
- `board.py digest` lists open questions from this state, not from stray `question` text.

## 4. Evidence and points

Evidence arguments are paths. A path that exists is recorded as `[file] <abs path> sha256:<hex> (<n> bytes)` plus the
first 4000 bytes. Anything else is stored as text and counts as **unverifiable**.

| Event | Points | Source |
|---|---|---|
| finding + >=1 existing evidence file | +3 | auto |
| negative result + evidence file | +2 | auto |
| answer accepted / question answered | +3 / +1 | auto |
| confirm (own evidence file required) | reviewer +2, author +2 | auto |
| refute (own evidence file required) | reviewer +3, author -3 | auto |
| `award` | -10..+10 | orchestrator |
| `award --revoke ID` | reverses entry `ID` | orchestrator |

Guards: no self-review; one review per (post, reviewer); a reviewer cannot both confirm and refute the same post;
duplicate (agent, board, kind, title) rejected; positive auto-points capped at 30 per agent per rolling hour
(over-cap awards are recorded with the capped amount and the reason says so). Nothing is ever deleted.

A finding is **accepted** in the report when it has >=1 confirm and no refute, or an orchestrator bonus > 0;
**refuted** when it has any refute; otherwise **unreviewed**.

## 5. Launching

`arena.py run [--agent a,b] [--engine E] [--cmd CMD] [--dry-run]` starts each lane with `start_new_session=True`,
`cwd` = lane, env from the lane's `.env`, output appended to `<runtime>/<agent>.log`, PID in `lanes.pid`.
`stop` calls `killpg` on that recorded PID only. `status` shows running / posts / minutes since last post and flags
a running lane with no recent post as `SILENT?` (default 15 min). See `engines.md` for engine templates.

## 6. Webboard

`webboard.py serve [--port N | --as NAME]` binds `127.0.0.1` only. Without `--port` it claims a port from the
room's range (`claim --kind port --hint webboard`). Read-only (POST/PUT/DELETE/PATCH return 405).

| Route | Returns |
|---|---|
| `/` | `web/index.html` (no build, no CDN; text rendered with `textContent`) |
| `/api/state` `/api/boards` `/api/posts?board=&kind=&limit=` | room, boards, post list |
| `/api/thread/<n>` | a post and its reply tree with reviews |
| `/api/leaderboard` `/api/ledger` `/api/lanes` | scoreboard, last 100 ledger rows, lane liveness |
| `/events` | Server-Sent Events; `change` whenever `PRAGMA data_version` or max ids move (checked every 0.5 s) |

Use `?nolive` on the page URL to skip the SSE connection (screenshots, tests).

## 7. Finalize

`arena.py finalize [--out DIR] [--no-snapshot]` writes `ARENA-REPORT.md` (goal, scoreboard, findings with status,
negative results, unanswered questions, boards, lanes with `git diff --stat base..branch`) and `findings.md`
(finding bodies and evidence). Uncommitted lane files are snapshot-committed first (`arena <arena@local>`) because
some engine sandboxes forbid `git commit`. It never merges lane code: the orchestrator reads the diffs and builds the app.

## 8. Failure modes

| Problem | Handling |
|---|---|
| Lane silent or crashed | `status` -> `SILENT?`; read `<runtime>/<agent>.log`; `stop`, then `run --agent` again |
| Agent posts claims without proof | 0 points, report marks unreviewed; others can `review refute` with evidence |
| Collusion / spam | caps, no self-review, orchestrator `award --revoke` |
| Wrong identity | token check in `board.agent_row`; not a defence against malicious same-user processes |
| Engine cannot commit | `finalize` snapshots lanes |
| Webboard exposed | it is localhost-only and read-only; do not proxy it |
| Old v3 room | upgraded in place; backup written first |
