# Engines: what was actually verified

Spike S2 (2026-10-31, this machine): a 4-lane arena was launched with `arena.py run`; every lane got the same tiny
task: create `hello.txt`, `git commit` it, post one finding with `--evidence hello.txt`. The board DB lives outside
each lane's worktree.

| Engine | Version | Launch template (`scripts/arena.py engine_command`) | Result |
|---|---|---|---|
| codex | 0.160.0 | `codex exec -C <lane> -s workspace-write --skip-git-repo-check -` (brief on stdin) | **Works.** Wrote the file, ran `arena.py post` against the DB outside the lane (+3 points, run twice). `git commit` **fails**: the sandbox marks `.git` metadata read-only even when the path is writable and even with `--add-dir <repo>/.git`. |
| claude | 2.1.207 | `claude -p --permission-mode acceptEdits --allowedTools Bash,Read,Write,Edit,Glob,Grep --add-dir <room dir>` (brief on stdin) | **Not verified.** Launch reached the CLI; it exited with `Failed to authenticate: OAuth session expired` (account state on this machine, not an arena fault). Re-run S2 after `claude` login. |
| opencode | 1.18.34 | `opencode run --dir <lane> [-m provider/model] <brief>` | **Not verified.** Plain `opencode run "say hi"` fails with an unknown server error; with `-m deepseek/deepseek-flash` the provider rejects the stored API key. Set a working model with `ARENA_OPENCODE_MODEL`. |
| gemini | 0.45.2 | `gemini -p <brief> --approval-mode yolo --include-directories <room dir>`, env `GEMINI_CLI_TRUST_WORKSPACE=true` | **Not verified.** First launch refused an untrusted folder (fixed by the env var, now set by the launcher); second launch then failed in user setup / auth (`_doSetupUser`). |

## Consequences baked into the design

- **Commits can be blocked by the engine's sandbox.** `arena.py finalize` therefore *snapshots* any uncommitted lane
  files (`git add -A && git commit` as `arena <arena@local>`) before computing the per-lane diff. Use
  `--no-snapshot` to skip. The brief tells agents to leave files in the lane if `git commit` is refused.
- **The board DB can sit outside the lane** for codex `workspace-write` (observed). Other engines are unverified.
- **`manual` always works**: `arena.py brief --agent NAME` prints the brief; hand it to an in-session subagent (set the
  three `AGENT_BOARD_*` variables from `<workspace>/briefs/NAME.env`).
- **`custom`**: `arena.py run --engine custom --cmd 'my-agent --cwd {lane}'` runs any command; the brief arrives on stdin
  and `AGENT_BOARD_*` are in its environment.

## Env overrides

`ARENA_CLAUDE_MODE` (default `acceptEdits`), `ARENA_CODEX_SANDBOX` (default `workspace-write`),
`ARENA_GEMINI_MODE` (default `yolo`; headless gemini needs it to run shell commands, so it auto-approves every tool:
only use in lanes you are willing to let it control), `ARENA_OPENCODE_MODEL`.

## Sandbox note

None of these engines isolates lanes from each other or from the room: the launcher gives each its own worktree, but
agents run as your user and can read the other lanes. Lane isolation is a convention enforced by the brief, not a
security boundary.
