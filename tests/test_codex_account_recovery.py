from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "codex_account_recovery.py"
TASK_ID = "01a012ec-bf20-7851-acaa-ea4e8e345088"
SECOND_TASK_ID = "12b123fd-c031-8962-bdbb-fb5f9f456199"


def load_recovery_module() -> object:
    spec = importlib.util.spec_from_file_location("codex_account_recovery_test_module", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load recovery helper for platform-independent unit checks.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RECOVERY_MODULE = load_recovery_module()


def run_tool(*arguments: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != expected:
        raise AssertionError(
            f"expected exit {expected}, got {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def write_fixture_source(root: Path) -> tuple[Path, Path]:
    attachment = root / "attachments" / "fixture" / "note.txt"
    attachment.parent.mkdir(parents=True)
    attachment.write_text("attachment fixture\n", encoding="utf-8")
    session = root / "sessions" / "2026" / "08" / "18" / f"rollout-2026-08-18T11-31-40-{TASK_ID}.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": TASK_ID, "model_provider": "codex"}}),
                json.dumps({"type": "event_msg", "payload": {"local_images": [str(attachment)]}}),
                json.dumps({"type": "event_msg", "payload": {"message": str(attachment)}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "session_index.jsonl").write_text(
        json.dumps({"id": TASK_ID, "thread_name": "fixture only"}) + "\n", encoding="utf-8"
    )
    # These must never appear in a portable conversation bundle.
    (root / "auth.json").write_text('{"sensitive":true}\n', encoding="utf-8")
    (root / "config.toml").write_text('model_provider = "codex"\n', encoding="utf-8")
    return session, attachment


def write_second_fixture_session(root: Path) -> tuple[Path, Path]:
    attachment = root / "attachments" / "fixture" / "second-note.txt"
    attachment.parent.mkdir(parents=True, exist_ok=True)
    attachment.write_text("second attachment fixture\n", encoding="utf-8")
    session = root / "sessions" / "2026" / "08" / "19" / f"rollout-2026-08-19T11-31-40-{SECOND_TASK_ID}.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text(
        "\n".join(
            [
                json.dumps(
                    {"type": "session_meta", "payload": {"id": SECOND_TASK_ID, "model_provider": "codex"}}
                ),
                json.dumps({"type": "event_msg", "payload": {"local_images": [str(attachment)]}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with (root / "session_index.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"id": SECOND_TASK_ID, "thread_name": "second fixture only"}) + "\n")
    return session, attachment


def write_target_config(root: Path) -> Path:
    config = root / "config.toml"
    config.write_text(
        "\n".join(
            [
                'model_provider = "chatgpt_http"',
                '',
                '[model_providers.chatgpt_http]',
                'name = "ChatGPT HTTPS"',
                'base_url = "https://chatgpt.com/backend-api/codex"',
                'requires_openai_auth = true',
                'supports_websockets = false',
                'stream_max_retries = 2',
                'stream_idle_timeout_ms = 300000',
            ]
        ),
        encoding="utf-8",
    )
    return config


def add_sensitive_provider_settings(config: Path) -> None:
    config.write_text(
        config.read_text(encoding="utf-8")
        + "\n"
        + 'env_key = "DO_NOT_COPY"\n'
        + 'http_headers = { "X-Do-Not-Copy" = "fixture" }\n\n'
        + "[model_providers.chatgpt_http.auth]\n"
        + 'command = "do-not-copy-this"\n',
        encoding="utf-8",
    )


class CodexAccountRecoveryTests(unittest.TestCase):
    def test_manifest_paths_are_posix_on_a_windows_producer(self) -> None:
        root = PureWindowsPath(r"C:\Users\fixture\.codex")
        session = root / "sessions" / "2026" / "08" / "18" / "rollout-fixture.jsonl"
        attachment = root / "attachments" / "fixture" / "note.txt"
        self.assertEqual(
            RECOVERY_MODULE.canonical_bundle_relative(session, root),
            "sessions/2026/08/18/rollout-fixture.jsonl",
        )
        self.assertEqual(
            RECOVERY_MODULE.canonical_bundle_relative(attachment, root / "attachments"),
            "fixture/note.txt",
        )

    def test_bundle_alias_verify_and_clean_target_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source-codex"
            target = workspace / "target-codex"
            bundle = workspace / "bundle"
            source.mkdir()
            target.mkdir()
            source_session, source_attachment = write_fixture_source(source)
            target_config = write_target_config(target)

            preflight = run_tool("inventory", "--source", str(source), "--target", str(target), "--json")
            preflight_report = json.loads(preflight.stdout)
            self.assertEqual(preflight_report["missing_target_provider_ids"], ["codex"])

            run_tool(
                "provider-alias",
                "--config",
                str(target_config),
                "--legacy-provider",
                "codex",
                "--active-provider",
                "chatgpt_http",
                "--apply",
                "--json",
            )
            config_text = target_config.read_text(encoding="utf-8")
            self.assertIn('[model_providers.codex]', config_text)
            self.assertIn('model_provider = "chatgpt_http"', config_text)
            alias_text = config_text.split('[model_providers.codex]', 1)[1]
            self.assertNotIn("DO_NOT_COPY", alias_text)
            self.assertNotIn("X-Do-Not-Copy", alias_text)
            self.assertNotIn("do-not-copy-this", alias_text)
            self.assertTrue(list(target.glob("config.toml.pre-codex-account-recovery-*.bak")))

            repaired = run_tool("inventory", "--source", str(source), "--target", str(target), "--json")
            self.assertEqual(json.loads(repaired.stdout)["missing_target_provider_ids"], [])

            run_tool(
                "bundle",
                "--source",
                str(source),
                "--output",
                str(bundle),
                "--thread",
                TASK_ID,
                "--include-attachments",
            )
            self.assertFalse(bundle.exists())
            run_tool(
                "bundle",
                "--source",
                str(source),
                "--output",
                str(bundle),
                "--thread",
                TASK_ID,
                "--include-attachments",
                "--apply",
                "--json",
            )
            self.assertTrue((bundle / "manifest.json").is_file())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(bundle.stat().st_mode), 0o700)
            self.assertFalse((bundle / "auth.json").exists())
            self.assertFalse((bundle / "config.toml").exists())
            run_tool("verify", "--bundle", str(bundle), "--json")

            run_tool(
                "restore",
                "--bundle",
                str(bundle),
                "--target",
                str(target),
                "--thread",
                TASK_ID,
                "--include-attachments",
            )
            restored = run_tool(
                "restore",
                "--bundle",
                str(bundle),
                "--target",
                str(target),
                "--thread",
                TASK_ID,
                "--include-attachments",
                "--i-confirm-codex-is-closed",
                "--apply",
                "--json",
            )
            target_session = target / source_session.relative_to(source)
            target_attachment = target / source_attachment.relative_to(source)
            source_records = [json.loads(line) for line in source_session.read_text(encoding="utf-8").splitlines()]
            target_records = [json.loads(line) for line in target_session.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(target_records[0], source_records[0])
            self.assertEqual(target_records[1]["payload"]["local_images"], [str(target_attachment)])
            self.assertEqual(source_records[1]["payload"]["local_images"], [str(source_attachment)])
            self.assertEqual(target_records[2]["payload"]["message"], str(source_attachment))
            self.assertEqual(target_attachment.read_bytes(), source_attachment.read_bytes())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((target / "sessions").stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(target_session.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(target_attachment.stat().st_mode), 0o600)
            self.assertEqual(json.loads(restored.stdout)["rewritten_attachment_references"], 1)
            self.assertIn(TASK_ID, (target / "session_index.jsonl").read_text(encoding="utf-8"))
            self.assertTrue(list(target.glob("session_index.jsonl.pre-codex-account-recovery-*.bak")) == [])

            collision = run_tool(
                "restore",
                "--bundle",
                str(bundle),
                "--target",
                str(target),
                "--thread",
                TASK_ID,
                expected=2,
            )
            self.assertIn("empty, clean target CODEX_HOME", collision.stderr)

    def test_alias_refuses_to_overwrite_existing_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = write_target_config(Path(temporary))
            config.write_text(
                config.read_text(encoding="utf-8")
                + "\n[model_providers.codex]\nbase_url = \"https://example.invalid\"\n",
                encoding="utf-8",
            )
            result = run_tool(
                "provider-alias",
                "--config",
                str(config),
                "--legacy-provider",
                "codex",
                "--active-provider",
                "chatgpt_http",
                "--apply",
                expected=2,
            )
            self.assertIn("already exists", result.stderr)

    def test_alias_can_target_builtin_openai_without_copying_an_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.toml"
            original = 'model_provider = "openai"\nmodel = "gpt-5.6-sol"\n'
            config.write_text(original, encoding="utf-8")

            dry_run = run_tool(
                "provider-alias",
                "--config",
                str(config),
                "--legacy-provider",
                "codex",
                "--active-provider",
                "openai",
                "--json",
            )
            dry_run_report = json.loads(dry_run.stdout.split("\nDry run only.", 1)[0])
            self.assertEqual(dry_run_report["active_provider_kind"], "built-in")
            self.assertFalse(dry_run_report["will_write"])
            self.assertEqual(config.read_text(encoding="utf-8"), original)

            applied = run_tool(
                "provider-alias",
                "--config",
                str(config),
                "--legacy-provider",
                "codex",
                "--active-provider",
                "openai",
                "--apply",
                "--json",
            )
            report = json.loads(applied.stdout)
            parsed = RECOVERY_MODULE.load_toml(config)
            self.assertEqual(parsed["model_provider"], "openai")
            self.assertEqual(
                parsed["model_providers"]["codex"],
                {
                    "name": "OpenAI (legacy codex tasks)",
                    "requires_openai_auth": True,
                    "wire_api": "responses",
                },
            )
            self.assertNotIn("base_url", parsed["model_providers"]["codex"])
            backup = Path(report["backup"])
            self.assertTrue(backup.is_file())
            self.assertEqual(backup.read_text(encoding="utf-8"), original)

    def test_alias_refuses_non_openai_builtin_targets(self) -> None:
        for active in ("ollama", "lmstudio"):
            with self.subTest(active=active), tempfile.TemporaryDirectory() as temporary:
                config = Path(temporary) / "config.toml"
                config.write_text(f'model_provider = "{active}"\n', encoding="utf-8")
                result = run_tool(
                    "provider-alias",
                    "--config",
                    str(config),
                    "--legacy-provider",
                    "codex",
                    "--active-provider",
                    active,
                    "--apply",
                    expected=2,
                )
                self.assertIn("does not use the target OpenAI login", result.stderr)
                self.assertNotIn("[model_providers.codex]", config.read_text(encoding="utf-8"))

    def test_alias_refuses_provider_url_with_query_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = write_target_config(Path(temporary))
            config.write_text(
                config.read_text(encoding="utf-8").replace(
                    "https://chatgpt.com/backend-api/codex", "https://example.invalid/responses?token=fixture"
                ),
                encoding="utf-8",
            )
            result = run_tool(
                "provider-alias",
                "--config",
                str(config),
                "--legacy-provider",
                "codex",
                "--active-provider",
                "chatgpt_http",
                "--apply",
                expected=2,
            )
            self.assertIn("unsupported credentials, query data", result.stderr)

    def test_alias_refuses_to_copy_a_provider_with_secret_transport_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = write_target_config(Path(temporary))
            add_sensitive_provider_settings(config)
            result = run_tool(
                "provider-alias",
                "--config",
                str(config),
                "--legacy-provider",
                "codex",
                "--active-provider",
                "chatgpt_http",
                "--apply",
                expected=2,
            )
            self.assertIn("credential or header fields", result.stderr)
            self.assertNotIn('[model_providers.codex]', config.read_text(encoding="utf-8"))

    def test_inventory_reports_target_occupancy_without_conversation_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source-codex"
            target = workspace / "target-codex"
            source.mkdir()
            target.mkdir()
            write_fixture_source(source)
            write_fixture_source(target)
            write_target_config(target)

            report = json.loads(
                run_tool("inventory", "--source", str(source), "--target", str(target), "--json").stdout
            )
            self.assertEqual(report["target_session_file_count"], 1)
            self.assertEqual(report["target_session_index_records"], 1)
            self.assertEqual(report["target_recovery_data_entries"], 2)
            self.assertNotIn("fixture only", json.dumps(report))

    def test_bundle_refuses_missing_referenced_attachment_and_extra_bundle_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source-codex"
            bundle = workspace / "bundle"
            source.mkdir()
            _, attachment = write_fixture_source(source)
            attachment.unlink()

            missing = run_tool(
                "bundle",
                "--source",
                str(source),
                "--output",
                str(bundle),
                "--thread",
                TASK_ID,
                "--include-attachments",
                "--apply",
                expected=2,
            )
            self.assertIn("recognizable attachment reference", missing.stderr)
            self.assertFalse(bundle.exists())

            # A session-only bundle is still valid, but a receiver must reject
            # unexpected payload files rather than silently carrying them over.
            run_tool(
                "bundle",
                "--source",
                str(source),
                "--output",
                str(bundle),
                "--thread",
                TASK_ID,
                "--apply",
            )
            (bundle / "unexpected.txt").write_text("not part of the manifest\n", encoding="utf-8")
            extra = run_tool("verify", "--bundle", str(bundle), expected=2)
            self.assertIn("unexpected file", extra.stderr)

    def test_attachment_bundle_refuses_a_missing_attachment_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source-codex"
            source.mkdir()
            _, attachment = write_fixture_source(source)
            attachment.unlink()
            attachment.parent.rmdir()
            (source / "attachments").rmdir()
            rejected = run_tool(
                "bundle",
                "--source",
                str(source),
                "--output",
                str(workspace / "bundle"),
                "--thread",
                TASK_ID,
                "--include-attachments",
                "--apply",
                expected=2,
            )
            self.assertIn("recognizable attachment reference", rejected.stderr)

    def test_alias_requires_target_openai_login_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = write_target_config(Path(temporary))
            config.write_text(
                config.read_text(encoding="utf-8").replace("requires_openai_auth = true", "requires_openai_auth = false"),
                encoding="utf-8",
            )
            result = run_tool(
                "provider-alias",
                "--config",
                str(config),
                "--legacy-provider",
                "codex",
                "--active-provider",
                "chatgpt_http",
                "--apply",
                expected=2,
            )
            self.assertIn("not explicitly configured to use the target OpenAI login", result.stderr)

    def test_bundle_refuses_a_git_worktree_output_location(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source-codex"
            repository = workspace / "repository"
            source.mkdir()
            (repository / ".git").mkdir(parents=True)
            write_fixture_source(source)
            result = run_tool(
                "bundle",
                "--source",
                str(source),
                "--output",
                str(repository / "recovery-bundle"),
                "--thread",
                TASK_ID,
                "--apply",
                expected=2,
            )
            self.assertIn("inside Git worktree", result.stderr)

    def test_bundle_omits_an_ambiguous_source_index_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source-codex"
            bundle = workspace / "bundle"
            source.mkdir()
            write_fixture_source(source)
            with (source / "session_index.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"id": TASK_ID, "thread_name": "ambiguous duplicate"}) + "\n")

            run_tool(
                "bundle",
                "--source",
                str(source),
                "--output",
                str(bundle),
                "--thread",
                TASK_ID,
                "--apply",
            )
            manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
            self.assertIsNone(manifest["session_index"]["relative_path"])
            self.assertEqual(manifest["session_index"]["record_count"], 0)
            self.assertEqual(manifest["session_index"]["omitted_duplicate_thread_ids"], [TASK_ID])
            self.assertFalse((bundle / "session_index.jsonl").exists())
            run_tool("verify", "--bundle", str(bundle))

    def test_archived_session_requires_explicit_selection_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source-codex"
            bundle = workspace / "bundle"
            source.mkdir()
            session, _ = write_fixture_source(source)
            archived = source / "archived_sessions" / session.relative_to(source / "sessions")
            archived.parent.mkdir(parents=True)
            session.replace(archived)

            absent = run_tool(
                "bundle",
                "--source",
                str(source),
                "--output",
                str(bundle),
                "--thread",
                TASK_ID,
                expected=2,
            )
            self.assertIn("were not found", absent.stderr)
            run_tool(
                "bundle",
                "--source",
                str(source),
                "--output",
                str(bundle),
                "--thread",
                TASK_ID,
                "--include-archived",
                "--apply",
            )
            manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["threads"][0]["relative_path"].startswith("archived_sessions/"))
            run_tool("verify", "--bundle", str(bundle))

    def test_inventory_refuses_symlinked_recovery_data_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source-codex"
            outside = workspace / "outside-attachments"
            source.mkdir()
            outside.mkdir()
            session = source / "sessions" / "2026" / f"rollout-{TASK_ID}.jsonl"
            session.parent.mkdir(parents=True)
            session.write_text(
                json.dumps({"type": "session_meta", "payload": {"id": TASK_ID, "model_provider": "codex"}})
                + "\n",
                encoding="utf-8",
            )
            try:
                os.symlink(outside, source / "attachments")
            except OSError as exc:
                if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                    self.skipTest("Windows symlink creation requires Developer Mode or elevated privileges")
                raise
            rejected = run_tool("inventory", "--source", str(source), expected=2)
            self.assertIn("symlinked attachments directory", rejected.stderr)

    def test_attachment_bundle_refuses_a_nested_attachment_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source-codex"
            source.mkdir()
            _, attachment = write_fixture_source(source)
            outside = workspace / "outside-attachment"
            outside.write_text("must not escape the attachment root\n", encoding="utf-8")
            attachment.unlink()
            try:
                os.symlink(outside, attachment)
            except OSError as exc:
                if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                    self.skipTest("Windows symlink creation requires Developer Mode or elevated privileges")
                raise
            rejected = run_tool(
                "bundle",
                "--source",
                str(source),
                "--output",
                str(workspace / "bundle"),
                "--thread",
                TASK_ID,
                "--include-attachments",
                "--apply",
                expected=2,
            )
            self.assertIn("symlinked attachments recovery-data entry", rejected.stderr)

    def test_bundle_refuses_hardlinked_session_or_attachment_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source-codex"
            source.mkdir()
            session, attachment = write_fixture_source(source)
            os.link(session, workspace / "second-link-to-session.jsonl")
            session_rejected = run_tool("inventory", "--source", str(source), expected=2)
            self.assertIn("source session file with 2 hard links", session_rejected.stderr)

            # A fresh fixture isolates the attachment guard from the session guard.
            source = workspace / "second-source-codex"
            source.mkdir()
            _, attachment = write_fixture_source(source)
            secret = workspace / "private-source-file"
            secret.write_text("do not package this\n", encoding="utf-8")
            attachment.unlink()
            os.link(secret, attachment)
            attachment_rejected = run_tool(
                "bundle",
                "--source",
                str(source),
                "--output",
                str(workspace / "bundle"),
                "--thread",
                TASK_ID,
                "--include-attachments",
                "--apply",
                expected=2,
            )
            self.assertIn("source attachment with 2 hard links", attachment_rejected.stderr)

    def test_verify_refuses_fabricated_attachment_locations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source-codex"
            bundle = workspace / "bundle"
            source.mkdir()
            write_fixture_source(source)
            run_tool(
                "bundle",
                "--source",
                str(source),
                "--output",
                str(bundle),
                "--thread",
                TASK_ID,
                "--include-attachments",
                "--apply",
            )
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["attachments"][0]["references_by_thread"][TASK_ID][0]["source_reference"] = "codex"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            rejected = run_tool("verify", "--bundle", str(bundle), expected=2)
            self.assertIn("does not match its declared selected task attachment path", rejected.stderr)

    def test_selective_restore_keeps_other_task_attachments_out(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source-codex"
            target = workspace / "target-codex"
            bundle = workspace / "bundle"
            source.mkdir()
            target.mkdir()
            first_session, first_attachment = write_fixture_source(source)
            second_session, second_attachment = write_second_fixture_session(source)
            target_config = write_target_config(target)
            run_tool(
                "provider-alias",
                "--config",
                str(target_config),
                "--legacy-provider",
                "codex",
                "--active-provider",
                "chatgpt_http",
                "--apply",
            )
            run_tool(
                "bundle",
                "--source",
                str(source),
                "--output",
                str(bundle),
                "--all-sessions",
                "--include-attachments",
                "--apply",
            )
            restored = run_tool(
                "restore",
                "--bundle",
                str(bundle),
                "--target",
                str(target),
                "--thread",
                TASK_ID,
                "--include-attachments",
                "--i-confirm-codex-is-closed",
                "--apply",
                "--json",
            )
            report = json.loads(restored.stdout)
            self.assertEqual(report["attachment_files"], 1)
            self.assertEqual(report["attachment_reference_paths_to_remap"], 1)
            self.assertTrue((target / first_session.relative_to(source)).is_file())
            self.assertTrue((target / first_attachment.relative_to(source)).is_file())
            self.assertFalse((target / second_session.relative_to(source)).exists())
            self.assertFalse((target / second_attachment.relative_to(source)).exists())
            self.assertIn(TASK_ID, (target / "session_index.jsonl").read_text(encoding="utf-8"))
            self.assertNotIn(SECOND_TASK_ID, (target / "session_index.jsonl").read_text(encoding="utf-8"))

    def test_attachment_remap_preserves_non_slot_json_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source-codex"
            target = workspace / "target-codex"
            bundle = workspace / "bundle"
            source.mkdir()
            target.mkdir()
            source_session, source_attachment = write_fixture_source(source)
            encoded_source_attachment = json.dumps(str(source_attachment), ensure_ascii=False)
            raw_session = (
                json.dumps({"type": "session_meta", "payload": {"id": TASK_ID, "model_provider": "codex"}})
                + "\r\n"
                + "{\"type\":\"event_msg\",\"payload\":{\"local_images\":["
                + encoded_source_attachment
                + "],\"message\":"
                + encoded_source_attachment
                + "},\"number\":1e999,\"duplicate\":\"first\",\"duplicate\":\"second\"}\r\n"
            )
            source_session.write_text(raw_session, encoding="utf-8")
            target_config = write_target_config(target)
            run_tool(
                "provider-alias",
                "--config",
                str(target_config),
                "--legacy-provider",
                "codex",
                "--active-provider",
                "chatgpt_http",
                "--apply",
            )
            run_tool(
                "bundle",
                "--source",
                str(source),
                "--output",
                str(bundle),
                "--thread",
                TASK_ID,
                "--include-attachments",
                "--apply",
            )
            run_tool(
                "restore",
                "--bundle",
                str(bundle),
                "--target",
                str(target),
                "--thread",
                TASK_ID,
                "--include-attachments",
                "--i-confirm-codex-is-closed",
                "--apply",
            )
            target_session = target / source_session.relative_to(source)
            target_text = target_session.read_bytes().decode("utf-8")
            encoded_target_attachment = json.dumps(
                str(target / source_attachment.relative_to(source)), ensure_ascii=False
            )
            self.assertIn("\r\n", target_text)
            self.assertIn('"number":1e999,"duplicate":"first","duplicate":"second"', target_text)
            self.assertEqual(target_text.count(encoded_source_attachment), 1)
            self.assertEqual(target_text.count(encoded_target_attachment), 1)

    def test_attachment_bundle_rejects_unsupported_registered_slot_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source-codex"
            source.mkdir()
            session, _ = write_fixture_source(source)
            session.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {"type": "session_meta", "payload": {"id": TASK_ID, "model_provider": "codex"}}
                        ),
                        json.dumps({"type": "event_msg", "payload": {"local_images": "not-a-list"}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            rejected = run_tool(
                "bundle",
                "--source",
                str(source),
                "--output",
                str(workspace / "bundle"),
                "--thread",
                TASK_ID,
                "--include-attachments",
                "--apply",
                expected=2,
            )
            self.assertIn("local_images is not an unambiguous array", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
