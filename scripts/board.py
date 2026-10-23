#!/usr/bin/env python3
"""Shared agent board: one SQLite room, created and planned by an orchestrator.

Design constraints:

* No fixed agent names. Identity comes from --as, $AGENT_BOARD_NAME, or an
  auto-generated id. Nothing in the code assumes who the peers are.
* No fixed paths. The room is discovered from --db, $AGENT_BOARD_DB, or a
  ``.agentboard`` pointer found by walking up from the current directory.
* Ports, sockets and work directories are allocated by the board, not by
  convention, so two sessions cannot pick the same resource.
* The orchestrator creates the room, writes the plan, and assigns tasks.
  Workers join, pull the next task, claim what they need, and post evidence.

Standard library only. Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

POINTER = ".agentboard"
SCHEMA = 2
ROLES = ("orchestrator", "worker", "observer")
KINDS = ("port", "socket", "path", "worktree", "lock", "custom")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SLUG_RE = re.compile(r"[^a-z0-9]+")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_seconds(ts: str, seconds: int) -> str:
    from datetime import timedelta

    return (datetime.fromisoformat(ts) + timedelta(seconds=seconds)).isoformat(
        timespec="seconds"
    )


def die(msg: str, code: int = 1) -> "NoReturn":
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def slug(text: str, limit: int = 48) -> str:
    out = SLUG_RE.sub("-", (text or "").lower()).strip("-")
    return (out[:limit].strip("-") or "item")


def safe_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        die("invalid name: use letters, digits, dot, dash, underscore (max 64)")
    return name


def emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for line in payload.get("lines", []):
            print(line)


def read_arg(value: str | None) -> str | None:
    """Accept inline text, @file, or '-' for stdin."""
    if value is None:
        return None
    if value == "-":
        return sys.stdin.read()
    if value.startswith("@"):
        path = Path(value[1:]).expanduser()
        if not path.is_file():
            die(f"file not found: {path}")
        return path.read_text()
    return value


def parse_range(value: str) -> tuple[int, int]:
    m = re.match(r"^(\d{2,5})\s*-\s*(\d{2,5})$", value or "")
    if not m:
        die("range must look like 12000-12999")
    lo, hi = int(m.group(1)), int(m.group(2))
    if not (0 < lo < hi < 65536):
        die("range out of bounds")
    return lo, hi


# --------------------------------------------------------------------------
# room discovery and storage
# --------------------------------------------------------------------------

def find_pointer(start: Path) -> tuple[Path, str] | None:
    for directory in (start, *start.parents):
        ptr = directory / POINTER
        if not ptr.is_file():
            continue
        raw = ptr.read_text().strip()
        if not raw:
            continue
        target = Path(raw)
        if not target.is_absolute():
            target = (directory / target).resolve()
        return target, str(ptr)
    return None


def resolve_db(explicit: str | None) -> tuple[Path, str]:
    """Return (database path, where it came from).

    Order: --db, $AGENT_BOARD_DB, nearest .agentboard pointer.
    Never guesses a default: a missing room is an error, because only the
    orchestrator creates rooms.
    """
    if explicit:
        return Path(explicit).expanduser().resolve(), "argument"
    env = os.environ.get("AGENT_BOARD_DB")
    if env:
        return Path(env).expanduser().resolve(), "environment"
    found = find_pointer(Path.cwd())
    if found:
        path, pointer = found
        return path, f"pointer {pointer}"
    die(
        "no room found. The orchestrator creates one with `board.py init`, "
        "or pass --db / set AGENT_BOARD_DB."
    )


def connect(db: Path) -> sqlite3.Connection:
    if not db.is_file():
        die(f"room database not found: {db}")
    con = sqlite3.connect(str(db), timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=15000")
    if con.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='room'").fetchone()[0] == 0:
        die(f"not a board room: {db}")
    return con


@contextmanager
def tx(con: sqlite3.Connection):
    con.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")


def schema(con: sqlite3.Connection, overwrite: bool = False) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS room(
            id TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            title TEXT NOT NULL,
            goal TEXT,
            workspace TEXT NOT NULL,
            port_start INTEGER NOT NULL,
            port_end INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agents(
            name TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            joined_at TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            retired_at TEXT
        );
        CREATE TABLE IF NOT EXISTS plan(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            detail TEXT,
            owner TEXT,
            state TEXT NOT NULL DEFAULT 'open',
            blocked_by TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS claims(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            agent TEXT NOT NULL,
            hint TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            released_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS claims_active
            ON claims(kind, value) WHERE released_at IS NULL;
        CREATE TABLE IF NOT EXISTS status(
            agent TEXT PRIMARY KEY,
            body TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS posts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            n INTEGER NOT NULL,
            agent TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT,
            evidence TEXT,
            question TEXT,
            task_id INTEGER,
            reply_to INTEGER,
            created_at TEXT NOT NULL
        );
        """
    )
    con.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES('schema', ?)", (str(SCHEMA),)
    )


