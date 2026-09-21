#!/usr/bin/env python3
"""Audit and narrowly repair ordinal continuity in a paginated Codex rollout.

The repair changes only the top-level ``ordinal`` integer on affected JSONL
records. It refuses forward gaps because those can indicate missing records.
An in-place repair requires an explicit apply flag and confirmation that Codex
Desktop and every App Server using the rollout are closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ORDINAL_RE = re.compile(rb'("ordinal"\s*:\s*)(-?\d+)')


class RolloutRepairError(RuntimeError):
    """Expected validation or safety failure."""


@dataclass
class OrdinalIssue:
    line: int
    expected: int
    actual: int
    kind: str


@dataclass
class Audit:
    path: str
    sha256: str
    line_count: int
    session_id: str | None
    history_mode: str | None
    first_ordinal: int | None
    last_ordinal: int | None
    first_issue: OrdinalIssue | None
    mismatch_count: int
    forward_gap_count: int
    valid_jsonl: bool
    repairable: bool
    healthy: bool
    reason: str

    def json_value(self) -> dict[str, Any]:
        value = asdict(self)
        return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_plain_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RolloutRepairError(f"Cannot stat rollout: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RolloutRepairError(f"Rollout must be a regular, non-symlink file: {path}")
    if metadata.st_nlink != 1:
        raise RolloutRepairError(
            f"Refusing rollout with {metadata.st_nlink} hard links: {path}"
        )


def audit_rollout(path: Path) -> Audit:
    require_plain_file(path)
    first_record: dict[str, Any] | None = None
    first_ordinal: int | None = None
    last_ordinal: int | None = None
    first_issue: OrdinalIssue | None = None
    mismatch_count = 0
    forward_gap_count = 0
    line_count = 0

    try:
        with path.open("rb") as handle:
            for line_count, raw_line in enumerate(handle, start=1):
                if not raw_line.strip():
                    raise RolloutRepairError(f"Blank JSONL record at line {line_count}.")
                try:
                    record = json.loads(raw_line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RolloutRepairError(
                        f"Invalid UTF-8 JSON record at line {line_count}."
                    ) from exc
                if not isinstance(record, dict):
                    raise RolloutRepairError(f"Non-object JSONL record at line {line_count}.")
                if first_record is None:
                    first_record = record
                ordinal = record.get("ordinal")
                if isinstance(ordinal, bool) or not isinstance(ordinal, int):
                    raise RolloutRepairError(
                        f"Missing or non-integer top-level ordinal at line {line_count}."
                    )
                expected = line_count - 1
                if first_ordinal is None:
                    first_ordinal = ordinal
                last_ordinal = ordinal
                if ordinal != expected:
                    mismatch_count += 1
                    kind = "forward_gap" if ordinal > expected else "duplicate_or_rewind"
                    if kind == "forward_gap":
                        forward_gap_count += 1
                    if first_issue is None:
                        first_issue = OrdinalIssue(line_count, expected, ordinal, kind)
    except OSError as exc:
        raise RolloutRepairError(f"Cannot read rollout: {path}") from exc

    if first_record is None:
        raise RolloutRepairError("Rollout is empty.")
    payload = first_record.get("payload")
    if first_record.get("type") != "session_meta" or not isinstance(payload, dict):
        raise RolloutRepairError("First JSONL record is not a valid session_meta record.")
    history_mode = payload.get("history_mode")
    session_id = payload.get("id") or payload.get("session_id")
    if not isinstance(session_id, str):
        session_id = None

    paginated = history_mode == "paginated"
    starts_at_zero = first_ordinal == 0
    healthy = paginated and starts_at_zero and mismatch_count == 0
    repairable = (
        paginated
        and starts_at_zero
        and mismatch_count > 0
        and forward_gap_count == 0
    )
    if not paginated:
        reason = "not_paginated"
    elif not starts_at_zero:
        reason = "first_ordinal_is_not_zero"
    elif forward_gap_count:
        reason = "forward_gap_may_mean_missing_records"
    elif mismatch_count:
        reason = "duplicate_or_rewound_ordinals"
    else:
        reason = "healthy"

    return Audit(
        path=str(path),
        sha256=sha256_file(path),
        line_count=line_count,
        session_id=session_id,
        history_mode=history_mode if isinstance(history_mode, str) else None,
        first_ordinal=first_ordinal,
        last_ordinal=last_ordinal,
        first_issue=first_issue,
        mismatch_count=mismatch_count,
        forward_gap_count=forward_gap_count,
        valid_jsonl=True,
        repairable=repairable,
        healthy=healthy,
        reason=reason,
    )


def rewrite_ordinals(source: Path, destination: Path) -> None:
    with source.open("rb") as reader, destination.open("wb") as writer:
        for line_number, raw_line in enumerate(reader, start=1):
            matches = list(ORDINAL_RE.finditer(raw_line))
            if not matches:
                raise RolloutRepairError(
                    f"Cannot locate serialized top-level ordinal at line {line_number}."
                )
            match = matches[0]
            expected = str(line_number - 1).encode("ascii")
            writer.write(raw_line[: match.start(2)] + expected + raw_line[match.end(2) :])
        writer.flush()
        os.fsync(writer.fileno())


def backup_path_for(path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.name}.before-ordinal-repair-{stamp}.bak")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(
            f"{path.name}.before-ordinal-repair-{stamp}-{counter}.bak"
        )
        counter += 1
    return candidate


def repair_rollout(path: Path, apply: bool, codex_closed: bool) -> dict[str, Any]:
    before = audit_rollout(path)
    report: dict[str, Any] = {
        "before": before.json_value(),
        "planned": before.repairable,
        "applied": False,
        "backup": None,
        "after": None,
    }
    if before.healthy:
        return report
    if not before.repairable:
        raise RolloutRepairError(
            f"Ordinal repair refused: {before.reason}. Preserve the rollout and investigate missing records."
        )
    if not apply:
        return report
    if not codex_closed:
        raise RolloutRepairError(
            "--i-confirm-codex-is-closed is required because an App Server may hold the rollout open."
        )

    backup = backup_path_for(path)
    temporary_name: str | None = None
    try:
        shutil.copy2(path, backup)
        with tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.", suffix=".ordinal-repair.tmp", dir=path.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
        temporary_path = Path(temporary_name)
        rewrite_ordinals(path, temporary_path)
        os.replace(temporary_path, path)
        temporary_name = None
        after = audit_rollout(path)
        if not after.healthy:
            raise RolloutRepairError("Post-repair ordinal validation did not pass.")
    except Exception:
        if backup.exists():
            rollback_fd, rollback_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".rollback.tmp", dir=path.parent
            )
            os.close(rollback_fd)
            rollback_path = Path(rollback_name)
            try:
                shutil.copy2(backup, rollback_path)
                os.replace(rollback_path, path)
            finally:
                if rollback_path.exists():
                    rollback_path.unlink()
        raise
    finally:
        if temporary_name is not None:
            temporary_path = Path(temporary_name)
            if temporary_path.exists():
                temporary_path.unlink()

    report.update(
        {
            "applied": True,
            "backup": str(backup),
            "after": after.json_value(),
        }
    )
    return report


def print_report(value: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit", help="Read-only ordinal continuity audit")
    audit.add_argument("--rollout", required=True, help="Paginated rollout JSONL file")
    audit.add_argument("--json", action="store_true", help="Emit JSON")

    repair = subparsers.add_parser(
        "repair", help="Atomically renumber duplicate/rewound ordinals after backup"
    )
    repair.add_argument("--rollout", required=True, help="Paginated rollout JSONL file")
    repair.add_argument("--apply", action="store_true", help="Apply the repair")
    repair.add_argument(
        "--i-confirm-codex-is-closed",
        action="store_true",
        help="Required with --apply",
    )
    repair.add_argument("--json", action="store_true", help="Emit JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        path = Path(args.rollout).expanduser().resolve()
        if args.command == "audit":
            print_report(audit_rollout(path).json_value(), args.json)
        else:
            print_report(
                repair_rollout(path, args.apply, args.i_confirm_codex_is_closed),
                args.json,
            )
        return 0
    except RolloutRepairError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: filesystem operation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
