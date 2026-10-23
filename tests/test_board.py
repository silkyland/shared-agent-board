#!/usr/bin/env python3
"""End-to-end tests for scripts/board.py (stdlib only)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BOARD = Path(__file__).resolve().parents[1] / "scripts" / "board.py"


class BoardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.env = dict(os.environ)
        for key in ("AGENT_BOARD_DB", "AGENT_BOARD_NAME", "AGENT_BOARD_RUNTIME"):
            self.env.pop(key, None)

    def tearDown(self):
        self._tmp.cleanup()

    def run_board(self, *args, cwd=None, ok=True, stdin=None):
        proc = subprocess.run(
            [sys.executable, str(BOARD), *args],
            cwd=str(cwd or self.dir),
            env=self.env,
            capture_output=True,
            text=True,
            input=stdin,
        )
        if ok and proc.returncode != 0:
            self.fail(f"{args} failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        return proc

    def run_json(self, *args, cwd=None, ok=True):
        proc = self.run_board(*args, "--json", cwd=cwd, ok=ok)
        return json.loads(proc.stdout)

    def init_room(self):
        data = self.run_json("init", "--as", "lead", "--title", "Room", "--goal", "goal")
        return data

    def test_orchestrator_plans_and_worker_executes(self):
        room = self.init_room()
        self.assertTrue(Path(room["db"]).is_file())
        self.assertTrue((self.dir / ".agentboard").is_file())

        self.run_board("join", "--as", "worker-1")
        self.run_board("plan", "--as", "lead", "--set", "1. probe\n2. report")
        self.run_board("task", "add", "--as", "lead", "--title", "probe",
                       "--owner", "worker-1")
        claimed = self.run_json("task", "next", "--as", "worker-1")
        self.assertEqual(claimed["task"]["id"], 1)

        first = self.run_json("claim", "--as", "worker-1", "--kind", "port", "--hint", "gdb")
        second = self.run_json("claim", "--as", "lead", "--kind", "port", "--hint", "gdb")
        self.assertNotEqual(first["value"], second["value"])

        sock = self.run_json("claim", "--as", "worker-1", "--kind", "socket", "--hint", "qmp")
        self.assertTrue(sock["value"].endswith(".sock"))
        work = self.run_json("claim", "--as", "worker-1", "--kind", "path",
                             "--hint", "lane", "--create")
        self.assertTrue(Path(work["value"]).is_dir())

        self.run_board("post", "--as", "worker-1", "--title", "probe ok",
                       "--body", "ran it", "--evidence", str(self.dir / "x.log"),
                       "--next", "what next?", "--task", "1")
        log = self.run_json("log", "--limit", "5")
        self.assertEqual(len(log["posts"]), 1)
        self.assertEqual(log["posts"][0]["question"], "what next?")

        self.run_board("status", "--as", "worker-1", "--body", "probe works")
        self.run_board("task", "done", "--as", "worker-1", "--id", "1", "--note", "verified")

        digest = self.run_board("digest").stdout
        self.assertIn("worker-1", digest)
        self.assertIn("probe ok", digest)

    def test_discovery_walks_up(self):
        room = self.init_room()
        nested = self.dir / "a" / "b"
        nested.mkdir(parents=True)
        data = self.run_json("room", cwd=nested)
        self.assertEqual(data["room"], room["room"])
        self.assertIn("pointer", data["via"])

    def test_explicit_path_requires_code(self):
        room = self.init_room()
        proc = self.run_board("join", "--as", "stranger", "--db", room["db"], ok=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--code", proc.stderr)

        bad = self.run_board("join", "--as", "stranger", "--db", room["db"],
                             "--code", "nope", ok=False)
        self.assertNotEqual(bad.returncode, 0)

        self.run_json("join", "--as", "stranger", "--db", room["db"],
                      "--code", room["code"])
        self.assertIn("stranger", self.run_board("who").stdout)

    def test_seed_builds_disposable_room(self):
        data = self.run_json("seed", "--agents", "3", "--posts", "2")
        self.assertTrue(Path(data["db"]).is_file())
        log = self.run_json("log", "--db", data["db"], "--limit", "10")
        self.assertEqual(len(log["posts"]), 2)

    def test_retire_releases_claims_and_reopens_tasks(self):
        room = self.init_room()
        self.run_board("join", "--as", "worker-1")
        self.run_json("claim", "--as", "worker-1", "--kind", "port", "--hint", "x")
        self.run_board("task", "add", "--as", "lead", "--title", "t",
                       "--owner", "worker-1")
        self.run_board("retire", "--as", "lead", "--agent", "worker-1")
        claims = self.run_json("claims", "--db", room["db"])
        self.assertEqual(claims["claims"], [])
        tasks = self.run_json("task", "list", "--db", room["db"])
        self.assertEqual(tasks["tasks"][0]["state"], "open")
        self.assertIsNone(tasks["tasks"][0]["owner"])

    def test_export_writes_markdown(self):
        room = self.init_room()
        self.run_board("join", "--as", "worker-1")
        self.run_board("post", "--as", "worker-1", "--title", "one",
                       "--evidence", "/dev/null", "--next", "q?")
        out = self.dir / "export"
        self.run_board("export", "--out", str(out))
        names = sorted(p.name for p in out.iterdir())
        self.assertIn("README.md", names)
        self.assertTrue(any(n.startswith("worker-1-01-") for n in names))

    def test_identity_from_environment(self):
        room = self.init_room()
        self.env["AGENT_BOARD_NAME"] = "worker-1"
        self.run_board("join", "--as", "worker-1")
        self.run_board("post", "--title", "env identity",
                       "--body", "no --as needed", "--next", "ok?")
        log = self.run_json("log", "--db", room["db"])
        self.assertEqual(log["posts"][0]["agent"], "worker-1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