def room_row(con: sqlite3.Connection) -> sqlite3.Row:
    row = con.execute("SELECT * FROM room LIMIT 1").fetchone()
    if row is None:
        die("room is empty")
    return row


def agent_row(con: sqlite3.Connection, name: str | None) -> sqlite3.Row:
    name = name or os.environ.get("AGENT_BOARD_NAME") or ""
    if not name:
        die("tell the board who you are with --as NAME (or set AGENT_BOARD_NAME)")
    safe_name(name)
    row = con.execute("SELECT * FROM agents WHERE name = ?", (name,)).fetchone()
    if row is None:
        die(f"unknown agent '{name}'. Join the room first: board.py join --as {name}")
    if row["retired_at"]:
        die(f"agent '{name}' was retired by the orchestrator")
    con.execute("UPDATE agents SET last_seen = ? WHERE name = ?", (now(), name))
    return row


def require_role(row: sqlite3.Row, roles: tuple[str, ...]) -> None:
    if row["role"] not in roles:
        die(f"'{row['name']}' is {row['role']}; requires {', '.join(roles)}")


def expire_claims(con: sqlite3.Connection) -> None:
    stamp = now()
    con.execute(
        "UPDATE claims SET released_at = ? "
        "WHERE released_at IS NULL AND expires_at IS NOT NULL AND expires_at <= ?",
        (stamp, stamp),
    )


def active_claim(con: sqlite3.Connection, kind: str, value: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM claims WHERE kind = ? AND value = ? AND released_at IS NULL",
        (kind, value),
    ).fetchone()


# --------------------------------------------------------------------------
# resource allocation (the part that used to be a hand-maintained table)
# --------------------------------------------------------------------------

def port_is_free(port: int) -> bool:
    probes = [(socket.AF_INET, ("", port))]
    if socket.has_ipv6:
        probes.append((socket.AF_INET6, ("::", port)))
    for family, address in probes:
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.bind(address)
        except OSError as exc:
            if family == socket.AF_INET6 and getattr(exc, "errno", None) in (97, 99):
                continue
            return False
        finally:
            sock.close()
    return True


def runtime_dir(room: sqlite3.Row) -> Path:
    explicit = os.environ.get("AGENT_BOARD_RUNTIME")
    if explicit:
        path = Path(explicit).expanduser().resolve()
    else:
        path = Path(tempfile.gettempdir()) / f"agent-board-{room['id'][:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def allocate(con: sqlite3.Connection, row: sqlite3.Row, kind: str,
             hint: str | None, ttl: int | None, port_range: str | None,
             create: bool) -> str:
    room = room_row(con)
    stamp = now()
    expires = add_seconds(stamp, ttl) if ttl else None
    hint_slug = slug(hint or row["name"], 40)

    if kind == "port":
        lo, hi = parse_range(port_range) if port_range else (
            room["port_start"], room["port_end"]
        )
        used = {
            int(r["value"])
            for r in con.execute(
                "SELECT value FROM claims WHERE kind='port' AND released_at IS NULL"
            )
        }
        for port in range(lo, hi + 1):
            if port in used:
                continue
            if port_is_free(port):
                value = str(port)
                break
        else:
            die(f"no free port in {lo}-{hi}")
    elif kind == "socket":
        base = runtime_dir(room)
        for i in range(1, 1000):
            candidate = base / (f"{hint_slug}.sock" if i == 1 else f"{hint_slug}-{i}.sock")
            value = str(candidate)
            if not candidate.exists() and not active_claim(con, kind, value):
                break
        else:
            die("could not find a free socket path")
    elif kind in ("path", "worktree"):
        base = Path(room["workspace"])
        base.mkdir(parents=True, exist_ok=True)
        for i in range(1, 1000):
            candidate = base / (hint_slug if i == 1 else f"{hint_slug}-{i}")
            value = str(candidate)
            if not candidate.exists() and not active_claim(con, kind, value):
                break
        else:
            die("could not find a free workspace path")
        if create:
            candidate.mkdir(parents=True, exist_ok=True)
    else:
        if not hint:
            die(f"--hint is required for kind '{kind}'")
        value = hint
        if active_claim(con, kind, value):
            die(f"{kind} '{value}' is already claimed")

    if active_claim(con, kind, str(value)):
        die(f"{kind} '{value}' just got claimed by someone else; retry")
    cur = con.execute(
        "INSERT INTO claims(kind, value, agent, hint, created_at, expires_at) "
        "VALUES(?,?,?,?,?,?)",
        (kind, str(value), row["name"], hint, stamp, expires),
    )
    return json.dumps({
        "id": cur.lastrowid, "kind": kind, "value": str(value),
        "agent": row["name"], "expires_at": expires,
    })


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_init(args) -> None:
    name = safe_name(args.name)
    db = Path(args.db).expanduser().resolve() if args.db else (
        Path.cwd() / ".agent-board" / "room.db"
    )
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db), timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    schema(con)
    if con.execute("SELECT count(*) FROM room").fetchone()[0]:
        die(f"a room already exists at {db}; join it instead")

    lo, hi = parse_range(args.port_range)
    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else (
        db.parent / "workspace"
    )
    workspace.mkdir(parents=True, exist_ok=True)
    room_id = secrets.token_hex(8)
    code = secrets.token_hex(3)
    stamp = now()
    con.execute(
        "INSERT INTO room(id, code, title, goal, workspace, port_start, port_end, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (room_id, code, args.title, read_arg(args.goal), str(workspace), lo, hi, stamp),
    )
    con.execute(
        "INSERT INTO agents(name, role, joined_at, last_seen) VALUES(?,?,?,?)",
        (name, "orchestrator", stamp, stamp),
    )

    pointer = None
    if not args.no_pointer:
        ptr = Path.cwd() / POINTER
        rel = os.path.relpath(db, Path.cwd())
        if ptr.exists() and ptr.read_text().strip() != rel:
            die(f"{ptr} already points at another room; remove it or use --no-pointer")
        ptr.write_text(rel + "\n")
        pointer = str(ptr)

    emit(
        {
            "ok": True,
            "room": room_id,
            "db": str(db),
            "code": code,
            "workspace": str(workspace),
            "pointer": pointer,
            "lines": [
                f"room     {room_id}",
                f"database {db}",
                f"code     {code}   (share only with invited agents)",
                f"workspace {workspace}",
                f"pointer  {pointer or 'none'}",
                "next: board.py plan --set @plan.md   then   board.py task add ...",
            ],
        },
        args.json,
    )
    con.close()


