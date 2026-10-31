#!/usr/bin/env python3
"""End-to-end tests for scripts/arena.py and scripts/webboard.py (stdlib only)."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ARENA = SCRIPTS / "arena.py"
BOARD = SCRIPTS / "board.py"
sys.path.insert(0, str(SCRIPTS))


class ArenaTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("AGENT_BOARD")}
        self.git("init", "-q")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
        (self.dir / ".gitignore").write_text(".agent-board/\n")
        self.git("add", ".")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "ig")
        self.evidence = self.dir / "ev.log"
        self.evidence.write_text("observed\n")

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *a):
        subprocess.run(["git", *a], cwd=self.dir, check=True, capture_output=True)

    def run_py(self, script, *args, env=None, ok=True):
        proc = subprocess.run([sys.executable, str(script), *args], cwd=str(self.dir),
                              env=env or self.env, capture_output=True, text=True)
        if ok and proc.returncode != 0:
            self.fail(f"{args}:\n{proc.stdout}\n{proc.stderr}")
        return proc

    def arena(self, *args, who=None, ok=True):
        env = dict(self.env)
        if who:
            env.update(self.tokens[who])
        return self.run_py(ARENA, *args, "--json", env=env, ok=ok)

    def aj(self, *args, who=None):
        return json.loads(self.arena(*args, who=who).stdout)

    def start(self, lanes="alpha,beta,gamma"):
        out = json.loads(self.run_py(ARENA, "init", "--as", "lead", "--title", "T", "--goal", "g",
                                     "--lanes", lanes, "--json").stdout)
        self.db = out["db"]
        self.tokens = {}
        for lane in out["lanes"]:
            env = {}
            for line in Path(lane["env"]).read_text().splitlines():
                k, _, v = line.replace("export ", "").partition("=")
                env[k] = v.strip("'")
            self.tokens[lane["agent"]] = env
        return out

    def test_lanes_are_real_worktrees(self):
        out = self.start()
        for lane in out["lanes"]:
            self.assertTrue((Path(lane["worktree"]) / ".git").exists())
            self.assertTrue(lane["branch"].startswith("arena/"))
            self.assertIn("MISSION", Path(lane["brief"]).read_text().upper())
        listing = subprocess.run(["git", "worktree", "list"], cwd=self.dir, capture_output=True, text=True).stdout
        self.assertGreaterEqual(listing.count("alpha") + listing.count("beta") + listing.count("gamma"), 3)

    def test_finding_points_need_a_real_evidence_file(self):
        self.start()
        got = self.aj("post", "--kind", "finding", "--title", "with file", "--evidence", str(self.evidence),
                      "--next", "q", who="alpha")
        self.assertEqual(got["points"], 3)
        got = self.aj("post", "--kind", "finding", "--title", "words only", "--evidence", "trust me",
                      "--next", "q", who="alpha")
        self.assertEqual(got["points"], 0)
        got = self.aj("post", "--kind", "negative", "--title", "ruled out", "--evidence", str(self.evidence),
                      who="alpha")
        self.assertEqual(got["points"], 2)
        scores = {s["agent"]: s["points"] for s in self.aj("score")["scores"]}
        self.assertEqual(scores["alpha"], 5)

    def test_qa_flow_and_open_question_state(self):
        self.start()
        q = self.aj("ask", "--title", "how?", who="alpha")["post"]
        picked = self.aj("pick", who="beta")["questions"]
        self.assertEqual([x["n"] for x in picked], [q])
        self.assertEqual(self.aj("pick", who="alpha")["questions"], [])
        a = self.aj("answer", "--to", str(q), "--title", "like so", "--body", "b", who="beta")["post"]
        bad = self.arena("accept", "--post", str(a), who="gamma", ok=False)
        self.assertNotEqual(bad.returncode, 0)
        self.arena("accept", "--post", str(a), who="alpha")
        self.assertEqual(self.aj("pick", who="gamma")["questions"], [])
        scores = {s["agent"]: s["points"] for s in self.aj("score")["scores"]}
        self.assertEqual((scores["beta"], scores["alpha"]), (3, 1))
        q2 = self.aj("ask", "--title", "still open", who="alpha")["post"]
        self.assertIn("(open question): still open", self.run_py(BOARD, "digest", "--as", "lead").stdout)
        self.aj("answer", "--to", str(q2), "--title", "r", who="beta")
        self.arena("accept", "--post", str(q2 + 1), who="alpha")
        digest = self.run_py(BOARD, "digest", "--as", "lead").stdout
        self.assertIn("- (none)", digest)

    def test_review_rules(self):
        self.start()
        n = self.aj("post", "--title", "f", "--evidence", str(self.evidence), "--next", "q", who="alpha")["post"]
        self.assertNotEqual(self.arena("review", "confirm", "--post", str(n),
                                       "--evidence", str(self.evidence), who="alpha", ok=False).returncode, 0)
        self.assertNotEqual(self.arena("review", "confirm", "--post", str(n),
                                       "--evidence", "no file", who="beta", ok=False).returncode, 0)
        self.arena("review", "confirm", "--post", str(n), "--evidence", str(self.evidence), who="beta")
        self.assertNotEqual(self.arena("review", "refute", "--post", str(n),
                                       "--evidence", str(self.evidence), who="beta", ok=False).returncode, 0)
        self.arena("review", "refute", "--post", str(n), "--evidence", str(self.evidence), who="gamma")
        scores = {s["agent"]: s["points"] for s in self.aj("score")["scores"]}
        self.assertEqual(scores["alpha"], 3 + 2 - 3)
        self.assertEqual((scores["beta"], scores["gamma"]), (2, 3))

    def test_orchestrator_award_and_revoke(self):
        self.start()
        self.run_py(ARENA, "award", "--as", "lead", "--agent", "alpha", "--points", "4", "--reason", "depth")
        self.assertNotEqual(self.arena("award", "--agent", "alpha", "--points", "4", "--reason", "x",
                                       who="beta", ok=False).returncode, 0)
        ledger = self.aj("score", "--ledger")
        self.assertEqual({s["agent"]: s["points"] for s in ledger["scores"]}["alpha"], 4)
        self.run_py(ARENA, "award", "--as", "lead", "--revoke", "1")
        rows = sqlite3.connect(self.db).execute("SELECT count(*), SUM(points) FROM ledger").fetchone()
        self.assertEqual(rows, (2, 0))

    def test_hourly_cap(self):
        self.start()
        for i in range(12):
            self.aj("post", "--title", f"f{i}", "--evidence", str(self.evidence), "--next", "q", who="alpha")
        scores = {s["agent"]: s["points"] for s in self.aj("score")["scores"]}
        self.assertEqual(scores["alpha"], 30)

    def test_token_blocks_impersonation_and_boards_open(self):
        self.start()
        env = dict(self.env, AGENT_BOARD_DB=self.db, AGENT_BOARD_NAME="alpha", AGENT_BOARD_TOKEN="wrong")
        bad = self.run_py(ARENA, "ask", "--title", "x", env=env, ok=False)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("token", bad.stderr.lower())
        self.aj("boards", "open", "--slug", "printer", "--title", "Printer", who="beta")
        got = self.aj("post", "--board", "printer", "--title", "t", "--evidence", str(self.evidence),
                      "--next", "q", who="gamma")
        self.assertEqual(got["points"], 3)
        slugs = [b["slug"] for b in self.aj("boards", "list")["boards"]]
        self.assertEqual(slugs, ["main", "printer"])

    def test_duplicate_post_rejected(self):
        self.start()
        self.aj("ask", "--title", "same", who="alpha")
        self.assertNotEqual(self.arena("ask", "--title", "same", who="alpha", ok=False).returncode, 0)

    def test_stop_signals_only_recorded_group(self):
        self.start()
        bystander = subprocess.Popen(["sleep", "30"])
        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            con = sqlite3.connect(self.db)
            con.execute("UPDATE lanes SET pid=? WHERE agent='alpha'", (proc.pid,))
            con.commit()
            out = self.run_py(ARENA, "stop", "--as", "lead", "--agent", "alpha").stdout
            self.assertIn(str(proc.pid), out)
            proc.wait(timeout=5)
            self.assertIsNone(bystander.poll())
        finally:
            bystander.kill(); proc.kill()
            bystander.wait(); proc.wait()

    def test_run_dry_and_custom_engine(self):
        self.start()
        out = self.run_py(ARENA, "run", "--as", "lead", "--engine", "codex", "--dry-run").stdout
        self.assertIn("codex exec -C", out)
        out = self.run_py(ARENA, "run", "--as", "lead", "--agent", "alpha", "--engine", "custom",
                          "--cmd", "sh -c 'board_post() { :; }; echo ran-$AGENT_BOARD_NAME'").stdout
        self.assertIn("started custom", out)

    def test_finalize_matches_score(self):
        self.start()
        n = self.aj("post", "--title", "good", "--evidence", str(self.evidence), "--next", "q", who="alpha")["post"]
        self.arena("review", "confirm", "--post", str(n), "--evidence", str(self.evidence), who="beta")
        self.aj("post", "--kind", "negative", "--title", "nope", "--evidence", str(self.evidence), who="gamma")
        lane = json.loads(self.run_py(ARENA, "lane", "list", "--as", "lead", "--json").stdout)["lanes"][0]
        (Path(lane["worktree"]) / "probe.py").write_text("print(1)\n")
        out = self.dir / "report"
        res = self.run_py(ARENA, "finalize", "--as", "lead", "--out", str(out), "--json")
        scores = json.loads(res.stdout)["scores"]
        self.assertEqual(scores, self.aj("score")["scores"])
        report = (out / "ARENA-REPORT.md").read_text()
        self.assertIn("[accepted]** good", report)
        self.assertIn("nope", report)
        self.assertIn("probe.py", report)
        self.assertIn("good", (out / "findings.md").read_text())

    def test_v3_room_upgrades_in_place(self):
        db = self.dir / "old" / "room.db"
        db.parent.mkdir()
        con = sqlite3.connect(db)
        con.executescript("""
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE room(id TEXT PRIMARY KEY, code TEXT NOT NULL, title TEXT NOT NULL, goal TEXT,
                workspace TEXT NOT NULL, port_start INTEGER NOT NULL, port_end INTEGER NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE agents(name TEXT PRIMARY KEY, role TEXT NOT NULL, joined_at TEXT NOT NULL,
                last_seen TEXT NOT NULL, retired_at TEXT);
            CREATE TABLE posts(id INTEGER PRIMARY KEY AUTOINCREMENT, n INTEGER NOT NULL, agent TEXT NOT NULL,
                title TEXT NOT NULL, body TEXT, evidence TEXT, question TEXT, task_id INTEGER, reply_to INTEGER,
                created_at TEXT NOT NULL);
            INSERT INTO meta VALUES('schema','3');
            INSERT INTO room VALUES('r1','c','Old','g','/tmp/w',12000,12999,'2026-01-01T00:00:00+00:00');
            INSERT INTO agents VALUES('lead','orchestrator','t','t',NULL);
            INSERT INTO posts(n,agent,title,created_at) VALUES(1,'lead','old post','2026-01-01T00:00:00+00:00');
        """)
        con.commit(); con.close()
        out = self.run_py(BOARD, "log", "--db", str(db), "--json")
        self.assertEqual(json.loads(out.stdout)["posts"][0]["title"], "old post")
        con = sqlite3.connect(db)
        self.assertEqual(con.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0], "4")
        self.assertEqual(con.execute("SELECT kind, board_id FROM posts").fetchone(), ("note", 1))
        self.assertEqual(con.execute("SELECT slug FROM boards").fetchone()[0], "main")
        self.assertTrue(Path(str(db) + ".v3.bak").is_file())

    def test_webboard_api_readonly_and_inert(self):
        import webboard
        self.start()
        self.aj("post", "--title", "<script>alert(1)</script>", "--evidence", str(self.evidence),
                "--next", "q", who="alpha")
        server = webboard.ThreadingHTTPServer(("127.0.0.1", 0), webboard.make_handler(Path(self.db)))
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{port}"
            posts = json.load(urllib.request.urlopen(base + "/api/posts"))
            self.assertEqual(posts[0]["title"], "<script>alert(1)</script>")
            self.assertEqual(json.load(urllib.request.urlopen(base + "/api/leaderboard"))[0]["agent"], "alpha")
            thread = json.load(urllib.request.urlopen(base + "/api/thread/1"))
            self.assertEqual(thread[0]["n"], 1)
            page = urllib.request.urlopen(base + "/").read().decode()
            self.assertNotIn("innerHTML", page)
            req = urllib.request.Request(base + "/api/posts", method="POST", data=b"{}")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req)
            self.assertEqual(ctx.exception.code, 405)
            self.assertEqual(server.server_address[0], "127.0.0.1")
        finally:
            server.shutdown(); server.server_close()


if __name__ == "__main__":
    unittest.main()
