#!/usr/bin/env python3
"""Research arena: competing agent lanes, a shared forum, a points ledger.

Built on board.py (same SQLite room). The orchestrator opens an arena with
`init`, which creates one git worktree ("lane") and one brief per competitor.
Agents post findings / questions / answers / negative results to boards,
review each other, and earn points. `finalize` writes the report the
orchestrator turns into the real app.

Standard library only.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import secrets
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board as B  # noqa: E402

SCRIPT = Path(__file__).resolve()
ENGINES = ("claude", "codex", "opencode", "gemini", "manual", "custom")
KINDS = ("finding", "negative", "question", "answer", "note")
ENGINE_BRIEF_DIR = "briefs"

RULES = {
    "finding": (3, "finding whose evidence cites an existing file/log (sha256 recorded)"),
    "negative": (2, "negative result with a verifiable evidence file"),
    "answer_accepted": (3, "your answer was accepted by the asker or orchestrator"),
    "question_answered": (1, "your question received an accepted answer"),
    "confirm_reviewer": (2, "you independently confirmed a peer's finding with your own evidence file"),
    "confirm_author": (2, "your finding was independently confirmed"),
    "refute_reviewer": (3, "you refuted a peer's finding with evidence"),
    "refute_author": (-3, "your finding was refuted with evidence"),
}
HOURLY_AUTO_CAP = 30


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def session(args):
    db, _ = B.resolve_db(args.db)
    con = B.connect(db)
    return db, con


def acting(con, args, orchestrator=False):
    name = args.name
    if orchestrator and not name and not os.environ.get("AGENT_BOARD_NAME"):
        row = con.execute(
            "SELECT name FROM agents WHERE role='orchestrator' AND retired_at IS NULL"
        ).fetchone()
        name = row["name"] if row else None
    row = B.agent_row(con, name)
    if orchestrator:
        B.require_role(row, ("orchestrator",))
    return row


def board_row(con, slug):
    row = con.execute("SELECT * FROM boards WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        B.die(f"no board '{slug}' (see: arena.py boards list)")
    if row["closed_at"]:
        B.die(f"board '{slug}' is closed")
    return row


def post_by_n(con, n):
    row = con.execute("SELECT * FROM posts WHERE n = ?", (n,)).fetchone()
    if row is None:
        B.die(f"no post #{n}")
    return row


def render_evidence(items):
    """Return (text, verified file count). A file path (or @file) is verifiable."""
    parts, verified = [], 0
    for item in items or []:
        path = Path(item[1:] if item.startswith("@") else item).expanduser()
        if "\n" not in item and path.is_file():
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            excerpt = data[:4000].decode("utf-8", "replace")
            parts.append(f"[file] {path.resolve()} sha256:{digest} ({len(data)} bytes)\n{excerpt}")
            verified += 1
        else:
            parts.append(B.read_arg(item) or "")
    return "\n".join(parts) or None, verified


def award(con, agent, post_id, reason, points, by="auto"):
    stamp = B.now()
    note = reason
    if by == "auto" and points > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
        recent = con.execute(
            "SELECT COALESCE(SUM(points),0) FROM ledger WHERE agent=? AND by='auto' "
            "AND points>0 AND created_at>=?", (agent, cutoff)).fetchone()[0]
        allowed = max(HOURLY_AUTO_CAP - recent, 0)
        if points > allowed:
            note = f"{reason} (hourly cap {HOURLY_AUTO_CAP})"
            points = allowed
    con.execute(
        "INSERT INTO ledger(agent, post_id, reason, points, by, created_at) VALUES(?,?,?,?,?,?)",
        (agent, post_id, note, points, by, stamp),
    )
    return points


def lane_of(con, name):
    return con.execute("SELECT * FROM lanes WHERE agent = ?", (name,)).fetchone()


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def git(*cmd, cwd=None, check=True):
    return subprocess.run(["git", *cmd], cwd=cwd, capture_output=True, text=True, check=check)


def rules_table():
    return "\n".join(f"| {v[0]:+d} | {v[1]} |" for v in RULES.values())


def env_file(workspace: Path, name: str) -> Path:
    return workspace / ENGINE_BRIEF_DIR / f"{name}.env"


def brief_file(workspace: Path, name: str) -> Path:
    return workspace / ENGINE_BRIEF_DIR / f"{name}.md"


# --------------------------------------------------------------------------
# briefs and lanes
# --------------------------------------------------------------------------

BRIEF = """# Arena brief for `{name}`

## Mission
{goal}

You are one competitor in a small research arena. Other agents research the same
goal in their own lanes. You win points by finding things that are **true and
checkable**, and by helping others. A good finding unlocks the next one: post it.

## Your lane
- Work ONLY inside: `{lane}` (git branch `{branch}`). Commit your work there; if your sandbox
  forbids `git commit`, just leave the files - the orchestrator snapshots lanes at finalize.
- Do not edit another lane. You may read the forum and cite other lanes' evidence.