def cmd_join(args) -> None:
    db, source = resolve_db(args.db)
    name = safe_name(args.name)
    con = connect(db)
    room = room_row(con)
    if args.code is not None and args.code != room["code"]:
        die("wrong room code")
    if args.code is None and source in ("argument", "environment"):
        die("pass --code when joining by explicit path (or run inside the room's directory tree)")

    existing = con.execute("SELECT * FROM agents WHERE name = ?", (name,)).fetchone()
    role = args.role
    if existing:
        if existing["retired_at"]:
            die(f"'{name}' was retired; ask the orchestrator to re-admit you")
        role = existing["role"]
    else:
        if role == "orchestrator":
            current = con.execute(
                "SELECT name FROM agents WHERE role='orchestrator' AND retired_at IS NULL"
            ).fetchone()
            if current and not args.force:
                die(f"the room already has an orchestrator ('{current['name']}'); use --force to add another")
        stamp = now()
        con.execute(
            "INSERT INTO agents(name, role, joined_at, last_seen) VALUES(?,?,?,?)",
            (name, role, stamp, stamp),
        )
    con.execute("UPDATE agents SET last_seen = ? WHERE name = ?", (now(), name))
    claims = [
        {"id": r["id"], "kind": r["kind"], "value": r["value"]}
        for r in con.execute(
            "SELECT * FROM claims WHERE agent = ? AND released_at IS NULL", (name,)
        )
    ]
    emit(
        {
            "ok": True,
            "room": room["id"],
            "db": str(db),
            "as": name,
            "role": role,
            "via": source,
            "claims": claims,
            "lines": [
                f"joined room {room['id']} as {name} ({role}) via {source}",
                f"database {db}",
                f"claims   {len(claims)} active",
            ],
        },
        args.json,
    )
    con.close()


def cmd_room(args) -> None:
    db, source = resolve_db(args.db)
    con = connect(db)
    room = room_row(con)
    agents = con.execute(
        "SELECT name, role, last_seen FROM agents WHERE retired_at IS NULL ORDER BY name"
    ).fetchall()
    claims = con.execute(
        "SELECT kind, value, agent FROM claims WHERE released_at IS NULL ORDER BY kind, value"
    ).fetchall()
    payload = {
        "ok": True,
        "room": room["id"],
        "db": str(db),
        "via": source,
        "title": room["title"],
        "agents": [dict(a) for a in agents],
        "claims": [dict(c) for c in claims],
        "lines": [
            f"room     {room['id']} — {room['title']}",
            f"database {db} (found via {source})",
            f"agents   {', '.join(a['name'] + ':' + a['role'] for a in agents)}",
            f"claims   {len(claims)} active",
        ],
    }
    if args.show_code:
        payload["code"] = room["code"]
        payload["lines"].append(f"code     {room['code']}")
    emit(payload, args.json)
    con.close()


