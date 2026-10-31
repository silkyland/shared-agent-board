#!/usr/bin/env python3
"""Read-only webboard for an arena room. Standard library only.

Binds to 127.0.0.1 only and exposes no write endpoint: agents write through the
CLI; people watch here. The page is `web/index.html` (no build, no CDN) and
updates live through Server-Sent Events.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arena as A  # noqa: E402
import board as B  # noqa: E402

WEB = Path(__file__).resolve().parents[1] / "web" / "index.html"
HOST = "127.0.0.1"


def ro(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def rows(con, sql, params=()):
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def api(db: Path, path: str, query: dict):
    con = ro(db)
    try:
        if path == "/api/state":
            room = dict(con.execute("SELECT id, title, created_at FROM room").fetchone())
            goal = con.execute("SELECT body FROM goals ORDER BY id DESC LIMIT 1").fetchone()
            plan = con.execute("SELECT body FROM plan ORDER BY id DESC LIMIT 1").fetchone()
            room.update(
                goal=goal[0] if goal else None,
                plan=plan[0] if plan else None,
                posts=con.execute("SELECT count(*) FROM posts").fetchone()[0],
                open_questions=len(A.open_questions(con)),
                now=B.now(),
            )
            return room
        if path == "/api/boards":
            return rows(con, "SELECT b.slug, b.title, b.description, b.opened_by, b.closed_at, b.parent_id, "
                             "(SELECT count(*) FROM posts p WHERE p.board_id=b.id) AS posts FROM boards b ORDER BY b.id")
        if path == "/api/posts":
            where, params = ["1=1"], []
            if query.get("board"):
                where.append("b.slug=?"); params.append(query["board"])
            if query.get("kind"):
                where.append("p.kind=?"); params.append(query["kind"])
            limit = min(int(query.get("limit", 100)), 500)
            return rows(con,
                "SELECT p.n, p.agent, p.title, p.kind, p.created_at, p.accepted_at, b.slug AS board, "
                "(SELECT n FROM posts r WHERE r.id=p.reply_to) AS reply_to, "
                "(SELECT count(*) FROM posts a WHERE a.reply_to=p.id) AS replies, "
                "(SELECT count(*) FROM reviews v WHERE v.post_id=p.id AND v.verdict='confirm') AS confirms, "
                "(SELECT count(*) FROM reviews v WHERE v.post_id=p.id AND v.verdict='refute') AS refutes, "
                "(SELECT count(*) FROM posts a WHERE a.reply_to=p.id AND a.kind='answer' AND a.accepted_at IS NOT NULL) AS solved "
                f"FROM posts p JOIN boards b ON b.id=p.board_id WHERE {' AND '.join(where)} "
                "ORDER BY p.id DESC LIMIT ?", (*params, limit))
        if path.startswith("/api/thread/"):
            n = int(path.rsplit("/", 1)[1])
            root = con.execute("SELECT id FROM posts WHERE n=?", (n,)).fetchone()
            if root is None:
                return None
            seen, queue, out = set(), [root[0]], []
            while queue:
                pid = queue.pop(0)
                if pid in seen:
                    continue
                seen.add(pid)
                out += rows(con,
                    "SELECT p.n, p.agent, p.title, p.body, p.evidence, p.question, p.kind, p.created_at, "
                    "p.accepted_at, p.lane, b.slug AS board, (SELECT n FROM posts r WHERE r.id=p.reply_to) AS reply_to "
                    "FROM posts p JOIN boards b ON b.id=p.board_id WHERE p.id=?", (pid,))
                out[-1]["reviews"] = rows(con, "SELECT reviewer, verdict, note, evidence, created_at FROM reviews WHERE post_id=?", (pid,))
                queue += [r[0] for r in con.execute("SELECT id FROM posts WHERE reply_to=? ORDER BY id", (pid,))]
            return out
        if path == "/api/leaderboard":
            return A.scoreboard(con)
        if path == "/api/ledger":
            return rows(con, "SELECT l.id, l.agent, l.points, l.reason, l.by, l.created_at, "
                             "(SELECT n FROM posts p WHERE p.id=l.post_id) AS post "
                             "FROM ledger l ORDER BY l.id DESC LIMIT 100")
        if path == "/api/lanes":
            out = rows(con, "SELECT agent, branch, worktree, engine, pid, started_at, ended_at FROM lanes ORDER BY agent")
            for l in out:
                last = con.execute("SELECT n, title, created_at FROM posts WHERE agent=? ORDER BY id DESC LIMIT 1",
                                   (l["agent"],)).fetchone()
                l["last_post"] = dict(last) if last else None
                l["running"] = A.pid_alive(l.pop("pid"))
                l["posts"] = con.execute("SELECT count(*) FROM posts WHERE agent=?", (l["agent"],)).fetchone()[0]
                l["points"] = con.execute("SELECT COALESCE(SUM(points),0) FROM ledger WHERE agent=?", (l["agent"],)).fetchone()[0]
            return out
        return None
    finally:
        con.close()


def make_handler(db: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path in ("/", "/index.html"):
                return self.send(200, WEB.read_bytes(), "text/html; charset=utf-8")
            if url.path == "/events":
                return self.events()
            if url.path.startswith("/api/"):
                try:
                    data = api(db, url.path, query)
                except (ValueError, sqlite3.Error) as exc:
                    return self.send(400, json.dumps({"error": str(exc)}), "application/json")
                if data is None:
                    return self.send(404, json.dumps({"error": "not found"}), "application/json")
                return self.send(200, json.dumps(data), "application/json")
            self.send(404, "not found", "text/plain")

        def do_POST(self):
            self.send(405, "read-only", "text/plain")

        do_PUT = do_DELETE = do_PATCH = do_POST

        def events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            con = ro(db)
            last = None
            try:
                while True:
                    version = con.execute("PRAGMA data_version").fetchone()[0]
                    total = con.execute("SELECT (SELECT COALESCE(MAX(id),0) FROM posts)||'/'||"
                                        "(SELECT COALESCE(MAX(id),0) FROM ledger)||'/'||(SELECT count(*) FROM boards)").fetchone()[0]
                    key = (version, total)
                    if key != last:
                        last = key
                        self.wfile.write(b"event: change\ndata: 1\n\n")
                    else:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    time.sleep(0.5)
            except (BrokenPipeError, ConnectionResetError, sqlite3.Error):
                pass
            finally:
                con.close()

    return Handler


def cmd_serve(args):
    db, con = A.session(args)
    port = args.port
    if port is None:
        row = B.agent_row(con, args.name)
        with B.tx(con):
            claim = json.loads(B.allocate(con, row, "port", "webboard", args.ttl, None, False))
        port = int(claim["value"])
    server = ThreadingHTTPServer((HOST, port), make_handler(db))
    server.daemon_threads = True
    print(f"webboard http://{HOST}:{port}/  (read-only, room {B.room_row(con)['id']})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def main(argv=None):
    p = argparse.ArgumentParser(prog="webboard")
    sub = p.add_subparsers(dest="command", required=True)
    q = sub.add_parser("serve", help="serve the read-only webboard on 127.0.0.1")
    q.add_argument("--db")
    q.add_argument("--as", dest="name")
    q.add_argument("--port", type=int, help="fixed port (default: claimed from the room's range)")
    q.add_argument("--ttl", type=int, help="seconds before the port claim expires")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_serve)
    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