## Environment (already exported if launched by `arena.py run`)
```
export AGENT_BOARD_DB={db}
export AGENT_BOARD_NAME={name}
export AGENT_BOARD_TOKEN={token}
```
Shorthand: `A="python3 {script}"`

## The forum (use it - this is how the arena beats working alone)
```
$A boards list                                  # topics; open a new one when a thread deserves its own
$A boards open --slug <slug> --title "..."      # any agent may open a board
$A feed [--board <slug>]                        # read what others posted (read before you work!)
$A post --kind finding --title "..." --body "..." --evidence path/to/log.txt --next "one question?"
$A post --kind negative --title "ruled out X by doing Y" --evidence path/to/log.txt
$A ask --title "how does X work?" --body "what I tried"   # ask the whole arena
$A pick                                         # unanswered questions - choose one YOU can answer
$A answer --to <post-number> --body "..." --evidence path   # answer the question you chose
$A accept --post <answer-number>                # (asker) accept the answer that solved it
$A review confirm --post <n> --evidence <your own log file>   # independently replicate a peer
$A review refute  --post <n> --evidence <your own log file>
$A score                                        # scoreboard
```
Evidence must be a **file path that exists** (a log, capture, script output). Text-only
claims earn 0. Say what was *observed* vs *inferred* vs *believed*.

## Points (cosmetic, append-only; they just make winning visible)
| Points | When |
|---|---|
{rules}
| 0-5 | orchestrator bonus for depth, next-path value, unlocking a teammate |

Auto-points are capped at {cap}/hour per agent. You cannot review your own posts.