def cmd_plan(args) -> None:
    db, _ = resolve_db(args.db)
    con = connect(db)
    if args.set_text is not None:
        row = agent_row(con, args.name)
        require_role(row, ("orchestrator",))
        body = read_arg(args.set_text) or ""
        if not body.strip():
            die("empty plan")
        stamp = now()
        con.execute(
            "INSERT INTO plan(agent, body, created_at) VALUES(?,?,?)",
            (row["name"], body, stamp),
        )
        emit({"ok": True, "plan": body, "lines": [f"plan updated by {row['name']}"]}, args.json)
    else:
        row = con.execute("SELECT * FROM plan ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            die("no plan yet; the orchestrator writes it with `board.py plan --set`")
        emit(
            {
                "ok": True,
                "plan": row["body"],
                "by": row["agent"],
                "at": row["created_at"],
                "lines": [f"# plan ({row['agent']}, {row['created_at']})", "", row["body"]],
            },
            args.json,
        )
    con.close()


def cmd_task(args) -> None:
    db, _ = resolve_db(args.db)
    con = connect(db)
    stamp = now()
    if args.action == "add":
        row = agent_row(con, args.name)
        require_role(row, ("orchestrator",))
        if args.owner:
            safe_name(args.owner)
            if not con.execute("SELECT 1 FROM agents WHERE name = ?", (args.owner,)).fetchone():
                die(f"owner '{args.owner}' has not joined")
        blocked = ",".join(str(int(x)) for x in args.blocked_by.split(",") if x.strip()) or None
        cur = con.execute(
            "INSERT INTO tasks(title, detail, owner, state, blocked_by, created_by, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (args.title, read_arg(args.detail), args.owner, "open", blocked,
             row["name"], stamp, stamp),
        )
        emit(
            {"ok": True, "task": cur.lastrowid,
             "lines": [f"task {cur.lastrowid} added: {args.title}"]},
            args.json,
        )
    elif args.action == "list":
        rows = con.execute(
            "SELECT * FROM tasks WHERE (? IS NULL OR state = ?) ORDER BY id",
            (args.state, args.state),
        ).fetchall()
        lines = [f"{'id':>3}  {'state':8} {'owner':16} title"]
        for r in rows:
            lines.append(f"{r['id']:>3}  {r['state']:8} {(r['owner'] or '-'):16} {r['title']}")
        emit({"ok": True, "tasks": [dict(r) for r in rows], "lines": lines}, args.json)
    elif args.action == "show":
        r = con.execute("SELECT * FROM tasks WHERE id = ?", (args.id,)).fetchone()
        if r is None:
            die(f"no task {args.id}")
        emit(
            {"ok": True, "task": dict(r),
             "lines": [f"# task {r['id']} — {r['title']}", f"state {r['state']} owner {r['owner']}",
                       "", r["detail"] or "", f"note: {r['note'] or '-'}"]},
            args.json,
        )
    elif args.action == "assign":
        row = agent_row(con, args.name)
        require_role(row, ("orchestrator",))
        safe_name(args.owner)
        if not con.execute("SELECT 1 FROM agents WHERE name = ? AND retired_at IS NULL", (args.owner,)).fetchone():
            die(f"owner '{args.owner}' has not joined")
        cur = con.execute(
            "UPDATE tasks SET owner = ?, updated_at = ? WHERE id = ?",
            (args.owner, stamp, args.id),
        )
        if cur.rowcount == 0:
            die(f"no task {args.id}")
        emit({"ok": True, "task": args.id, "owner": args.owner,
              "lines": [f"task {args.id} → {args.owner}"]}, args.json)
    elif args.action in ("next", "claim"):
        row = agent_row(con, args.name)
        target = args.id
        if target is None:
            candidates = con.execute(
                "SELECT * FROM tasks WHERE state != 'done' "
                "AND (owner IS NULL OR owner = ?) ORDER BY id", (row["name"],)
            ).fetchall()
            for cand in candidates:
                if not blocked_open(con, cand):
                    target = cand["id"]
                    break
            if target is None:
                emit({"ok": True, "task": None, "lines": ["no claimable task"]}, args.json)
                con.close()
                return
        item = con.execute("SELECT * FROM tasks WHERE id = ?", (target,)).fetchone()
        if item is None:
            die(f"no task {target}")
        if item["state"] == "done":
            die(f"task {target} is already done")
        if item["owner"] not in (None, row["name"]):
            die(f"task {target} belongs to {item['owner']}")
        if blocked_open(con, item):
            die(f"task {target} is blocked by {item['blocked_by']}")
        con.execute(
            "UPDATE tasks SET owner = ?, state = 'claimed', updated_at = ? WHERE id = ?",
            (row["name"], stamp, target),
        )
        emit(
            {"ok": True, "task": dict(con.execute("SELECT * FROM tasks WHERE id=?", (target,)).fetchone()),
             "lines": [f"claimed task {target}: {item['title']}"]},
            args.json,
        )
    elif args.action == "done":
        row = agent_row(con, args.name)
        item = con.execute("SELECT * FROM tasks WHERE id = ?", (args.id,)).fetchone()
        if item is None:
            die(f"no task {args.id}")
        if row["role"] != "orchestrator" and item["owner"] != row["name"]:
            die(f"task {args.id} belongs to {item['owner'] or 'nobody'}")
        con.execute(
            "UPDATE tasks SET state = 'done', note = ?, updated_at = ? WHERE id = ?",
            (read_arg(args.note), stamp, args.id),
        )
        emit({"ok": True, "task": args.id,
              "lines": [f"task {args.id} done"]}, args.json)
    con.close()


