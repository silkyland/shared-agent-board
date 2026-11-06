# deep-plan state

phase: 7; exit criteria passed: yes
tier: Standard (cross-module protocol redesign; additive, reversible rollout; no destructive changes)
task: Redesign shared-agent-board around the current host agent's native subagents, a constructor-created goal with multiple self-selected ways, and observable cross-way evidence exchange. Produce an executable plan only.
inputs: root=/home/dit/Sites/Labs/shared-agent-board; ground-truth=local Python/git source, repo tests, current host tool contracts; plan=docs/NATIVE-ARENA-PLAN.md

## Frame
- Deliverable: research-backed redesign, protocol/schema/CLI contracts, phased implementation and verification, three fresh-agent gates.
- Preserve stdlib, old commands/rooms, read-only localhost UI, append-only ledger, claim allocation, transaction discipline.
- Non-goals: implementation now, hosting/security isolation, provider authentication, automatically merging lanes, implementing model APIs.
- User-confirmed framing: constructor=host orchestrator; multiple agents may select/switch ways with provenance. Remaining defaults: retain explicit legacy launch commands but remove them from normal workflow; native unavailable => report limitation, never silently launch external CLI. These are assumed (framing default).
- Scale UNVERIFIED: design tests use bounded local arenas; no production-size assertions.

## Research Questions
| RQ | Question | Status | Answer + citation |
|---|---|---|---|
| 1 | How are rooms, goals, lanes and native/manual/CLI launches wired? | open | |
| 2 | Why can posting/answering be omitted despite the existing forum? | open | |
| 3 | What models and contracts support selectable ways, provenance and revisions? | open | |
| 4 | Which stdlib/host mechanisms actually exist, and what cannot Python invoke? | open | |
| 5 | Which migrations, compatibility and scoring/report invariants must survive? | open | |
| 6 | What tests, history and operational evidence establish the baseline? | open | |

## Corrections ledger
Pending research; do not invent corrections.

## Gaps and decisions
Pending phases 4–5.

## Goldfish log
| Gate | Round | Verdict | Findings and triage |
|---|---|---|---|

## Phase 1 exit — passed
Inventory: board room/goals/claims and arena forum/ledger/worktrees work; native handoff and prompt-only interaction half-wired; no relevant dead executable component established. Write CLI -> session/connect/migrate -> identity -> tx -> posts/ledger; read feed/API -> SQL -> JSON/textContent, web SSE refresh. Sources: scripts/arena.py:231-276,370-451,588-686; scripts/board.py:148-171,257-340; scripts/webboard.py:38-108. Git HEAD a33d6e0; 10 commits from classic room to arena/webboard/docs, real dogfood remains TODO.

## Phase 2 exit — passed
VERIFIED runtime: Python 3.14.9, SQLite 3.54.0, git 2.56.0; local sqlite3 docs /usr/lib/python3.14/sqlite3/__init__.py:23-52; transaction and backup mechanisms scripts/board.py:148-171,257-340; subprocess Popen /usr/lib/python3.14/subprocess.py:759,817. Current session exposes collaboration.spawn_agent, send_message, followup_task, wait_agent, interrupt_agent; two read-only scout spawns succeeded (2026-11-06 session observation). Child agents share cwd/filesystem and cannot assume lane cwd. Official rolling subagent documentation fetched 2026-11-06: https://learn.chatgpt.com/docs/agent-configuration/subagents?surface=app (orchestration section). No Python-to-host spawn API established; plan must put tool calls in SKILL instructions. Other host tool names/cwd/lifecycle support UNVERIFIED => native capability spike.

## Phase 3 exit — passed
Audit: pull feed --since picks newest limit (arena.py:433-438), risks skipping backlog if used as cursor; brief answer example misses required --title (190 vs 409); no persisted native orchestration lifecycle, read receipt/checkpoint, way association or source links (board.py:201-206,289-328; arena.py:370-415,611-686); --next is not typed question (399-401,454-459). Exact cause of user's live silence UNVERIFIED, no active room evidence => M1 native dogfood spike. Current 21 unittest tests passed in 5.277s, ResourceWarnings from test/server cleanup observed; runtime Python 3.9 support UNVERIFIED => compatibility spike.
Corrections: initial hypothesis external executables are mandatory -> default is manual (arena.py:788); native is possible by brief handoff, not automated. Initial hypothesis no forum -> Q&A/review already implemented (arena.py:454-526). Docs digest broken -> implemented typed accepted-answer filter (board.py:1073-1089). Webboard no writes -> HTTP read-only but startup allocates claim (webboard.py:177-185).
RQ1 answered arena.py:218-304,588-649. RQ2 answered as structural gap (207-214,370-415,426-476), live root cause UNVERIFIED. RQ3 answered board.py:201-206,289-328. RQ4 answered host contracts + official docs + local sources above, cross-host portability UNVERIFIED. RQ5 answered board.py:257-340 and webboard.py:28-31,127-147. RQ6 answered tests baseline/history, actual scale and Python3.9 UNVERIFIED.

