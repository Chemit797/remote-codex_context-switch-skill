import importlib.util
import io
import json
import subprocess
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("codex_fuel_test_module", ROOT / "scripts" / "codex_fuel.py")
fuel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fuel)

KEY_A = "sk-" + "a" * 42 + "AAAA1"
KEY_B = "sk-" + "b" * 42 + "BBBB2"
RELAY_CONFIG = """model_provider = "relay"
model = "gpt-x"

[model_providers.relay]
name = "relay"
base_url = "https://relay.example/v1"
wire_api = "responses"
requires_openai_auth = true
"""


def make_home(config="", auth=None, raw_auth=None, tags=None):
    home = Path(tempfile.mkdtemp())
    (home / "config.toml").write_text(config, encoding="utf-8")
    if raw_auth is not None:
        (home / "auth.json").write_bytes(raw_auth)
    elif auth is not None:
        (home / "auth.json").write_text(json.dumps(auth), encoding="utf-8")
    if tags is not None:
        con = sqlite3.connect(home / "state_5.sqlite")
        con.execute("CREATE TABLE threads (id TEXT, model_provider TEXT)")
        for provider, count in tags.items():
            con.executemany("INSERT INTO threads VALUES (?, ?)", [(f"{provider}-{i}", provider) for i in range(count)])
        con.commit()
        con.close()
    return home


def run_report(home, expect_tail=None, probe=False):
    class Args:
        pass

    args = Args()
    args.home, args.codex, args.expect_tail, args.probe, args.doctor = str(home), None, expect_tail, probe, False
    original = fuel.find_codex
    fuel.find_codex = lambda explicit=None: []
    try:
        return fuel.build_report(args)
    finally:
        fuel.find_codex = original


def levels(report):
    return [(f["level"], f["message"]) for f in report["findings"]]


class AuthTests(unittest.TestCase):
    def test_fingerprint_keeps_only_tail(self):
        fp = fuel.fingerprint(KEY_A)
        self.assertEqual(fp["tail"], "AAAA1")
        self.assertNotIn("aaaa", json.dumps(fp))

    def test_comment_line_makes_auth_unusable(self):
        raw = ('{\n  // "OPENAI_API_KEY": "%s"\n  "OPENAI_API_KEY": "%s"\n}' % (KEY_A, KEY_B)).encode()
        info = fuel.inspect_auth(make_home(raw_auth=raw))
        self.assertFalse(info["valid"])
        self.assertTrue(any("comment" in p for p in info["problems"]))

    def test_bom_is_flagged_but_key_still_read(self):
        raw = b"\xef\xbb\xbf" + json.dumps({"OPENAI_API_KEY": KEY_A}).encode()
        info = fuel.inspect_auth(make_home(raw_auth=raw))
        self.assertTrue(info["valid"])
        self.assertEqual(info["key"]["tail"], "AAAA1")
        self.assertTrue(any("BOM" in p for p in info["problems"]))

    def test_expect_tail_mismatch_is_an_error_and_match_is_silent(self):
        home = make_home(RELAY_CONFIG, auth={"OPENAI_API_KEY": KEY_A})
        self.assertTrue(any(l == "error" and "expected BBBB2" in m for l, m in levels(run_report(home, "BBBB2"))))
        self.assertFalse(any("expected" in m for _, m in levels(run_report(home, "AAAA1"))))