def blocked_open(con: sqlite3.Connection, task: sqlite3.Row) -> bool:
    raw = task["blocked_by"]
    if not raw:
        return False
    for dep in str(raw).split(","):
        dep = dep.strip()
        if not dep:
            continue
        row = con.execute("SELECT state FROM tasks WHERE id = ?", (int(dep),)).fetchone()
        if row is None or row["state"] != "done":
            return True
    return False


def cmd_claim(args) -> None:
    db, _ = resolve_db(args.db)
    con = connect(db)
    row = agent_row(con, args.name)
    with tx(con):
        expire_claims(con)
        data = json.loads(allocate(con, row, args.kind, args.hint, args.ttl,
                                   args.range, args.create))
    emit(
        {**data, "ok": True,
         "lines": [f"{data['kind']} {data['value']}  (claim {data['id']})"]},
        args.json,
    )
    con.close()


def cmd_release(args) -> None:
    db, _ = resolve_db(args.db)
    con = connect(db)
    row = agent_row(con, args.name)
    stamp = now()
    if args.all:
        if args.agent and args.agent != row["name"]:
            require_role(row, ("orchestrator",))
            target = args.agent
        else:
            target = row["name"]
        cur = con.execute(
            "UPDATE claims SET released_at = ? WHERE agent = ? AND released_at IS NULL",
            (stamp, target),
        )
        emit({"ok": True, "released": cur.rowcount,
              "lines": [f"released {cur.rowcount} claim(s) for {target}"]}, args.json)
    else:
        if args.id is None:
            die("pass --id or --all")
        item = con.execute("SELECT * FROM claims WHERE id = ?", (args.id,)).fetchone()
        if item is None:
            die(f"no claim {args.id}")
        if item["released_at"]:
            emit({"ok": True, "lines": [f"claim {args.id} already released"]}, args.json)
        else:
            if item["agent"] != row["name"] and row["role"] != "orchestrator":
                die("that claim belongs to someone else")
            con.execute("UPDATE claims SET released_at = ? WHERE id = ?", (stamp, args.id))
            emit({"ok": True, "lines": [f"released {item['kind']} {item['value']}"]}, args.json)
    con.close()


def cmd_claims(args) -> None:
    db, _ = resolve_db(args.db)
    con = connect(db)
    with tx(con):
        expire_claims(con)
    rows = con.execute(
        "SELECT * FROM claims WHERE (? = 1 OR released_at IS NULL) "
        "AND (? IS NULL OR agent = ?) ORDER BY kind, id",
        (1 if args.all else 0, args.agent, args.agent),
    ).fetchall()
    lines = [f"{'id':>3}  {'kind':8} {'agent':16} value"]
    for r in rows:
        mark = " (released)" if r["released_at"] else (
            f" (expires {r['expires_at']})" if r["expires_at"] else ""
        )
        lines.append(f"{r['id']:>3}  {r['kind']:8} {r['agent']:16} {r['value']}{mark}")
    emit({"ok": True, "claims": [dict(r) for r in rows], "lines": lines}, args.json)
    con.close()


def cmd_post(args) -> None:
    db, _ = resolve_db(args.db)
    con = connect(db)
    row = agent_row(con, args.name)
    question = read_arg(args.next_q)
    if question is None and not args.no_question:
        die("a post ends with exactly one next question (or pass --no-question)")
    body = read_arg(args.body)
    evidence = "\n".join(read_arg(e) or "" for e in (args.evidence or [])) or None
    stamp = now()
    with tx(con):
        if args.task:
            task = con.execute("SELECT * FROM tasks WHERE id = ?", (args.task,)).fetchone()
            if task is None:
                die(f"no task {args.task}")
        n = con.execute("SELECT COALESCE(MAX(n), 0) + 1 FROM posts").fetchone()[0]
        cur = con.execute(
            "INSERT INTO posts(n, agent, title, body, evidence, question, task_id, reply_to, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (n, row["name"], args.title, body, evidence, question,
             args.task, args.reply_to, stamp),
        )
    emit({"ok": True, "post": n, "id": cur.lastrowid,
          "lines": [f"posted #{n}"]}, args.json)
    con.close()


def cmd_log(args) -> None:
    db, _ = resolve_db(args.db)
    con = connect(db)
    rows = con.execute(
        "SELECT * FROM posts WHERE (? IS NULL OR agent = ?) AND (? IS NULL OR id > ?) "
        "ORDER BY id DESC LIMIT ?",
        (args.agent, args.agent, args.since, args.since, args.limit),
    ).fetchall()
    rows = list(reversed(rows))
    lines = []
    for r in rows:
        lines.append(f"#{r['n']}  {r['created_at']}  {r['agent']} — {r['title']}")
        for part in (r["body"], r["evidence"]):
            if part:
                lines.extend("    " + x for x in str(part).splitlines())
        if r["question"]:
            lines.append(f"    Q: {r['question']}")
        lines.append("")
    if not rows:
        lines = ["(no posts yet)"]
    emit({"ok": True, "posts": [dict(r) for r in rows], "lines": lines}, args.json)
    con.close()