## Phase 4 exit — passed
1 MISSING native host dispatch contract; 2 MISSING selectable ways/revision-bound membership; 3 MISSING enforced round/checkpoint feedback protocol; 4 BROKEN backlog cursor and unusable brief answer example; 5 MISSING provenance links without point farming; 6 MISSING native liveness/budget/recovery; 7 MISSING goal/way comparative synthesis + observer views; 8 BROKEN stale architecture documentation (citations above). All evidence comes from Phase 1–3; no runtime diagnosis inferred.
phase 4 exit criteria passed: yes.

## Phase 5 exit — passed
User confirmed constructor and shared/switchable ways; user confirmed peer consideration with answer/use/review or reasoned non-applicability, not mandatory answer quota. Decision Brief presented 10 concrete reversible choices. Added user-requested Markdown as RQ7/Gap9; reviewed web/index.html and impeccable scoped UI context, no UI edits. D1–D9 in docs/NATIVE-ARENA-PLAN.md map one decision per gap; all REVERSIBLE, opt-in additive rollout, no ONE-WAY decisions. Research extension: urllib.parse local source read; Markdown proposed AST preserves safe text sinks. Phase 4 extended for RQ7 before design finalized.

## Phase 6 exit — passed
Plan written docs/NATIVE-ARENA-PLAN.md with ten required sections; M0 correctness; M1 native walking skeleton; M2 provenance/recovery; M3 observer/Markdown/synthesis; M4 compatibility/docs rollout. Unknown host behavior/live silence/scale => M1-T0; Python3.9 => M4-T0. No executable project changes. Goldfish header NOT RUN pending 7a/gates.

## Phase 7a self-check
Evidence items: YES (citations, runtime-scoped tool claims, unknowns/spikes). Research items: YES (corrections/RQs/inventory/write+read). Decisions: YES (one D per gap, all REVERSIBLE). Roadmap: YES (M0 shippable fixes, M1 E2E skeleton, spikes first, IDs/files/commands). Verification: YES (automated + explicit manual steps). G1/G2/G3: pending. Before gate fixed self-review bootstrap: M1-T0 uses existing manual-mode room, not future native commands; removed contradictory acceptance-watermark prose.

| G1 | 1 | FAIL | Accepted 6 ambiguities: spike artifacts/race simulation; retry/budget; run partial successes; mutation states; command availability; dependent tests. Three citations held. Plan corrected, requires fresh r2. |

| G1 | 2 | FAIL, corrected pending revalidation | Accepted both: ancestry-wide peer consideration and receipt-producing inbox lifecycle. Corrected plan; 3 citations held. Two-round cap reached; request user choice before an extra fresh G1 round. G2/G3 not run yet. |

User override (2026-11-06): explicitly authorized continuing until all gates pass even beyond skill round cap. Applies to remaining goldfish rounds; no need to request again. Extra G1 r3 fresh agent dispatched with only prescribed prompt/plan path.

| G1 | 3 (user override) | PASS | Every M0/M1 task PASS, zero blocking questions; 3/3 source citations held. Fresh G2 critic dispatched. |

| G2 | 1 | FAIL, corrected | Accepted all 3: M2->M3 backwards dependency; atomic mutation guards; staged docs schedule. Added same-tx guard/interleaving tests and split manual acceptance by milestone. Planner also specified review_id output, git subprocess allowance and Markdown containers/navigation. Fresh critic r2 required. |

| G2 | 2 | FAIL, corrected | Accepted 2 majors: initial inbox/choice guard deadlock and concurrent prepare vs finalize race. Guard prerequisites now command-specific; native prepare/finalize share stable claimed POSIX file lock, lock-before-tx ordering and tests. User already authorized extra rounds. |
Research extension for new D6 mechanism: Python3.14.9 fcntl binary path/doc/constants inspected; separate-process probe in TemporaryDirectory verified contention (exit73), unlock (exit0), and release after process exit (exit0). B.allocate create=False path contract read board.py:431-495; proposed native init claims exact lock path and stores meta.native_control_lock. No runtime project files changed.

| G2 | 3 | FAIL, corrected | Accepted inbox prerequisite wording; native-vs-historical archive guarantee; existing brief permission claim. Corrected explicit before/after-choice reads, unarchived-source labels, native private artifact mode0600 + permissive-umask test. |

| G2 | 4 | FAIL, corrected | Accepted single major: native artifact permission protection must start at init/add_lane, not prepare. Added first-write strict modes, explicit add_lane scope and pre-prepare/lane-add tests. No additional blockers/majors reported. |

| G2 | 5 | PASS (nitpick corrected) | No blockers/majors. Accepted idle_seconds nitpick: explicit default300, first deadline from prepared_at, next from last_reminder_at; no implicit reset on posts. Fresh r6 confirms final correction before G3. |

| G3 | 1 | PASS | Fresh agent answered GO, zero blocking questions: M0/M1 behavior, storage, permissions, lifecycle, pagination, checkpoint validation, compatibility and acceptance criteria sufficient. G3 began after G2 r5 had already passed with no blockers/majors; r6 is final confirmation of nitpick fix. |