## Rules
1. Read the feed before starting, and again before each new experiment.
2. Publish negative results - they are the most reused content.
3. One next question per finding. Questions and answers are free of that rule.
4. Never kill processes by name (`killall`, `pkill -f`). Kill only your own PID/process group.
5. Label diagnostics as diagnostics; a check-skipping patch is a probe, not a fix.
6. Claim ports/sockets via `python3 {board} claim --kind port --hint <label>`; never guess one.
7. When finished, post a final `note` summarising what you proved and what remains.
"""


def make_brief(con, room, name, token, lane):
    goal = B.current_goal(con)
    text = BRIEF.format(
        name=name,
        goal=(goal["body"] if goal else "(the orchestrator has not stated a goal yet)"),
        lane=lane["worktree"], branch=lane["branch"] or "(no git)",
        db=room["_db"], token=token,
        script=SCRIPT, board=SCRIPT.parent / "board.py",
        rules=rules_table(), cap=HOURLY_AUTO_CAP,
    )
    return text


def add_lane(con, room, db, orch, name, engine, base, repo):
    B.safe_name(name)
    if con.execute("SELECT 1 FROM agents WHERE name=?", (name,)).fetchone():
        B.die(f"agent '{name}' already exists")
    workspace = Path(room["workspace"])
    path = workspace / name
    if path.exists():
        B.die(f"lane path already exists: {path}")
    branch = f"arena/{room['id'][:8]}/{name}"
    base_commit = None
    if repo and git("rev-parse", "--verify", "HEAD", cwd=repo, check=False).returncode == 0:
        base_commit = git("rev-parse", base or "HEAD", cwd=repo).stdout.strip()
        workspace.mkdir(parents=True, exist_ok=True)
        git("worktree", "add", "-b", branch, str(path), base_commit, cwd=repo)
    else:
        path.mkdir(parents=True, exist_ok=True)
        branch = None
    token = secrets.token_urlsafe(18)
    stamp = B.now()
    with B.tx(con):
        con.execute(
            "INSERT INTO agents(name, role, joined_at, last_seen, token_hash) VALUES(?,?,?,?,?)",
            (name, "worker", stamp, stamp, hashlib.sha256(token.encode()).hexdigest()),
        )
        con.execute(
            "INSERT INTO lanes(agent, branch, worktree, base_commit, engine, started_at) VALUES(?,?,?,?,?,?)",
            (name, branch, str(path), base_commit, engine, stamp),
        )
        con.execute(
            "INSERT INTO claims(kind, value, agent, hint, created_at) VALUES('worktree',?,?,?,?)",
            (str(path), name, "lane", stamp),
        )
    room = dict(room)
    room["_db"] = str(db)
    lane = lane_of(con, name)
    (workspace / ENGINE_BRIEF_DIR).mkdir(parents=True, exist_ok=True)
    brief_file(workspace, name).write_text(make_brief(con, room, name, token, lane))
    ef = env_file(workspace, name)
    ef.write_text(
        f"export AGENT_BOARD_DB={shlex.quote(str(db))}\n"
        f"export AGENT_BOARD_NAME={shlex.quote(name)}\n"
        f"export AGENT_BOARD_TOKEN={shlex.quote(token)}\n"
    )
    ef.chmod(0o600)
    return {"agent": name, "worktree": str(path), "branch": branch, "engine": engine,
            "brief": str(brief_file(workspace, name)), "env": str(ef)}


def cmd_init(args):
    repo = Path(args.repo).expanduser().resolve() if args.repo else Path.cwd()
    top = git("rev-parse", "--show-toplevel", cwd=repo, check=False)
    repo = Path(top.stdout.strip()) if top.returncode == 0 else None
    ns = argparse.Namespace(
        name=args.name, db=args.db, title=args.title, goal=args.goal,
        workspace=args.workspace, port_range=args.port_range,
        no_pointer=args.no_pointer, json=True,
    )
    with contextlib.redirect_stdout(io.StringIO()):
        B.cmd_init(ns)
    db, _ = B.resolve_db(args.db)
    con = B.connect(db)
    room = B.room_row(con)
    orch = B.agent_row(con, args.name)
    with B.tx(con):
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('arena','1')")
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('repo',?)", (str(repo or ""),))
    names = [n.strip() for n in (args.lanes or "").split(",") if n.strip()]
    lanes = [add_lane(con, room, db, orch, n, args.engine, args.base, repo) for n in names]
    lines = [f"arena room {room['id']} - {room['title']}", f"database {db}",
             f"repo     {repo or '(not a git repo: lanes are plain directories)'}"]
    for l in lanes:
        lines.append(f"lane {l['agent']:12} {l['worktree']}  brief {l['brief']}")
    lines.append("next: arena.py run   (headless engines)   or hand each brief to an in-session subagent")
    B.emit({"ok": True, "db": str(db), "lanes": lanes, "lines": lines}, args.json)


def cmd_lane(args):
    db, con = session(args)
    room = B.room_row(con)
    orch = acting(con, args, orchestrator=True)
    if args.action == "add":
        repo = con.execute("SELECT value FROM meta WHERE key='repo'").fetchone()
        repo = Path(repo[0]) if repo and repo[0] else None
        out = add_lane(con, room, db, orch, args.agent, args.engine, args.base, repo)
        B.emit({"ok": True, "lane": out, "lines": [json.dumps(out, indent=1)]}, args.json)
    elif args.action == "list":
        rows = con.execute("SELECT * FROM lanes ORDER BY agent").fetchall()
        lines = [f"{r['agent']:14} {r['engine']:9} {r['branch'] or '-':40} {r['worktree']}" for r in rows]
        B.emit({"ok": True, "lanes": [dict(r) for r in rows], "lines": lines or ["(no lanes)"]}, args.json)


def cmd_brief(args):
    db, con = session(args)
    room = B.room_row(con)
    name = args.agent or args.name or os.environ.get("AGENT_BOARD_NAME")
    if not name:
        B.die("--agent NAME")
    path = brief_file(Path(room["workspace"]), name)
    if not path.is_file():
        B.die(f"no brief for '{name}' (is it a lane?)")
    text = path.read_text()
    B.emit({"ok": True, "brief": text, "path": str(path), "lines": text.splitlines()}, args.json)


# --------------------------------------------------------------------------
# boards and forum
# --------------------------------------------------------------------------

def cmd_boards(args):
    db, con = session(args)
    if args.action == "open":
        row = B.agent_row(con, args.name)
        slug = B.slug(args.slug or args.title or "", 40)
        if not args.title:
            B.die("--title required")
        parent = board_row(con, args.parent)["id"] if args.parent else None
        with B.tx(con):
            if con.execute("SELECT 1 FROM boards WHERE slug=?", (slug,)).fetchone():
                B.die(f"board '{slug}' already exists")
            con.execute(
                "INSERT INTO boards(slug, title, description, opened_by, parent_id, created_at) VALUES(?,?,?,?,?,?)",
                (slug, args.title, B.read_arg(args.desc), row["name"], parent, B.now()),
            )
        B.emit({"ok": True, "slug": slug, "lines": [f"opened board '{slug}'"]}, args.json)
    elif args.action == "close":
        row = B.agent_row(con, args.name)
        b = board_row(con, args.slug)
        if row["role"] != "orchestrator" and row["name"] != b["opened_by"]:
            B.die("only the orchestrator or the opener can close a board")
        con.execute("UPDATE boards SET closed_at=? WHERE id=?", (B.now(), b["id"]))
        B.emit({"ok": True, "lines": [f"closed '{args.slug}'"]}, args.json)
    else:
        rows = con.execute(
            "SELECT b.*, (SELECT count(*) FROM posts p WHERE p.board_id=b.id) AS posts FROM boards b ORDER BY id"
        ).fetchall()
        lines = [f"{r['slug']:24} {r['posts']:>4} posts  {'closed' if r['closed_at'] else 'open':6} {r['title']}  (by {r['opened_by']})" for r in rows]
        B.emit({"ok": True, "boards": [dict(r) for r in rows], "lines": lines}, args.json)


def create_post(con, row, kind, title, body, evidence, board_slug, reply_to_n, question, task):
    b = board_row(con, board_slug)
    ev_text, verified = render_evidence(evidence)
    points = 0
    with B.tx(con):
        if con.execute(
            "SELECT 1 FROM posts WHERE agent=? AND board_id=? AND title=? AND kind=?",
            (row["name"], b["id"], title, kind)).fetchone():
            B.die("duplicate post (same agent, board, kind and title)")
        reply_id = post_by_n(con, reply_to_n)["id"] if reply_to_n else None
        n = con.execute("SELECT COALESCE(MAX(n),0)+1 FROM posts").fetchone()[0]
        lane = lane_of(con, row["name"])
        cur = con.execute(
            "INSERT INTO posts(n, agent, title, body, evidence, question, task_id, reply_to, created_at,"
            " board_id, kind, lane) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (n, row["name"], title, body, ev_text, question, task, reply_id, B.now(),
             b["id"], kind, lane["agent"] if lane else None),
        )
        if kind == "finding" and verified:
            points = award(con, row["name"], cur.lastrowid, RULES["finding"][1], RULES["finding"][0])
        elif kind == "negative" and verified:
            points = award(con, row["name"], cur.lastrowid, RULES["negative"][1], RULES["negative"][0])
    return n, points, verified


def cmd_post(args, kind=None):
    db, con = session(args)
    row = B.agent_row(con, args.name)
    kind = kind or args.kind
    question = B.read_arg(getattr(args, "next_q", None))
    if kind == "finding" and question is None and not args.no_question:
        B.die("a finding ends with one next question (--next) or --no-question")
    reply = getattr(args, "to", None)
    if kind == "answer":
        if not reply:
            B.die("--to <question post number> required")
        if post_by_n(con, reply)["kind"] != "question":
            B.die(f"#{reply} is not a question")
    n, points, verified = create_post(
        con, row, kind, args.title or B.die("--title required"), B.read_arg(args.body),
        args.evidence, args.board, reply, question, getattr(args, "task", None))
    note = ""
    if kind in ("finding", "negative") and not verified:
        note = " (no verifiable evidence file: 0 points)"
    B.emit({"ok": True, "post": n, "points": points, "verified": verified,
            "lines": [f"posted #{n} [{kind}] +{points}{note}"]}, args.json)


def cmd_ask(args):
    cmd_post(args, "question")


def cmd_answer(args):
    cmd_post(args, "answer")


def cmd_feed(args):
    db, con = session(args)
    where, params = ["1=1"], []
    if args.board:
        where.append("b.slug = ?"); params.append(args.board)
    if args.kind:
        where.append("p.kind = ?"); params.append(args.kind)
    if args.since:
        where.append("p.n > ?"); params.append(args.since)
    rows = con.execute(
        "SELECT p.*, b.slug AS board FROM posts p JOIN boards b ON b.id=p.board_id "
        f"WHERE {' AND '.join(where)} ORDER BY p.id DESC LIMIT ?", (*params, args.limit)).fetchall()
    rows = list(reversed(rows))
    lines = []
    for r in rows:
        reply = f" re:#{con.execute('SELECT n FROM posts WHERE id=?', (r['reply_to'],)).fetchone()[0]}" if r["reply_to"] else ""
        mark = " ACCEPTED" if r["accepted_at"] else ""
        lines.append(f"#{r['n']} [{r['board']}] {r['kind']}{reply}{mark} {r['agent']}: {r['title']}")
        if args.full:
            for part in (r["body"], r["evidence"]):
                if part:
                    lines.extend("    " + x for x in str(part).splitlines())
            if r["question"]:
                lines.append(f"    next: {r['question']}")
            lines.append("")
    B.emit({"ok": True, "posts": [dict(r) for r in rows], "lines": lines or ["(nothing yet)"]}, args.json)


def open_questions(con, board=None, exclude=None):
    sql = (
        "SELECT p.n, p.agent, p.title, b.slug AS board, p.created_at, "
        "(SELECT count(*) FROM posts a WHERE a.reply_to=p.id AND a.kind='answer') AS answers "
        "FROM posts p JOIN boards b ON b.id=p.board_id WHERE p.kind='question' AND NOT EXISTS ("
        "SELECT 1 FROM posts a WHERE a.reply_to=p.id AND a.kind='answer' AND a.accepted_at IS NOT NULL)"
    )
    params = []
    if board:
        sql += " AND b.slug=?"; params.append(board)
    if exclude:
        sql += " AND p.agent != ?"; params.append(exclude)
    return con.execute(sql + " ORDER BY answers, p.id", params).fetchall()


def cmd_pick(args):
    db, con = session(args)
    me = args.name or os.environ.get("AGENT_BOARD_NAME")
    rows = open_questions(con, args.board, me)
    lines = [f"#{r['n']} [{r['board']}] {r['agent']} ({r['answers']} answers): {r['title']}" for r in rows]
    B.emit({"ok": True, "questions": [dict(r) for r in rows],
            "lines": (lines + ["", "answer one: arena.py answer --to <n> --title ... --body ..."]) if rows
            else ["(no open questions)"]}, args.json)


def cmd_accept(args):
    db, con = session(args)
    row = B.agent_row(con, args.name)
    a = post_by_n(con, args.post)
    if a["kind"] != "answer" or not a["reply_to"]:
        B.die(f"#{args.post} is not an answer")
    q = con.execute("SELECT * FROM posts WHERE id=?", (a["reply_to"],)).fetchone()
    if row["name"] != q["agent"] and row["role"] != "orchestrator":
        B.die("only the asker or the orchestrator can accept an answer")
    with B.tx(con):
        if a["accepted_at"]:
            B.die("already accepted")
        first = not con.execute(
            "SELECT 1 FROM posts WHERE reply_to=? AND kind='answer' AND accepted_at IS NOT NULL",
            (q["id"],)).fetchone()
        con.execute("UPDATE posts SET accepted_at=? WHERE id=?", (B.now(), a["id"]))
        pts = 0
        if a["agent"] != q["agent"]:
            pts += award(con, a["agent"], a["id"], RULES["answer_accepted"][1], RULES["answer_accepted"][0])
            if first:
                award(con, q["agent"], q["id"], RULES["question_answered"][1], RULES["question_answered"][0])
    B.emit({"ok": True, "lines": [f"accepted #{args.post}; answerer +{pts}"]}, args.json)


def cmd_review(args):
    db, con = session(args)
    row = B.agent_row(con, args.name)
    p = post_by_n(con, args.post)
    if p["kind"] not in ("finding", "negative"):
        B.die("only findings and negative results can be reviewed")
    if p["agent"] == row["name"]:
        B.die("you cannot review your own post")
    ev_text, verified = render_evidence(args.evidence)
    if not verified:
        B.die("a review needs your own evidence file (--evidence path)")
    verdict = "confirm" if args.verdict == "confirm" else "refute"
    other = "refute" if verdict == "confirm" else "confirm"
    with B.tx(con):
        if con.execute("SELECT 1 FROM reviews WHERE post_id=? AND reviewer=?", (p["id"], row["name"])).fetchone():
            B.die("you already reviewed this post")
        con.execute(
            "INSERT INTO reviews(post_id, reviewer, verdict, note, evidence, created_at) VALUES(?,?,?,?,?,?)",
            (p["id"], row["name"], verdict, B.read_arg(args.note), ev_text, B.now()))
        r_key, a_key = (("confirm_reviewer", "confirm_author") if verdict == "confirm"
                        else ("refute_reviewer", "refute_author"))
        got = award(con, row["name"], p["id"], RULES[r_key][1], RULES[r_key][0])
        award(con, p["agent"], p["id"], RULES[a_key][1], RULES[a_key][0])
    B.emit({"ok": True, "lines": [f"{verdict}ed #{args.post}; you +{got}"]}, args.json)


def cmd_award(args):
    db, con = session(args)
    row = acting(con, args, orchestrator=True)
    with B.tx(con):
        if args.revoke:
            e = con.execute("SELECT * FROM ledger WHERE id=?", (args.revoke,)).fetchone()
            if e is None:
                B.die(f"no ledger entry {args.revoke}")
            award(con, e["agent"], e["post_id"], f"revoked entry {e['id']}: {args.reason or e['reason']}", -e["points"], row["name"])
            msg = f"revoked entry {args.revoke}"
        else:
            if not args.agent or args.points is None or not args.reason:
                B.die("--agent, --points and --reason are required")
            if con.execute("SELECT 1 FROM agents WHERE name=?", (args.agent,)).fetchone() is None:
                B.die(f"unknown agent '{args.agent}'")
            if not -10 <= args.points <= 10:
                B.die("bonus must be between -10 and 10")
            pid = post_by_n(con, args.post)["id"] if args.post else None
            award(con, args.agent, pid, args.reason, args.points, row["name"])
            msg = f"awarded {args.points:+d} to {args.agent}"
    B.emit({"ok": True, "lines": [msg]}, args.json)


def scoreboard(con):
    rows = con.execute(
        "SELECT a.name AS agent, COALESCE(SUM(l.points),0) AS points, "
        "(SELECT count(*) FROM posts p WHERE p.agent=a.name AND p.kind='finding') AS findings, "
        "(SELECT count(*) FROM posts p WHERE p.agent=a.name AND p.kind='negative') AS negatives, "
        "(SELECT count(*) FROM posts p WHERE p.agent=a.name AND p.kind='answer' AND p.accepted_at IS NOT NULL) AS answers, "
        "(SELECT count(*) FROM reviews r WHERE r.reviewer=a.name AND r.verdict='confirm') AS confirms "
        "FROM agents a LEFT JOIN ledger l ON l.agent=a.name WHERE a.role='worker' AND a.retired_at IS NULL "
        "GROUP BY a.name ORDER BY points DESC, findings DESC, a.name"
    ).fetchall()
    return [dict(r, rank=i + 1) for i, r in enumerate(rows)]


def score_lines(rows):
    lines = [f"{'#':>2} {'agent':16} {'points':>6} {'findings':>8} {'negative':>8} {'answers':>7} {'confirms':>8}"]
    for r in rows:
        lines.append(f"{r['rank']:>2} {r['agent']:16} {r['points']:>6} {r['findings']:>8} "
                     f"{r['negatives']:>8} {r['answers']:>7} {r['confirms']:>8}")
    return lines


def cmd_score(args):
    db, con = session(args)
    rows = scoreboard(con)
    lines = score_lines(rows)
    if args.ledger:
        lines += ["", "ledger:"]
        for e in con.execute("SELECT * FROM ledger ORDER BY id"):
            lines.append(f"  {e['id']:>3} {e['agent']:14} {e['points']:+d}  by {e['by']:10} {e['reason']}")
    B.emit({"ok": True, "scores": rows, "lines": lines}, args.json)


# --------------------------------------------------------------------------
# launching engines
# --------------------------------------------------------------------------

def engine_command(engine, prompt, lane, roomdir, custom):
    """Return (argv, stdin_text). Flags verified with each CLI's --help; see references/engines.md."""
    if engine == "claude":
        mode = os.environ.get("ARENA_CLAUDE_MODE", "acceptEdits")
        return (["claude", "-p", "--permission-mode", mode,
                 "--allowedTools", "Bash,Read,Write,Edit,Glob,Grep",
                 "--add-dir", roomdir], prompt)
    if engine == "codex":
        return (["codex", "exec", "-C", lane, "-s", os.environ.get("ARENA_CODEX_SANDBOX", "workspace-write"),
                 "--skip-git-repo-check", "-"], prompt)
    if engine == "opencode":
        model = os.environ.get("ARENA_OPENCODE_MODEL")
        return (["opencode", "run", "--dir", lane, *(["-m", model] if model else []), prompt], None)
    if engine == "gemini":
        return (["gemini", "-p", prompt, "--approval-mode", os.environ.get("ARENA_GEMINI_MODE", "yolo"),
                 "--include-directories", roomdir], None)
    if engine == "custom":
        if not custom:
            B.die("engine=custom needs --cmd 'command' ({lane} is replaced; the brief arrives on stdin)")
        return (shlex.split(custom.replace("{lane}", shlex.quote(lane))), prompt)
    return None, None