def cmd_status(args) -> None:
    db, _ = resolve_db(args.db)
    con = connect(db)
    if args.body is None:
        rows = con.execute(
            "SELECT s.*, a.role FROM status s JOIN agents a ON a.name = s.agent ORDER BY s.agent"
        ).fetchall()
        lines = []
        for r in rows:
            lines.append(f"## {r['agent']} ({r['role']})  {r['updated_at']}")
            lines.append(r["body"])
            lines.append("")
        emit({"ok": True, "status": [dict(r) for r in rows],
              "lines": lines or ["(no status yet)"]}, args.json)
    else:
        row = agent_row(con, args.name)
        body = read_arg(args.body) or ""
        con.execute(
            "INSERT INTO status(agent, body, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(agent) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at",
            (row["name"], body, now()),
        )
        emit({"ok": True, "lines": [f"status updated for {row['name']}"]}, args.json)
    con.close()


def cmd_who(args) -> None:
    db, _ = resolve_db(args.db)
    con = connect(db)
    rows = con.execute(
        "SELECT a.name, a.role, a.last_seen, "
        "(SELECT count(*) FROM claims c WHERE c.agent=a.name AND c.released_at IS NULL) AS claims, "
        "(SELECT count(*) FROM tasks t WHERE t.owner=a.name AND t.state!='done') AS tasks, "
        "(SELECT substr(body,1,60) FROM status s WHERE s.agent=a.name) AS status "
        "FROM agents a WHERE a.retired_at IS NULL ORDER BY a.role, a.name"
    ).fetchall()
    lines = [f"{'agent':18} {'role':13} {'claims':>6} {'tasks':>5}  last seen"]
    for r in rows:
        lines.append(
            f"{r['name']:18} {r['role']:13} {r['claims']:>6} {r['tasks']:>5}  {r['last_seen']}"
        )
    emit({"ok": True, "agents": [dict(r) for r in rows], "lines": lines}, args.json)
    con.close()


def cmd_digest(args) -> None:
    db, _ = resolve_db(args.db)
    con = connect(db)
    room = room_row(con)
    agents = con.execute(
        "SELECT * FROM agents WHERE retired_at IS NULL ORDER BY role, name"
    ).fetchall()
    lines = [f"# digest — {room['title']}", f"room {room['id']}", ""]
    plan = con.execute("SELECT * FROM plan ORDER BY id DESC LIMIT 1").fetchone()
    if plan:
        lines += [f"## plan (by {plan['agent']})", plan["body"], ""]

    lines += ["| agent | role | task | latest post | claims |", "|---|---|---|---|---|"]
    for a in agents:
        task = con.execute(
            "SELECT id, title, state FROM tasks WHERE owner = ? AND state != 'done' ORDER BY id LIMIT 1",
            (a["name"],),
        ).fetchone()
        post = con.execute(
            "SELECT n, title, created_at FROM posts WHERE agent = ? ORDER BY id DESC LIMIT 1",
            (a["name"],),
        ).fetchone()
        claims = con.execute(
            "SELECT count(*) FROM claims WHERE agent = ? AND released_at IS NULL", (a["name"],)
        ).fetchone()[0]
        lines.append(
            f"| {a['name']} | {a['role']} | "
            f"{('#%d %s' % (task['id'], task['state'])) if task else '-'} | "
            f"{('#%d %s' % (post['n'], post['title'])) if post else '-'} | {claims} |"
        )

    lines += ["", "## open questions", ""]
    questions = con.execute(
        "SELECT n, agent, question FROM posts WHERE question IS NOT NULL AND question != '' "
        "ORDER BY id DESC LIMIT 10"
    ).fetchall()
    for q in questions:
        lines.append(f"- #{q['n']} {q['agent']}: {q['question']}")
    if not questions:
        lines.append("- (none)")

    lines += ["", "## tasks", ""]
    for t in con.execute("SELECT * FROM tasks ORDER BY id").fetchall():
        lines.append(
            f"- #{t['id']} [{t['state']}] {t['title']} — {t['owner'] or 'unassigned'}"
        )
    emit({"ok": True, "digest": "\n".join(lines), "lines": lines}, args.json)
    con.close()


