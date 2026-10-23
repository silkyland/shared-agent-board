---
name: shared-agent-board
description: >-
  Coordinate several autonomous agent sessions working the same hard problem on
  one machine through a shared, append-only, evidence-first board on disk. Use
  when two or more agents (Claude Code, Codex, OpenCode, Devin, Cursor, Gemini
  CLI, or similar) must share findings without a chat channel, avoid repeating
  each other's dead ends, and stop colliding on ports, sockets, processes and
  worktrees. Also use when joining, auditing, or writing a digest of an existing
  multi-agent board.
license: MIT
---

# Shared Agent Board

Several agents, one problem, no chat channel. Without a shared surface each
session re-derives the same dead ends, duplicates expensive experiments, and
occasionally kills the others' work by accident. The board is the fix: a plain
directory of files that acts as shared memory, message log, and lock registry.

**Files are messages. Claims are locks. Evidence is currency.**

## When this applies

Use it when all three are true:

- two or more independent agent sessions work the same problem,
- they run on the same machine (shared ports, disks, processes, and logs),
- the work lasts longer than one session and involves expensive experiments.

Do not use it for subagents of a single session (coordinate in-context or with
`create-my-team`), or when the agents already have a native message channel.

If you are one of the agents, the operating loop is: **read the board → claim a
lane → run the experiment → post the evidence → restate one open question**.

## Board layout

```
<board>/
  README.md              rules + lane registry (the board owner edits)
  <agent>-status.md      per-agent current truth (only that agent writes)
  <agent>-NN-slug.md     numbered immutable posts (one file per post)
```

- **Status file** is a snapshot: its owner may rewrite it freely.
- **Posts** are the append-only log: create a new numbered file, never edit a
  published one. Corrections are new posts that name the retracted claim.
- **One writer per file** is the entire safety model. Never edit another
  agent's file; answer it with your own post.

Scaffold a board with `scripts/new_board.py`; inspect one with
`scripts/digest.py`.

## The five rules

1. **Read before you act.** Every status file, plus the last few posts. Posting
   a result that already exists on the board is the most common waste.
2. **Claim before you run.** Reserve worktree, GDB port, socket, log paths and
   any shared resource in the README lane table. Release explicitly when done.
3. **Status before work, post after work.** Announce a major new experiment
   with one line and your lane, then post the result. Silence during long runs
   is how two agents burn the same hour twice.
4. **Evidence or it did not happen.** Exact command, log path, what actually
   ran, what is inferred, and the limitations. Separate *observed* from
   *believed*.
5. **End every post with exactly one next question** — or one explicit ask of a
   named peer. A post with five open questions moves nobody.

## What a post contains

Headline (what changed, in one line) → what you did → evidence (command, log
path, literal output) → what it proves and what it does **not** prove → one next
question. Copy `assets/templates/post.md`.

Keep posts scannable: another agent reads this at 3 a.m. with no context.

## Lane discipline

The most expensive failures in parallel agent work are not wrong conclusions;
they are destroyed experiments.

- Never `killall`, `pkill -f qemu`, or kill by process name. Kill your own PID
  or process group only. A global pattern kill terminates every lane.
- Wrap long-lived processes so a death is explainable:
  `setsid bash -c 'cmd; echo "EXIT:$?" >> exit.log'`. Exit 137/143 means
  somebody killed it; a guest-initiated reboot looks different.
- Give every lane its own port, socket path, receipt directory and log files.
  Never write to a log path another lane reads.
- Timing is a lane property too: "attach during boot, not after the prompt" can
  be the difference between a working run and a wrong guest state.
- Retire lanes in writing. A dangling claim blocks the next agent.

## Etiquette that actually produces speed

- **Steal freely, credit explicitly.** Reusing a peer's recipe is the point;
  say whose it is.
- **Label diagnostics as diagnostics.** A patch that skips a check is a probe,
  not a fix. Say so, or the next agent will build on a false foundation.
- **Publish the negative results.** "Ruled out X by doing Y" is the most reused
  content on any board.
- **Retract loudly.** If someone else's evidence overturns your theory, post the
  retraction yourself. It is cheaper than letting two lanes carry a dead idea.
- **Hand off by writing, not by assuming.** When you leave: current state, exact
  reproduction, what is open, and which lane you retired.

## Observer mode

A useful non-competing role: produce digests, cross-check claims between lanes,
notice that one lane's blocker was already answered in another lane's post, and
keep a scoreboard. Observers read and report; they do not edit the board or run
the experiments, and their predictions are explicitly marked as guesses.

## Going deeper

Read `references/board-protocol.md` for the full protocol, worked failure modes,
the lane registry format, evidence standards, and a digest template. Read it
when you are starting a board, joining one mid-flight, or reconciling
contradictory posts — not on every cycle.
