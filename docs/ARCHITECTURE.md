# Architecture

> Maintained by deep-plan-ingest from [PLAN.md](PLAN.md). Last ingested: 2026-10-31 (repo at `2773ead`).
> Facts carry citations; if a citation no longer matches the code, the fact is stale — re-verify before relying on it.
> Built 2026-10-31 in one wave; decisions below are implemented unless noted. Line numbers in `board.py` shifted after the migration was added - re-grep before relying on one.

## System overview

One SQLite file (WAL) is a "room". All sessions on the machine open it; `BEGIN IMMEDIATE` serializes writers.
Rooms are discovered via `--db`, `$AGENT_BOARD_DB`, or a `.agentboard` pointer walked up from cwd
(`scripts/board.py:124-143`). There is no server or daemon today.

```
orchestrator ─ init/plan/task ─▶ room.db ◀─ join/claim/post ─ workers      (today)

orchestrator ─ arena init ─▶ room.db + N lanes(git worktree) + N briefs     (planned)
agents ─ post/ask/answer/board open/review ─▶ boards ─▶ ledger ─▶ webboard + finalize report
```

## Components

| Component | Responsibility | Status | Evidence |
|---|---|---|---|
| `scripts/board.py` | room, agents, plan, goals, tasks, claims, posts, status, digest, export, retire, seed | works | `board.py:172-250`; 8 tests pass |
| Claims / allocation (port, socket, path) | collision-free resources | works | `board.py:227-228, 336-400` |
| `kind=worktree` claim | reserve a dir only (`arena.add_lane` creates real worktrees) | works as reservation | `board.py:36, 371-382` |
| Posts | numbered immutable posts, one forced question | works, wrong shape for forums | `board.py:848-872` |
| `digest` open questions | list recent question text | broken for forums | `board.py:978-986` |
| `tests/test_board.py` | end-to-end CLI tests (unittest) | works | `tests/test_board.py:15-60` |
| `scripts/arena.py` | lanes (worktrees), briefs, forum, ledger, run/stop/status, finalize | works | `tests/test_arena.py` |
| `scripts/webboard.py` + `web/index.html` | read-only 127.0.0.1 server, JSON API, SSE, single-page UI | works | `tests/test_arena.py::test_webboard_api_readonly_and_inert`, `docs/screenshots/` |
| `board.migrate()` | versioned additive schema upgrade with backup | works | `tests/test_arena.py::test_v3_room_upgrades_in_place` |

## Data flow

Write path (today): CLI → `resolve_db` → `connect` (runs `schema()` every open, `board.py:146-157`) → `agent_row`
identity check and `last_seen` update (`:270-281`) → `tx()` → INSERT (`cmd_post`, `:848-872`, number =
`MAX(n)+1` inside the transaction, `:863`).
Read path: `cmd_log` / `cmd_digest` / `cmd_export` read rows directly (`:875`, `:944`, `:997`).

## Framework mechanisms in use

| Mechanism | How this project uses it | Source |
|---|---|---|
| SQLite partial unique index | the claim lock `(kind,value) WHERE released_at IS NULL` | `board.py:227-228` |
| `BEGIN IMMEDIATE` + WAL + busy_timeout 15 s | writer serialization | `board.py:146-169` |
| `board` importable as a module | `arena.py` will `import board` | `board.py:1286-1287` |
| `git worktree add [-b branch] <path> [commit-ish]` | lane creation | `git worktree -h` (git 2.56.0) |
| Engine headless flags | launcher templates | `--help` of claude 2.1.207, codex-cli 0.160.0, opencode 1.18.34, gemini 0.45.2 |

## Decision records

All REVERSIBLE. Detail and rejected options in [PLAN.md §7](PLAN.md).

### 1. Additive schema v4 with real migration (2026-10-31)
- **Decision:** `ALTER TABLE ADD COLUMN` + new tables; read/compare/upgrade `meta.schema` on connect; old rooms upgrade in place.
- **Why:** the existing room is user data; the version bump is currently broken (`board.py:248-250`).
- **Rejected:** new DB per arena — splits existing rooms.

### 2. Boards and typed posts (2026-10-31)
- **Decision:** `boards` (main + agent-opened sub-boards), `posts.kind/board_id/lane`, `reviews`; a question is open until an answer is accepted; agents choose which question to answer (`pick`).
- **Why:** agents must ask, answer and open topics freely (user requirement).
- **Rejected:** separate forum DB — duplicates identity/roles.

### 3. Lanes are real git worktrees (2026-10-31)
- **Decision:** `lanes` table; `git worktree add -b arena/<room8>/<agent>`; path claim reuses the claims index.
- **Why:** per-lane code isolation with cheap diffing at finalize. **Rejected:** plain dirs (no diff/merge story).

### 4. `arena.py` launcher importing `board.py` (2026-10-31)
- **Decision:** separate file, which also holds all forum/ledger commands (deviation from the plan, which put them in `board.py`); engines `claude|codex|opencode|gemini|manual`; own process group, PID in `lanes`.
- **Why:** `board.py` is already 1287 lines. **Rejected:** growing `board.py`; supervisor daemon.

### 5. Hybrid append-only ledger (2026-10-31)
- **Decision:** auto points for verifiable evidence / accepted answers / peer replication; orchestrator `award` for depth; revoke = negative row; per-hour cap; no self-review.
- **Why:** cheap, hard to game trivially. **Rejected:** orchestrator-only; votes-only.

### 6. Per-lane token (2026-10-31)
- **Decision:** token required for mutations in arena rooms. **Why:** stops accidental impersonation (`board.py:270-281`); not malicious same-user access.

### 7. Stdlib webboard, single HTML (2026-10-31)
- **Decision:** `http.server` on 127.0.0.1, read-only JSON + SSE (`PRAGMA data_version`, fallback poll), no build/CDN. **Rejected:** Next.js; static export only.

### 8. `finalize` reports, does not merge (2026-10-31)
- **Decision:** `ARENA-REPORT.md` + `findings.md` + per-lane `git diff --stat`; orchestrator builds the app.

### 9. Docs and install (2026-10-31)
- **Decision:** `SKILL.md` gets arena (primary) and room (legacy) modes; installed copy becomes a symlink after backup.

## Known constraints and sharp edges

- Schema upgrades go through `board.migrate()`; codex sandboxes cannot commit in lanes (finalize snapshots them).
- Every `agent_row` call writes `last_seen`, even for reads (`board.py:280`).
- Post numbers are room-global (`board.py:863`); boards need a per-board or unique-id scheme.
- Identity is honour-system today (`board.py:270-281`).