def cmd_export(args) -> None:
    db, _ = resolve_db(args.db)
    con = connect(db)
    room = room_row(con)
    out = Path(args.out).expanduser().resolve()
    if out.exists() and any(out.iterdir()) and not args.force:
        die(f"{out} is not empty; pass --force to overwrite board files")
    out.mkdir(parents=True, exist_ok=True)

    agents = con.execute("SELECT * FROM agents ORDER BY name").fetchall()
    plan = con.execute("SELECT * FROM plan ORDER BY id DESC LIMIT 1").fetchone()
    tasks = con.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    claims = con.execute(
        "SELECT * FROM claims WHERE released_at IS NULL ORDER BY kind, value"
    ).fetchall()
    posts = con.execute("SELECT * FROM posts ORDER BY id").fetchall()

    readme = [f"# {room['title']}", "", f"Room `{room['id']}`", ""]
    if room["goal"]:
        readme += ["## Goal", "", room["goal"], ""]
    if plan:
        readme += [f"## Plan (by {plan['agent']})", "", plan["body"], ""]
    readme += ["## Agents", "", "| agent | role | last seen |", "|---|---|---|"]
    for a in agents:
        readme.append(f"| {a['name']} | {a['role']} | {a['last_seen']} |")
    readme += ["", "## Active claims", ""]
    if claims:
        readme += ["| kind | value | agent |", "|---|---|---|"]
        for c in claims:
            readme.append(f"| {c['kind']} | `{c['value']}` | {c['agent']} |")
    else:
        readme.append("(none)")
    readme += ["", "## Tasks", ""]
    for t in tasks:
        readme.append(f"- #{t['id']} [{t['state']}] {t['title']} — {t['owner'] or 'unassigned'}")
    (out / "README.md").write_text("\n".join(readme) + "\n")

    status_rows = con.execute("SELECT * FROM status ORDER BY agent").fetchall()
    for s in status_rows:
        (out / f"{slug(s['agent'])}-status.md").write_text(
            f"# {s['agent']} status\n\nUpdated: {s['updated_at']}\n\n{s['body']}\n"
        )
    for p in posts:
        fname = f"{slug(p['agent'])}-{p['n']:02d}-{slug(p['title'], 40)}.md"
        body = [f"# {p['title']}", "", f"_{p['agent']}, {p['created_at']}_", ""]
        if p["body"]:
            body += [p["body"], ""]
        if p["evidence"]:
            body += ["## Evidence", "", "```", p["evidence"], "```", ""]
        if p["question"]:
            body += ["## Next question", "", p["question"], ""]
        (out / fname).write_text("\n".join(body) + "\n")

    emit({"ok": True, "out": str(out),
          "lines": [f"exported {len(posts)} post(s) and {len(status_rows)} status file(s) to {out}"]},
         args.json)
    con.close()


def cmd_retire(args) -> None:
    db, _ = resolve_db(args.db)
    con = connect(db)
    row = agent_row(con, args.name)
    require_role(row, ("orchestrator",))
    target = safe_name(args.agent)
    if target == row["name"]:
        die("the orchestrator cannot retire itself")
    item = con.execute("SELECT * FROM agents WHERE name = ?", (target,)).fetchone()
    if item is None:
        die(f"unknown agent '{target}'")
    stamp = now()
    with tx(con):
        con.execute("UPDATE agents SET retired_at = ? WHERE name = ?", (stamp, target))
        con.execute(
            "UPDATE claims SET released_at = ? WHERE agent = ? AND released_at IS NULL",
            (stamp, target),
        )
        con.execute(
            "UPDATE tasks SET owner = NULL, state = 'open', updated_at = ? "
            "WHERE owner = ? AND state != 'done'",
            (stamp, target),
        )
    emit({"ok": True, "lines": [f"retired {target}; claims released, tasks reopened"]}, args.json)
    con.close()