def cmd_run(args):
    db, con = session(args)
    room = B.room_row(con)
    acting(con, args, orchestrator=True)
    workspace = Path(room["workspace"])
    runtime = B.runtime_dir(room)
    lanes = con.execute("SELECT * FROM lanes ORDER BY agent").fetchall()
    if args.agent:
        lanes = [l for l in lanes if l["agent"] in args.agent.split(",")]
    lines = []
    for l in lanes:
        name = l["agent"]
        engine = args.engine or l["engine"]
        if pid_alive(l["pid"]):
            lines.append(f"{name}: already running (pid {l['pid']})")
            continue
        prompt = brief_file(workspace, name).read_text()
        argv, stdin_text = engine_command(engine, prompt, l["worktree"], str(workspace.parent), args.cmd)
        if argv is None:
            lines.append(f"{name}: engine manual - hand the brief to the agent: {brief_file(workspace, name)}")
            continue
        if args.dry_run:
            lines.append(f"{name}: {shlex.join(argv[:6])} ...")
            continue
        env = dict(os.environ, GEMINI_CLI_TRUST_WORKSPACE="true")
        for line in env_file(workspace, name).read_text().splitlines():
            k, _, v = line.replace("export ", "", 1).partition("=")
            env[k] = shlex.split(v)[0]
        log = runtime / f"{name}.log"
        with open(log, "ab") as out:
            proc = subprocess.Popen(
                argv, cwd=l["worktree"], env=env, stdin=subprocess.PIPE if stdin_text else subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
            if stdin_text:
                proc.stdin.write(stdin_text.encode()); proc.stdin.close()
        con.execute("UPDATE lanes SET pid=?, log=?, engine=?, started_at=?, ended_at=NULL WHERE agent=?",
                    (proc.pid, str(log), engine, B.now(), name))
        lines.append(f"{name}: started {engine} pid {proc.pid} log {log}")
    B.emit({"ok": True, "lines": lines or ["(no lanes)"]}, args.json)


def cmd_stop(args):
    db, con = session(args)
    acting(con, args, orchestrator=True)
    lines = []
    for l in con.execute("SELECT * FROM lanes ORDER BY agent").fetchall():
        if args.agent and l["agent"] not in args.agent.split(","):
            continue
        if pid_alive(l["pid"]):
            try:
                os.killpg(l["pid"], signal.SIGTERM)
                lines.append(f"{l['agent']}: SIGTERM to process group {l['pid']}")
            except OSError as exc:
                lines.append(f"{l['agent']}: could not stop pid {l['pid']}: {exc}")
        con.execute("UPDATE lanes SET ended_at=? WHERE agent=? AND ended_at IS NULL", (B.now(), l["agent"]))
    B.emit({"ok": True, "lines": lines or ["(nothing running)"]}, args.json)


def cmd_status(args):
    db, con = session(args)
    now_dt = datetime.now(timezone.utc)
    scores = {s["agent"]: s["points"] for s in scoreboard(con)}
    lines, out = [f"{'lane':14} {'engine':9} {'run':8} {'posts':>5} {'last post':>10} {'points':>6}"], []
    for l in con.execute("SELECT * FROM lanes ORDER BY agent"):
        last = con.execute("SELECT created_at FROM posts WHERE agent=? ORDER BY id DESC LIMIT 1", (l["agent"],)).fetchone()
        n = con.execute("SELECT count(*) FROM posts WHERE agent=?", (l["agent"],)).fetchone()[0]
        age = None
        if last:
            age = int((now_dt - datetime.fromisoformat(last[0])).total_seconds() // 60)
        alive = pid_alive(l["pid"])
        silent = alive and (age is None or age > args.silent_min)
        lines.append(f"{l['agent']:14} {l['engine']:9} {('running' if alive else 'idle'):8} {n:>5} "
                     f"{(str(age) + 'm') if age is not None else '-':>10} {scores.get(l['agent'], 0):>6}"
                     + ("  SILENT?" if silent else ""))
        out.append({"agent": l["agent"], "running": alive, "posts": n, "minutes_since_post": age, "silent": silent})
    B.emit({"ok": True, "lanes": out, "lines": lines}, args.json)


# --------------------------------------------------------------------------
# finalize
# --------------------------------------------------------------------------

def finalize_data(con):
    findings = con.execute(
        "SELECT p.*, b.slug AS board FROM posts p JOIN boards b ON b.id=p.board_id "
        "WHERE p.kind='finding' ORDER BY p.n").fetchall()
    out = []
    for f in findings:
        conf = con.execute("SELECT reviewer FROM reviews WHERE post_id=? AND verdict='confirm'", (f["id"],)).fetchall()
        ref = con.execute("SELECT reviewer FROM reviews WHERE post_id=? AND verdict='refute'", (f["id"],)).fetchall()
        bonus = con.execute("SELECT COALESCE(SUM(points),0) FROM ledger WHERE post_id=? AND by!='auto' AND points>0",
                            (f["id"],)).fetchone()[0]
        status = "refuted" if ref else ("accepted" if conf or bonus > 0 else "unreviewed")
        out.append({"post": dict(f), "confirmed_by": [r[0] for r in conf],
                    "refuted_by": [r[0] for r in ref], "status": status})
    return out


def cmd_finalize(args):
    db, con = session(args)
    acting(con, args, orchestrator=True)
    room = B.room_row(con)
    out = Path(args.out).expanduser().resolve() if args.out else Path(room["workspace"]).parent / "report"
    out.mkdir(parents=True, exist_ok=True)
    scores = scoreboard(con)
    goal = B.current_goal(con)
    lines = [f"# Arena report - {room['title']}", "", f"Room `{room['id']}` - generated {B.now()}", "",
             "## Goal", "", goal["body"] if goal else "(unset)", "", "## Scoreboard", "", "```"]
    lines += score_lines(scores) + ["```", ""]
    fd = finalize_data(con)
    lines += ["## Findings", ""]
    findings_md = ["# Findings", ""]
    for f in fd:
        p = f["post"]
        head = f"- **#{p['n']} [{f['status']}]** {p['title']} - {p['agent']} (board `{p['board']}`)"
        lines.append(head)
        findings_md += [f"## #{p['n']} {p['title']}", "", f"_{p['agent']}, status: {f['status']}, "
                        f"confirmed by: {', '.join(f['confirmed_by']) or '-'}_", ""]
        if p["body"]:
            findings_md += [p["body"], ""]
        if p["evidence"]:
            findings_md += ["Evidence:", "", "```", p["evidence"], "```", ""]
    if not fd:
        lines.append("(none)")
    lines += ["", "## Negative results (ruled out)", ""]
    neg = con.execute("SELECT n, agent, title FROM posts WHERE kind='negative' ORDER BY n").fetchall()
    lines += [f"- #{r['n']} {r['title']} - {r['agent']}" for r in neg] or ["(none)"]
    lines += ["", "## Unanswered questions", ""]
    oq = open_questions(con)
    lines += [f"- #{r['n']} [{r['board']}] {r['title']} - {r['agent']}" for r in oq] or ["(none)"]
    lines += ["", "## Boards", ""]
    for b in con.execute("SELECT b.*, (SELECT count(*) FROM posts p WHERE p.board_id=b.id) AS n FROM boards b ORDER BY id"):
        lines.append(f"- `{b['slug']}` - {b['title']} ({b['n']} posts, opened by {b['opened_by']})")
    lines += ["", "## Lanes", ""]
    for l in con.execute("SELECT * FROM lanes ORDER BY agent"):
        stat = ""
        wt = Path(l["worktree"])
        if l["branch"] and l["base_commit"] and wt.is_dir():
            repo = con.execute("SELECT value FROM meta WHERE key='repo'").fetchone()[0]
            if not args.no_snapshot and git("status", "--porcelain", cwd=wt, check=False).stdout.strip():
                git("add", "-A", cwd=wt, check=False)
                git("-c", "user.name=arena", "-c", "user.email=arena@local", "commit", "-q", "-m",
                    f"arena snapshot of lane {l['agent']}", cwd=wt, check=False)
            diff = git("diff", "--stat", f"{l['base_commit']}..{l['branch']}", cwd=repo, check=False).stdout.strip()
            dirty = git("status", "--short", cwd=wt, check=False).stdout.strip()
            stat = f"\n  committed diff vs base:\n```\n{diff or '(no commits)'}\n```"
            if dirty:
                stat += f"\n  UNCOMMITTED in lane:\n```\n{dirty}\n```"
        lines.append(f"- **{l['agent']}** branch `{l['branch'] or '-'}` at `{l['worktree']}`{stat}")
    (out / "ARENA-REPORT.md").write_text("\n".join(lines) + "\n")
    (out / "findings.md").write_text("\n".join(findings_md) + "\n")
    B.emit({"ok": True, "out": str(out), "scores": scores,
            "lines": score_lines(scores) + ["", f"report   {out / 'ARENA-REPORT.md'}",
                                            f"findings {out / 'findings.md'}"]}, args.json)


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db")
    common.add_argument("--json", action="store_true")
    common.add_argument("--as", dest="name")
    p = argparse.ArgumentParser(prog="arena", description="Research arena on top of the shared board.")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, fn, help, **kw):
        q = sub.add_parser(name, parents=[common], help=help, **kw)
        q.set_defaults(func=fn)
        return q

    q = add("init", cmd_init, "create arena room + lanes (orchestrator)")
    q.add_argument("--title", default="Research arena")
    q.add_argument("--goal")
    q.add_argument("--lanes", help="comma-separated competitor names")
    q.add_argument("--engine", choices=ENGINES, default="manual")
    q.add_argument("--repo", help="git repo to branch lanes from (default: cwd)")
    q.add_argument("--base", help="commit-ish to branch from (default HEAD)")
    q.add_argument("--workspace")
    q.add_argument("--port-range", default="12000-12999")
    q.add_argument("--no-pointer", action="store_true")

    q = add("lane", cmd_lane, "add or list lanes (orchestrator)")
    q.add_argument("action", choices=("add", "list"))
    q.add_argument("--agent")
    q.add_argument("--engine", choices=ENGINES, default="manual")
    q.add_argument("--base")

    q = add("brief", cmd_brief, "print a lane's mission brief")
    q.add_argument("--agent")

    q = add("boards", cmd_boards, "list / open / close boards")
    q.add_argument("action", choices=("list", "open", "close"), nargs="?", default="list")
    q.add_argument("--slug")
    q.add_argument("--title")
    q.add_argument("--desc")
    q.add_argument("--parent")

    def postargs(q, kind_choices=None):
        q.add_argument("--board", default="main")
        q.add_argument("--title")
        q.add_argument("--body")
        q.add_argument("--evidence", action="append")
        q.add_argument("--next", dest="next_q")
        q.add_argument("--no-question", action="store_true")
        q.add_argument("--task", type=int)

    q = add("post", cmd_post, "post a finding / negative result / note")
    q.add_argument("--kind", choices=("finding", "negative", "note"), default="finding")
    q.add_argument("--to", type=int, help="reply to post number")
    postargs(q)
    q = add("ask", cmd_ask, "ask the whole arena a question")
    postargs(q)
    q = add("answer", cmd_answer, "answer a question")
    q.add_argument("--to", type=int, required=True, help="question post number")
    postargs(q)

    q = add("feed", cmd_feed, "read the forum")
    q.add_argument("--board")
    q.add_argument("--kind", choices=KINDS)
    q.add_argument("--since", type=int)
    q.add_argument("--limit", type=int, default=30)
    q.add_argument("--full", action="store_true")

    q = add("pick", cmd_pick, "list open questions you may choose to answer")
    q.add_argument("--board")
    q = add("accept", cmd_accept, "accept an answer (asker/orchestrator)")
    q.add_argument("--post", type=int, required=True)
    q = add("review", cmd_review, "confirm or refute a peer's finding with your own evidence")
    q.add_argument("verdict", choices=("confirm", "refute"))
    q.add_argument("--post", type=int, required=True)
    q.add_argument("--evidence", action="append")
    q.add_argument("--note")
    q = add("award", cmd_award, "bonus points or revoke an entry (orchestrator)")
    q.add_argument("--agent")
    q.add_argument("--points", type=int)
    q.add_argument("--reason")
    q.add_argument("--post", type=int)
    q.add_argument("--revoke", type=int, help="ledger entry id to reverse")
    q = add("score", cmd_score, "scoreboard")
    q.add_argument("--ledger", action="store_true")

    q = add("run", cmd_run, "launch lane agents headless (orchestrator)")
    q.add_argument("--agent", help="comma-separated lanes (default all)")
    q.add_argument("--engine", choices=ENGINES)
    q.add_argument("--cmd", help="template for engine=custom: {lane}")
    q.add_argument("--dry-run", action="store_true")
    q = add("stop", cmd_stop, "stop lane process groups (orchestrator)")
    q.add_argument("--agent")
    q = add("status", cmd_status, "lane liveness and silence")
    q.add_argument("--silent-min", type=int, default=15)
    q = add("finalize", cmd_finalize, "write ARENA-REPORT.md and findings.md (orchestrator)")
    q.add_argument("--out")
    q.add_argument("--no-snapshot", action="store_true", help="do not commit uncommitted lane files")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
