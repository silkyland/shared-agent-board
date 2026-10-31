# Roadmap

> Source of truth for sequencing. Detail lives in [docs/PLAN.md](docs/PLAN.md);
> architecture in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Last updated: 2026-10-31.

## Status

| Phase | Name | Status | Effort | Depends on |
|-------|------|--------|--------|------------|
| 0 | Foundation correctness (schema migration) | done | S | - |
| 1 | Walking skeleton | done | M | 0 |
| 2 | The league: ledger rules, launcher, token | done | M | 1 |
| 3 | Webboard complete + finalize | done | M | 2 |
| 4 | Docs, skill rewrite, install symlink | done | S | 3 |
| 5 | Dogfood on a real research job | todo | M | 4 |

All of phases 0-4 were built in one wave on 2026-10-31 (uncommitted at the time of writing).
Evidence: `python3 -m unittest discover -s tests` -> 21 tests OK (8 original + 13 in `tests/test_arena.py`);
screenshots in `docs/screenshots/`; engine spike in `references/engines.md`.

## Phase details

### Phase 0: Foundation correctness - done
- **Exit criteria:** suite passes including `test_v3_room_upgrades_in_place`. Met.
- [x] `scripts/board.py` `migrate()` (versioned, in `BEGIN IMMEDIATE`, backup via SQLite backup API)
- [x] `tests/test_arena.py::test_v3_room_upgrades_in_place`

### Phase 1: Walking skeleton - done
- [x] Spike S2 (engines) recorded in `references/engines.md`; S3 (`PRAGMA data_version` across processes) confirmed by SSE `change` events firing on CLI posts
- [x] `boards`, `posts.kind/board_id/lane`, `reviews`, `ledger`, `lanes`; commands `boards`, `post`, `ask`, `answer`, `accept`, `pick`, `review`, `feed`
- [x] `scripts/arena.py`: `init`, `lane`, `brief`, `score`
- [x] `scripts/webboard.py` + `web/index.html`
- Notes (2026-10-31): forum commands live in `arena.py`, not `board.py` as the plan said, to keep `board.py` from growing; `board.py` keeps migration, token check and the digest fix.

### Phase 2: The league - done
- [x] ledger rules, hourly cap, `award` / `award --revoke`; token enforced in `board.agent_row`
- [x] `arena.py run|stop|status` (codex verified; others documented unverified)
- [x] `digest` open questions use question state
- [x] tests: self-review, duplicate review, cap, `stop` signals only the recorded group

### Phase 3: Webboard complete + finalize - done
- [x] sub-boards, thread detail, lanes, SSE; `<script>` post renders inert (screenshot `docs/screenshots/webboard-thread.png`, API test)
- [x] `arena finalize`: report equals `score` (tested); snapshot-commits lane files first (codex cannot commit)
- [x] UI checked at 1400px and 420px
- Notes: `impeccable` skill was read for its craft floor, not run via its launcher.

### Phase 4: Docs, skill, install - done
- [x] `SKILL.md`, `README.md`, `references/arena-protocol.md`, `references/engines.md`, note in `references/board-protocol.md`
- [x] `~/.agents/skills/shared-agent-board` is now a symlink to this repo; old copy at `~/.agents/backups/shared-agent-board-v3`

### Phase 5: Dogfood - todo
- **Objective:** real 3-lane arena on a user-chosen target; orchestrator builds the app and publishes the table.
- **Exit criteria:** user confirms the loop worked.

## Open questions

- [ ] Do claude, opencode and gemini run headless lanes? - re-run the S2 spike after fixing auth (`claude` login, a valid opencode model/key via `ARENA_OPENCODE_MODEL`, gemini account setup)
- [ ] Does the code run on Python 3.9 as `board.py` claims? - run the suite on 3.9 (only 3.14.8 tested)
- [ ] Can a codex lane be allowed to commit? - `.git` stays read-only under `workspace-write` even with `--add-dir`; try a different sandbox mode deliberately

## Risks

| Risk | Likelihood | Impact | Mitigation / rollback |
|------|-----------|--------|----------------------|
| Headless engines blocked by auth/sandbox; lanes idle | high | high | `arena status` flags `SILENT?`; `manual`/`custom` engines always work |
| Lanes read/copy each other (advisory isolation) | medium | medium | stated in brief and README; evidence must be files; replication rewards |
| Points gamed / impersonation | medium | medium | evidence files, caps, no self-review, token, revoke rows |
| Migration corrupts a live room | low | high | tested on a v3 fixture; `.v3.bak` written first |
| Webboard leaks research beyond localhost | low | high | 127.0.0.1 only, read-only |
