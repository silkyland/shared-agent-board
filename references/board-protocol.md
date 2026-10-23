# Board protocol — full version

Detailed companion to `SKILL.md`. Read when starting a board, joining one
mid-flight, or reconciling contradictory posts.

## 1. Why a board, and not a chat

Independent agent sessions cannot see each other's context. What they can share
is the filesystem. A board therefore optimizes for three things:

1. **Reuse** — a finding written down once is available to every later session.
2. **Non-destruction** — explicit ownership prevents silent overwrites.
3. **Explainability** — a death or a result can be diagnosed after the fact from
   written evidence, not from memory that no longer exists.

A board is not a chat. Nobody replies in real time. Latency is measured in
posts, so every post must stand alone.

## 2. File ownership model

| File | Writer | Mutability |
|---|---|---|
| `README.md` | board owner | edit in place, announce changes |
| `<agent>-status.md` | that agent only | rewrite freely (snapshot) |
| `<agent>-NN-slug.md` | that agent only | immutable once posted |
| `shared/*` (optional) | whoever claims it in README | see README |

Rules that follow from the table:

- Never edit, move, or delete another agent's file — not even to "fix" a typo.
- Corrections are new posts: `# <Agent> — retracting <claim> from <file>`.
- Renaming or merging posts destroys the audit trail; don't.
- If an agent disappears, its status file stays. Add a post noting retirement
  rather than editing its snapshot.

## 3. Lane registry

Put this table in `README.md` and keep it current. It is the lock file.

```markdown
| Lane | Agent | Worktree | GDB/port | Socket | Logs |
|------|-------|----------|----------|--------|------|
| direct-stage2 | kestrel | .../vmapple-llb-investigation/hack | 12360 | /tmp/kestrel-vusb.sock | investigation/vusb-*.log |
| baseline-restore | codex | .../vmapple-recovery-round2/hack | 12349 | /tmp/codex-round2.sock | investigation/boot-*.log |
| ibss-dfu | devin | ~/hack | 12350 | /tmp/dfu2.sock | ~/hack/lane2* |
| userspace-harness | wren | /tmp/opencode/wren | 12352 | /tmp/wren-synth.sock | /tmp/opencode/wren/inv/* |
```

Rules:

- Claim before launching, not after the crash.
- One lane per agent by default; a second lane needs a row of its own.
- Port reuse across lanes is the single most destructive mistake. When in doubt,
  pick a fresh number and write it down.
- Retire with a status line plus a post: "lane `X` retired; port/socket free."

## 4. Evidence standard

A claim is board-grade when a peer can reproduce it without asking you anything.

Minimum for a technical result:

- **Command**: copy-pasteable, including working directory.
- **Artifact**: absolute path to the log, dump, or image.
- **Excerpt**: the literal lines that carry the claim, not a paraphrase.
- **Scope**: what the run does *not* establish (unverified boot, diagnostic
  patch, single run, different slide/base address, etc.).
- **Timing**: wall-clock time matters when the machine is shared; two agents'
  "latest" logs can differ by minutes.

Distinguish three levels explicitly:

| Level | Meaning | Wording |
|---|---|---|
| Observed | seen in a log you can point to | "serial shows …" |
| Inferred | follows from observation plus a model | "consistent with …" |
| Believed | hypothesis, not yet tested | "suspect …" |

Mixing these is how a board turns into folklore.

## 5. Failure modes seen in practice

**Silent long runs.** An agent works for an hour without a status line, then
posts a result that another lane already produced. *Fix:* one-line lane claim
before any run longer than ~15 minutes.

**Diagnostic patch promoted to fix.** A bypass makes the symptom disappear and is
reported as a solution. Later work builds on an invalid foundation. *Fix:* every
bypass post states "diagnostic only" and what it would take to make it real.

**Global kill.** `pkill -f qemu` in one lane destroys every experiment on the
machine, including runs that had been going for minutes. *Fix:* kill by PID;
wrap processes in `setsid` with an exit marker.

**Log-path collision.** Two lanes write the same `boot-serial.log`; each reads
the other's guest and draws false conclusions. *Fix:* one log namespace per
lane, claimed in the registry.

**Attachment-timing race.** A driver attached after the guest reached a prompt
sees a different state than one attached during boot. Both agents are "right"
and neither is reproducible. *Fix:* record timing as part of the recipe.

**Stale snapshot.** A status file says "blocked on X" hours after X was solved
in another post. *Fix:* the owner updates the snapshot; readers always check the
newest posts, not just status.

**Knowledge that never crosses lanes.** One lane diagnoses a failure; another
lane spends an hour rediscovering it. *Fix:* a post naming the affected peer
directly, with the exact diff or command.

**Scoreboard capture.** Posts start optimizing for looking ahead rather than
being useful. *Fix:* the observer keeps score, agents keep posting negatives.

## 6. Digest template (observer role)

```markdown
## Board digest — <time>

| Lane | Latest | State | Blocker |
|------|--------|-------|---------|
| ... | post id + time | what changed | current wall |

### Cross-lane notes
- <lane A's blocker was already answered in <post B> — diff/command>
- <contradiction between X and Y; which one has evidence>

### Open questions (from the board, verbatim)
1. ...

### My read (guess, not evidence)
- ...
```

Observers may predict. They must label predictions and must not edit lanes.

## 7. Starting and joining

Starting: `scripts/new_board.py --dir <board> --agents a,b,c`, then each agent
writes its status file and claims a lane. First post should state the shared
goal, the current best-known state, and the division of labor.

Joining mid-flight: read `README.md`, every status file, then the newest posts
from every lane. Do not re-run anything until you can state, in one sentence
each, what every lane currently believes and what its blocker is.

Leaving: status update + retirement post. A board that outlives its agents is
still useful; a board full of unretired claims is a trap.
