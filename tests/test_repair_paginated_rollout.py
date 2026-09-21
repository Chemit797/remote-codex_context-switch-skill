from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "repair_paginated_rollout.py"
TASK_ID = "01a06681-e47d-76e1-9896-8cb5218cac80"


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


def write_rollout(path: Path, ordinals: list[int], history_mode: str = "paginated") -> None:
    records = [
        {
            "timestamp": "2026-09-03T09:03:52.093Z",
            "ordinal": ordinals[0],
            "type": "session_meta",
            "payload": {"id": TASK_ID, "history_mode": history_mode},
        }
    ]
    records.extend(
        {
            "timestamp": f"2026-09-05T12:00:0{index}.000Z",
            "ordinal": ordinal,
            "type": "event_msg",
            "payload": {"type": "fixture", "private_text": f"unchanged-{index}"},
        }
        for index, ordinal in enumerate(ordinals[1:], start=1)
    )
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


class PaginatedRolloutRepairTests(unittest.TestCase):
    def test_healthy_rollout_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rollout = Path(temporary) / "rollout.jsonl"
            write_rollout(rollout, [0, 1, 2, 3])
            result = json.loads(run_tool("audit", "--rollout", str(rollout), "--json").stdout)
            self.assertTrue(result["healthy"])
            self.assertFalse(result["repairable"])
            self.assertEqual(result["mismatch_count"], 0)

    def test_duplicate_segment_is_repaired_without_changing_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rollout = Path(temporary) / "rollout.jsonl"
            write_rollout(rollout, [0, 1, 1, 2])
            before_records = [json.loads(line) for line in rollout.read_text(encoding="utf-8").splitlines()]

            dry_run = json.loads(
                run_tool("repair", "--rollout", str(rollout), "--json").stdout
            )
            self.assertTrue(dry_run["planned"])
            self.assertFalse(dry_run["applied"])

            applied = json.loads(
                run_tool(
                    "repair",
                    "--rollout",
                    str(rollout),
                    "--i-confirm-codex-is-closed",
                    "--apply",
                    "--json",
                ).stdout
            )
            self.assertTrue(applied["applied"])
            self.assertTrue(applied["after"]["healthy"])
            backup = Path(applied["backup"])
            self.assertTrue(backup.is_file())

            after_records = [json.loads(line) for line in rollout.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([record["ordinal"] for record in after_records], [0, 1, 2, 3])
            for before, after in zip(before_records, after_records, strict=True):
                before.pop("ordinal")
                after.pop("ordinal")
                self.assertEqual(before, after)

    def test_apply_requires_closed_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rollout = Path(temporary) / "rollout.jsonl"
            write_rollout(rollout, [0, 1, 1])
            run_tool("repair", "--rollout", str(rollout), "--apply", expected=2)
            self.assertEqual(
                [json.loads(line)["ordinal"] for line in rollout.read_text(encoding="utf-8").splitlines()],
                [0, 1, 1],
            )

    def test_forward_gap_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rollout = Path(temporary) / "rollout.jsonl"
            write_rollout(rollout, [0, 1, 3])
            audit = json.loads(run_tool("audit", "--rollout", str(rollout), "--json").stdout)
            self.assertEqual(audit["reason"], "forward_gap_may_mean_missing_records")
            self.assertFalse(audit["repairable"])
            run_tool(
                "repair",
                "--rollout",
                str(rollout),
                "--i-confirm-codex-is-closed",
                "--apply",
                expected=2,
            )

    def test_non_paginated_rollout_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rollout = Path(temporary) / "rollout.jsonl"
            write_rollout(rollout, [0, 1, 2], history_mode="legacy")
            audit = json.loads(run_tool("audit", "--rollout", str(rollout), "--json").stdout)
            self.assertEqual(audit["reason"], "not_paginated")
            self.assertFalse(audit["repairable"])


if __name__ == "__main__":
    unittest.main()