def cmd_seed(args) -> None:
    """Create a disposable room with generic names, for demos and tests."""
    base = Path(args.db).expanduser().resolve() if args.db else (
        Path(tempfile.mkdtemp(prefix="agent-board-seed-")) / "room.db"
    )
    base.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(base), timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    schema(con)
    if con.execute("SELECT count(*) FROM room").fetchone()[0]:
        die(f"room already exists at {base}")

    names = args.agents
    if not names:
        names = f"{args.count}"
    if names.isdigit():
        total = max(2, int(names))
        names = [f"agent-{i + 1}" for i in range(total)]
    else:
        names = [safe_name(x.strip()) for x in names.split(",") if x.strip()]
    if len(names) < 2:
        die("seed needs at least two agents")

    stamp = now()
    lo, hi = parse_range(args.port_range)
    workspace = base.parent / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    con.execute(
        "INSERT INTO room(id, code, title, goal, workspace, port_start, port_end, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (secrets.token_hex(8), secrets.token_hex(3), args.title, args.goal,
         str(workspace), lo, hi, stamp),
    )
    con.execute(
        "INSERT INTO agents(name, role, joined_at, last_seen) VALUES(?,?,?,?)",
        (names[0], "orchestrator", stamp, stamp),
    )
    for name in names[1:]:
        con.execute(
            "INSERT INTO agents(name, role, joined_at, last_seen) VALUES(?,?,?,?)",
            (name, "worker", stamp, stamp),
        )
    con.execute(
        "INSERT INTO plan(agent, body, created_at) VALUES(?,?,?)",
        (names[0], args.plan, stamp),
    )
    for i, name in enumerate(names[1:], start=1):
        con.execute(
            "INSERT INTO tasks(title, detail, owner, state, created_by, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (f"task {i}", "seeded task", name, "open", names[0], stamp, stamp),
        )
    for i in range(args.posts):
        agent = names[1 + (i % max(1, len(names) - 1))]
        n = i + 1
        con.execute(
            "INSERT INTO posts(n, agent, title, body, evidence, question, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (n, agent, f"seeded post {n}", "demo body", "/dev/null",
             f"seeded question {n}?", stamp),
        )
    con.close()
    emit({"ok": True, "db": str(base),
          "lines": [f"seeded room at {base}", f"agents: {', '.join(names)}"]}, args.json)


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="room database (default: discover)")
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("--as", dest="name", help="agent identity")

    p = argparse.ArgumentParser(
        prog="board",
        description="One SQLite room, created and planned by an orchestrator.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    q = sub.add_parser("init", parents=[common], help="create a room (orchestrator)")
    q.add_argument("--title", default="Agent room")
    q.add_argument("--goal", help="goal text, @file or '-'")
    q.add_argument("--workspace", help="directory for path/worktree claims")
    q.add_argument("--port-range", default="12000-12999")
    q.add_argument("--no-pointer", action="store_true")
    q.set_defaults(func=cmd_init)

    q = sub.add_parser("join", parents=[common], help="join an existing room")
    q.add_argument("--role", choices=ROLES, default="worker")
    q.add_argument("--code", help="room code (required when joining by explicit path)")
    q.add_argument("--force", action="store_true", help="allow a second orchestrator")
    q.set_defaults(func=cmd_join)

    q = sub.add_parser("room", parents=[common], help="show the discovered room")
    q.add_argument("--show-code", action="store_true")
    q.set_defaults(func=cmd_room)

    q = sub.add_parser("plan", parents=[common], help="write or show the plan")
    q.add_argument("--set", dest="set_text", help="plan text, @file or '-'")
    q.set_defaults(func=cmd_plan)

    q = sub.add_parser("task", parents=[common], help="plan tasks")
    q.add_argument("action", choices=("add", "list", "show", "assign", "next", "claim", "done"))
    q.add_argument("--id", type=int)
    q.add_argument("--title")
    q.add_argument("--detail")
    q.add_argument("--owner")
    q.add_argument("--state", choices=("open", "claimed", "done"))
    q.add_argument("--blocked-by", default="")
    q.add_argument("--note")
    q.set_defaults(func=cmd_task)

    q = sub.add_parser("claim", parents=[common], help="allocate a resource")
    q.add_argument("--kind", choices=KINDS, required=True)
    q.add_argument("--hint", help="human label used in allocated names")
    q.add_argument("--ttl", type=int, help="seconds until the claim expires")
    q.add_argument("--range", help="port range for kind=port")
    q.add_argument("--create", action="store_true", help="create the directory for path/worktree")
    q.set_defaults(func=cmd_claim)

    q = sub.add_parser("release", parents=[common], help="release claims")
    q.add_argument("--id", type=int)
    q.add_argument("--all", action="store_true")
    q.add_argument("--agent", help="release another agent's claims (orchestrator)")
    q.set_defaults(func=cmd_release)

    q = sub.add_parser("claims", parents=[common], help="list claims")
    q.add_argument("--agent")
    q.add_argument("--all", action="store_true")
    q.set_defaults(func=cmd_claims)

    q = sub.add_parser("post", parents=[common], help="append a post")
    q.add_argument("--title", required=True)
    q.add_argument("--body")
    q.add_argument("--evidence", action="append")
    q.add_argument("--next", dest="next_q")
    q.add_argument("--no-question", action="store_true")
    q.add_argument("--task", type=int)
    q.add_argument("--reply-to", type=int)
    q.set_defaults(func=cmd_post)

    q = sub.add_parser("log", parents=[common], help="read posts")
    q.add_argument("--limit", type=int, default=20)
    q.add_argument("--agent")
    q.add_argument("--since", type=int, help="only posts with id greater than this")
    q.set_defaults(func=cmd_log)

    q = sub.add_parser("status", parents=[common], help="write or show snapshots")
    q.add_argument("--body")
    q.set_defaults(func=cmd_status)

    q = sub.add_parser("who", parents=[common], help="list agents")
    q.set_defaults(func=cmd_who)

    q = sub.add_parser("digest", parents=[common], help="summarize the room")
    q.set_defaults(func=cmd_digest)

    q = sub.add_parser("export", parents=[common], help="export markdown for humans")
    q.add_argument("--out", required=True)
    q.add_argument("--force", action="store_true")
    q.set_defaults(func=cmd_export)

    q = sub.add_parser("retire", parents=[common], help="retire an agent (orchestrator)")
    q.add_argument("--agent", required=True)
    q.set_defaults(func=cmd_retire)

    q = sub.add_parser("seed", parents=[common], help="create a disposable demo room")
    q.add_argument("--agents", help="comma-separated names, or a count")
    q.add_argument("--count", type=int, default=3)
    q.add_argument("--posts", type=int, default=3)
    q.add_argument("--title", default="Seeded room")
    q.add_argument("--goal", default="demo")
    q.add_argument("--plan", default="1. do the thing\n2. post evidence")
    q.add_argument("--port-range", default="12000-12999")
    q.set_defaults(func=cmd_seed)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
