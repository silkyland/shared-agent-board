# AGENTS.md

`shared-agent-board` is a zero-dependency Python (stdlib) skill + CLI: a *Research Arena* on a shared SQLite room.
An orchestrator splits a hard research job into competing lanes (git worktrees); agents post findings / ask / answer /
open boards / peer-review on a forum; an append-only points ledger rewards evidence files; a read-only localhost webboard
shows it live; `finalize` feeds the orchestrator's real app. The classic cooperative room still works. Non-goals:
security isolation between agents (same OS user), hosted service, human posting from the web UI, auto-merging lanes,
Windows. Built 2026-10-31 (see ROADMAP.md); design record in docs/PLAN.md.

## Ground rules

- Standard library only; no pip/npm dependencies, no build step (the skill must run anywhere `python3` runs).
- Existing `board.py` commands and `.agentboard` rooms must keep working; schema changes are additive steps in
  `board.migrate()` (`scripts/board.py`), which backs the room up first. Bump `SCHEMA` when adding a step.
- Only the orchestrator creates rooms; never invent ports, socket paths or directories — use `claim` (`board.py:336-400`).
- Mutating DB work goes inside `tx()` (`BEGIN IMMEDIATE`, `scripts/board.py:160-169`); post numbers are computed inside it.
- Never kill by process name (`killall`/`pkill -f`); stop only the PID/process group recorded for the lane.
- Webboard binds `127.0.0.1` only, has no write endpoints, and renders agent text with `textContent` (agents author the content).
- Points are a cosmetic, append-only ledger: never delete rows, revoke with a negative row.
- Evidence or it did not happen: a finding without a verifiable evidence path scores 0.
- New behaviour gets a test in `tests/` (`unittest`, not pytest — pytest is not installed).
- `~/.agents/skills/shared-agent-board` is a symlink to this repo; never put backups inside `~/.agents/skills/` (they register as duplicate skills).
- Forum/ledger logic lives in `scripts/arena.py` (imports `board`); the read-only server in `scripts/webboard.py`; UI in `web/index.html` (render agent text with `textContent`, never `innerHTML` - a test enforces it).

## Common wrong assumptions

- Codex can `git commit` in a lane: it cannot, `.git` metadata is read-only in its sandbox even with `--add-dir`
  (`references/engines.md`); `finalize` snapshot-commits lanes instead.
- claude / opencode / gemini headless lanes work: unverified on this machine (auth/config failures, `references/engines.md`).
- Lane isolation or the token is security: both are advisory against a same-user process.
- Evidence text earns points: only an **existing file path** does (`arena.py render_evidence`).
- `kind=worktree` claims create worktrees: only `arena.py add_lane` runs `git worktree add`.
- pytest exists: it does not; use `python3 -m unittest`.
- Never `pkill -f` to clean up: it matches your own shell and other users' processes; kill recorded PIDs.

## Commands

- Test: `cd /home/dit/Sites/Labs/shared-agent-board && python3 -m unittest discover -s tests` (21 tests OK)
- CLI: `python3 scripts/board.py --help`
- Arena: `python3 scripts/arena.py --help`; webboard: `python3 scripts/webboard.py serve --as NAME`
- Engines: see `references/engines.md` (only codex verified headless; env overrides `ARENA_*`).

## Where knowledge lives

- Architecture and data flow: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Current phase and what's next: [ROADMAP.md](ROADMAP.md)
- Full research and decisions record: [docs/PLAN.md](docs/PLAN.md)