class ConfigTests(unittest.TestCase):
    def test_overriding_builtin_openai_is_an_error(self):
        config = 'model_provider = "relay"\n[model_providers.relay]\nbase_url = "https://r/v1"\n[model_providers.openai]\nbase_url = "https://r/v1"\n'
        report = run_report(make_home(config, auth={"OPENAI_API_KEY": KEY_A}))
        self.assertEqual(report["config"]["overrides_builtin"], ["openai"])
        self.assertTrue(any(l == "error" and "reserved built-in" in m for l, m in levels(report)))

    def test_undefined_active_provider(self):
        report = run_report(make_home('model_provider = "ghost"\n', auth={"OPENAI_API_KEY": KEY_A}))
        self.assertEqual(report["config"]["active_kind"], "undefined")
        self.assertTrue(any(l == "error" and "ghost" in m for l, m in levels(report)))

    def test_builtin_openai_with_openai_base_url(self):
        config = 'model_provider = "openai"\nopenai_base_url = "https://relay.example/v1"\n'
        report = run_report(make_home(config, auth={"OPENAI_API_KEY": KEY_A}, tags={"openai": 5}))
        self.assertEqual(report["config"]["endpoint"], "https://relay.example/v1")
        self.assertEqual([f for f in report["findings"] if f["level"] in ("error", "warn")], [])

    def test_keyring_store_warns(self):
        config = 'cli_auth_credentials_store = "keyring"\n' + RELAY_CONFIG
        report = run_report(make_home(config, auth={"OPENAI_API_KEY": KEY_A}))
        self.assertTrue(any(l == "warn" and "keyring" in m for l, m in levels(report)))

    def test_mini_toml_reads_what_the_check_needs(self):
        text = RELAY_CONFIG + "[projects.'c:\\users\\dell']\ntrust_level = \"trusted\"\n"
        data = fuel.mini_toml(text)
        self.assertEqual(data["model_provider"], "relay")
        self.assertEqual(data["model_providers"]["relay"]["base_url"], "https://relay.example/v1")
        self.assertIs(data["model_providers"]["relay"]["requires_openai_auth"], True)
        self.assertEqual(data["projects"]["c:\\users\\dell"]["trust_level"], "trusted")


class HistoryTagTests(unittest.TestCase):
    def test_orphan_tag_gets_alias_command_and_openai_tag_gets_route_hint(self):
        home = make_home(RELAY_CONFIG, auth={"OPENAI_API_KEY": KEY_A}, tags={"codex": 3, "relay": 2, "openai": 4})
        report = run_report(home)
        self.assertEqual(report["tags"]["counts"], {"codex": 3, "relay": 2, "openai": 4})
        errors = [f for f in report["findings"] if f["level"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("'codex'", errors[0]["message"])
        self.assertIn("--legacy-provider codex --active-provider relay", errors[0]["fix"])
        warns = [f for f in report["findings"] if f["level"] == "warn"]
        self.assertEqual(len(warns), 1)
        self.assertIn('openai_base_url = "https://relay.example/v1"', warns[0]["fix"])

    def test_newest_state_database_wins(self):
        home = make_home(RELAY_CONFIG, auth={"OPENAI_API_KEY": KEY_A}, tags={"relay": 1})
        con = sqlite3.connect(home / "state_9.sqlite")
        con.execute("CREATE TABLE threads (id TEXT, model_provider TEXT)")
        con.execute("INSERT INTO threads VALUES ('x', 'newer')")
        con.commit()
        con.close()
        self.assertEqual(fuel.sqlite_tags(home), {"newer": 1})

    def test_newest_binary_by_version(self):
        picked = fuel.newest([{"path": "a", "version": "codex-cli 0.146.0"}, {"path": "b", "version": "codex-cli 0.155.0-alpha.2.6"}])
        self.assertEqual(picked["path"], "b")


class ProbeTests(unittest.TestCase):
    def setUp(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                good = self.headers.get("Authorization") == "Bearer good" and self.path == "/v1/models"
                body = json.dumps({"data": [{"id": "m1"}, {"id": "m2"}]} if good else {"error": "no"}).encode()
                self.send_response(200 if good else 401)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_probe_accepts_and_rejects(self):
        self.assertEqual(fuel.probe_models(self.base, "good")["models"], ["m1", "m2"])
        self.assertEqual(fuel.probe_models(self.base, "bad")["status"], 401)

    def test_probe_rejected_key_is_an_error_finding(self):
        config = RELAY_CONFIG.replace("https://relay.example/v1", self.base)
        report = run_report(make_home(config, auth={"OPENAI_API_KEY": KEY_A}), probe=True)
        self.assertTrue(any(l == "error" and "HTTP 401" in m for l, m in levels(report)))


class SetKeyTests(unittest.TestCase):
    def test_clean_key_strips_bom_utf16_and_whitespace(self):
        self.assertEqual(fuel.clean_key(b"\xef\xbb\xbf" + KEY_A.encode() + b"\r\n"), KEY_A)
        self.assertEqual(fuel.clean_key(KEY_A.encode("utf-16")), KEY_A)
        self.assertEqual(fuel.clean_key(("\ufeff" + KEY_A + "\n").encode()), KEY_A)

    def test_clean_key_rejects_garbage(self):
        for bad in (b"", b"short", b"has space inside 0123456789", "cl\u00e9".encode() + b"0" * 20):
            with self.assertRaises(ValueError):
                fuel.clean_key(bad)

    def test_key_with_hidden_bom_is_an_error(self):
        report = run_report(make_home(RELAY_CONFIG, auth={"OPENAI_API_KEY": "\ufeff" + KEY_A}))
        self.assertIs(report["auth"]["key_clean"], False)
        self.assertTrue(any(l == "error" and "invisible" in m for l, m in levels(report)))

    def run_set_key(self, home, extra_args):
        originals = (fuel.find_codex, fuel.probe_models, fuel.sys.stdin)
        fuel.find_codex = lambda explicit=None: []
        fuel.probe_models = lambda base, key, timeout=20: {"url": base, "status": 200, "models": ["m"]}
        fuel.sys.stdin = io.TextIOWrapper(io.BytesIO(b"\xef\xbb\xbf" + KEY_B.encode() + b"\r\n"))
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                code = fuel.main(["set-key", "--home", str(home), *extra_args])
        finally:
            fuel.find_codex, fuel.probe_models, fuel.sys.stdin = originals
        return code, buffer

    def test_set_key_reads_stdin_automatically_when_it_is_not_a_terminal(self):
        home = make_home(RELAY_CONFIG, auth={"OPENAI_API_KEY": KEY_A})
        code, _ = self.run_set_key(home, [])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((home / "auth.json").read_text(encoding="utf-8"))["OPENAI_API_KEY"], KEY_B)

    def test_set_key_backs_up_writes_clean_key_and_verifies(self):
        home = make_home(RELAY_CONFIG, auth={"OPENAI_API_KEY": KEY_A})
        code, buffer = self.run_set_key(home, ["--key-stdin"])
        self.assertEqual(code, 0)
        stored = json.loads((home / "auth.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["OPENAI_API_KEY"], KEY_B)
        backups = list(home.glob("auth.json.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertIn(KEY_A, backups[0].read_text(encoding="utf-8"))
        self.assertNotIn(KEY_B, buffer.getvalue())
        self.assertIn("BBBB2", buffer.getvalue())


class OutputTests(unittest.TestCase):
    def test_script_source_is_pure_ascii_so_it_survives_any_locale_when_piped(self):
        source = (ROOT / "scripts" / "codex_fuel.py").read_text(encoding="utf-8")
        self.assertEqual([n for n, line in enumerate(source.splitlines(), 1) if not line.isascii()], [])

    def test_runs_from_any_directory_and_piped_on_stdin_with_usable_fix_paths(self):
        home = make_home(RELAY_CONFIG, auth={"OPENAI_API_KEY": KEY_A}, tags={"codex": 1})
        script = ROOT / "scripts" / "codex_fuel.py"
        elsewhere = tempfile.mkdtemp()
        by_path = subprocess.run([sys.executable, str(script), "--home", str(home), "--json"],
                                 cwd=elsewhere, capture_output=True, text=True)
        piped = subprocess.run([sys.executable, "-", "--home", str(home), "--json"], input=script.read_text(encoding="utf-8"),
                               cwd=elsewhere, capture_output=True, text=True)
        for proc, expected in ((by_path, str(ROOT / "scripts" / "codex_account_recovery.py")), (piped, "scripts/codex_account_recovery.py")):
            self.assertEqual(proc.returncode, 1, proc.stderr)
            fixes = [f["fix"] for f in json.loads(proc.stdout)["findings"] if f["level"] == "error"]
            self.assertTrue(any(expected in fix for fix in fixes), fixes)

    def test_json_and_text_output_never_contain_the_full_key(self):
        home = make_home(RELAY_CONFIG, auth={"OPENAI_API_KEY": KEY_A}, tags={"relay": 1})
        original = fuel.find_codex
        fuel.find_codex = lambda explicit=None: []
        try:
            for extra in ([], ["--json"]):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = fuel.main(["--home", str(home), *extra])
                self.assertEqual(code, 0)
                self.assertNotIn(KEY_A, buffer.getvalue())
                self.assertNotIn("aaaaaaaa", buffer.getvalue())
                self.assertIn("AAAA1", buffer.getvalue())
        finally:
            fuel.find_codex = original


if __name__ == "__main__":
    unittest.main()
