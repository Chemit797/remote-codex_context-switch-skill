#!/usr/bin/env python3
"""Safely package and restore selected local Codex conversation artifacts.

The tool intentionally works on JSONL session files and the session index only.
It never imports authentication, source configuration, or Codex SQLite databases.
All mutation-oriented commands require --apply; restore additionally requires an
explicit acknowledgement that Codex is closed on the target.
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
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable
from urllib.parse import urlparse

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ is documented.
    tomllib = None


TOOL_VERSION = "1.3.0"
BUNDLE_FORMAT = "codex-account-recovery-bundle"
BUNDLE_VERSION = 2
UUID_RE = re.compile(
    r"(?i)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BUILTIN_PROVIDERS = {"openai", "ollama", "lmstudio"}
PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
ATTACHMENT_POINTER_RE = re.compile(r"^/payload/local_images/(0|[1-9][0-9]*)$")
SESSION_CATEGORIES = ("sessions", "archived_sessions")
SENSITIVE_PROVIDER_FIELDS = (
    "env_key",
    "experimental_bearer_token",
    "query_params",
    "http_headers",
    "env_http_headers",
    "auth",
)
ALIAS_FIELDS = (
    "base_url",
    "requires_openai_auth",
    "wire_api",
    "request_max_retries",
    "stream_max_retries",
    "stream_idle_timeout_ms",
    "supports_websockets",
    "supports_standalone_web_search",
)
BUILTIN_OPENAI_ALIAS = {
    "name": "OpenAI",
    "requires_openai_auth": True,
    "wire_api": "responses",
}


class RecoveryError(RuntimeError):
    """An expected safety or input-validation failure."""


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def timestamp_label() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_regular_unlinked_file(path: Path, label: str) -> None:
    """Refuse symlink-adjacent and hard-link payload tricks before copying data."""
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RecoveryError(f"Cannot stat {label}: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise RecoveryError(f"{label} may not be a symlink: {path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise RecoveryError(f"{label} is not a regular file: {path}")
    if metadata.st_nlink != 1:
        raise RecoveryError(f"Refusing {label} with {metadata.st_nlink} hard links: {path}")


def relative_to(path: Path, root: Path) -> Path:
    try:
        return path.resolve().relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise RecoveryError(f"Path escapes expected root: {path}") from exc


def canonical_bundle_relative(path: Path | PureWindowsPath, root: Path | PureWindowsPath) -> str:
    """Return a manifest/layout path using `/` on every supported host platform."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise RecoveryError(f"Path escapes expected root: {path}") from exc


def safe_bundle_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise RecoveryError("Bundle paths must be nonempty relative strings.")
    raw = Path(relative)
    if raw.is_absolute() or "\\" in relative or any(part in {".", ".."} for part in raw.parts):
        raise RecoveryError(f"Unsafe bundle-relative path: {relative!r}")
    candidate = root.joinpath(raw)
    relative_to(candidate, root)
    return candidate


def ensure_private_directory(path: Path, label: str) -> None:
    """Create or tighten one recovery-data directory without following a symlink."""
    if path.is_symlink():
        raise RecoveryError(f"Refusing a symlinked {label} directory: {path}")
    if path.exists():
        if not path.is_dir():
            raise RecoveryError(f"Expected {label} directory, found a non-directory: {path}")
    else:
        try:
            path.mkdir(mode=0o700)
        except OSError as exc:
            raise RecoveryError(f"Cannot create private {label} directory: {path}") from exc
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        raise RecoveryError(f"Cannot secure {label} directory permissions: {path}") from exc


def ensure_private_destination_parent(root: Path, destination: Path, label: str) -> None:
    """Ensure every recovery-data directory below ``root`` is private and real."""
    try:
        relative = destination.parent.relative_to(root)
    except ValueError as exc:
        raise RecoveryError(f"Destination escapes its recovery-data root: {destination}") from exc
    ensure_private_directory(root, label)
    current = root
    for component in relative.parts:
        current = current / component
        ensure_private_directory(current, label)


def enclosing_git_worktree(path: Path) -> Path | None:
    """Return a containing Git worktree without shelling out to Git."""
    candidate = path.resolve()
    while True:
        if (candidate / ".git").exists():
            return candidate
        if candidate.parent == candidate:
            return None
        candidate = candidate.parent


def session_relative_path(relative: str) -> Path:
    """Validate a manifest path before it can target a session file."""
    path = Path(relative)
    safe_bundle_path(Path("/unused"), relative)
    if len(path.parts) < 2 or path.parts[0] not in SESSION_CATEGORIES or path.suffix != ".jsonl":
        raise RecoveryError(f"Invalid session path in bundle manifest: {relative!r}")
    return path


def attachment_relative_path(relative: str) -> Path:
    """Validate a manifest path before it can target an attachment."""
    path = Path(relative)
    safe_bundle_path(Path("/unused"), relative)
    if len(path.parts) < 2 or path.parts[0] != "attachments":
        raise RecoveryError(f"Invalid attachment path in bundle manifest: {relative!r}")
    return path


def attachment_reference_matches_path(reference: str, relative_path: str) -> bool:
    """Accept only exact relative or absolute forms of a declared attachment path."""
    normalized = reference.replace("\\", "/")
    expected = relative_path.replace("\\", "/")
    if normalized == expected:
        return True
    is_absolute = Path(reference).is_absolute() or PureWindowsPath(reference).is_absolute()
    return is_absolute and normalized.endswith("/" + expected)


def read_json_lines(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Read JSONL without ever printing its user-message contents."""
    records: list[dict[str, Any]] = []
    invalid = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue
            if isinstance(value, dict):
                records.append(value)
            else:
                invalid += 1
    return records, invalid


def thread_id_from_path(path: Path) -> str | None:
    matches = UUID_RE.findall(path.name)
    return matches[-1].lower() if matches else None


def session_metadata(records: Iterable[dict[str, Any]], fallback_id: str | None) -> tuple[str | None, str | None]:
    """Return only non-content metadata needed for recovery decisions."""
    session_id = fallback_id
    provider: str | None = None
    for record in records:
        if record.get("type") != "session_meta":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            payload = record
        for id_key in ("id", "thread_id", "session_id"):
            candidate = payload.get(id_key)
            if isinstance(candidate, str) and UUID_RE.fullmatch(candidate):
                session_id = candidate.lower()
                break
        candidate_provider = payload.get("model_provider")
        if isinstance(candidate_provider, str) and candidate_provider:
            provider = candidate_provider
        break
    return session_id, provider


def resolve_context_root(raw_root: str | Path) -> Path:
    """Accept either a live CODEX_HOME or a bundle/extracted live-context root."""
    root = Path(raw_root).expanduser().resolve()
    candidates = (root, root / "live-context")
    for candidate in candidates:
        if (candidate / "sessions").is_dir() or (candidate / "archived_sessions").is_dir():
            return candidate
    raise RecoveryError(
        f"No sessions/ or archived_sessions/ directory found under {root}. "
        "Pass a CODEX_HOME, an extracted live-context directory, or a recovery bundle."
    )


def session_files(context_root: Path, include_archived: bool = True) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for category in SESSION_CATEGORIES:
        if category == "archived_sessions" and not include_archived:
            continue
        directory = context_root / category
        if not directory.is_dir():
            continue
        if directory.is_symlink():
            raise RecoveryError(f"Refusing to inspect a symlinked {category} directory: {directory}")
        for path in sorted(directory.rglob("*.jsonl")):
            if path.is_symlink():
                raise RecoveryError(f"Refusing to inspect a symlinked session file: {path}")
            if path.is_file():
                relative_to(path, context_root / category)
                require_regular_unlinked_file(path, "source session file")
                found.append((category, path))
    return found


def recovery_data_entries(context_root: Path) -> list[str]:
    """Return only paths that make a nominally empty restore target nonempty."""
    found: list[str] = []
    for category in (*SESSION_CATEGORIES, "attachments"):
        directory = context_root / category
        if not directory.is_dir():
            continue
        if directory.is_symlink():
            raise RecoveryError(f"Refusing to inspect a symlinked {category} directory: {directory}")
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise RecoveryError(f"Refusing to inspect a symlinked {category} recovery-data entry: {path}")
            if path.is_file():
                found.append(f"{category}/{relative_to(path, directory).as_posix()}")
    return found


def scan_context(context_root: Path, include_archived: bool = True) -> dict[str, Any]:
    threads: list[dict[str, Any]] = []
    all_invalid_lines = 0
    providers: Counter[str] = Counter()
    duplicate_ids: set[str] = set()
    seen_ids: set[str] = set()

    for category, path in session_files(context_root, include_archived=include_archived):
        records, invalid_lines = read_json_lines(path)
        fallback_id = thread_id_from_path(path)
        thread_id, provider = session_metadata(records, fallback_id)
        all_invalid_lines += invalid_lines
        if thread_id:
            if thread_id in seen_ids:
                duplicate_ids.add(thread_id)
            seen_ids.add(thread_id)
        if provider:
            providers[provider] += 1
        threads.append(
            {
                "id": thread_id,
                "category": category,
                "relative_path": f"{category}/{canonical_bundle_relative(path, context_root / category)}",
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "model_provider": provider,
                "invalid_jsonl_lines": invalid_lines,
            }
        )

    index_path = context_root / "session_index.jsonl"
    index_entries: dict[str, str] = {}
    index_invalid_lines = 0
    duplicate_index_ids: set[str] = set()
    if index_path.is_symlink():
        raise RecoveryError(f"Refusing to inspect a symlinked session index: {index_path}")
    if index_path.is_file():
        require_regular_unlinked_file(index_path, "session index")
        with index_path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    index_invalid_lines += 1
                    continue
                thread_id = entry.get("id") if isinstance(entry, dict) else None
                if isinstance(thread_id, str) and UUID_RE.fullmatch(thread_id):
                    normalized = thread_id.lower()
                    if normalized in index_entries:
                        duplicate_index_ids.add(normalized)
                    else:
                        index_entries[normalized] = raw_line if raw_line.endswith("\n") else raw_line + "\n"
                else:
                    index_invalid_lines += 1

    return {
        "context_root": str(context_root),
        "threads": threads,
        "thread_count": len(threads),
        "thread_ids": sorted(thread_id for thread_id in seen_ids if thread_id),
        "duplicate_thread_ids": sorted(duplicate_ids),
        "providers": dict(sorted(providers.items())),
        "invalid_jsonl_lines": all_invalid_lines,
        "index_path": str(index_path) if index_path.is_file() else None,
        "index_entries": index_entries,
        "index_invalid_lines": index_invalid_lines,
        "duplicate_index_ids": sorted(duplicate_index_ids),
        "recovery_data_entries": recovery_data_entries(context_root),
    }


def load_toml(path: Path) -> dict[str, Any]:
    if tomllib is None:
        raise RecoveryError("Python 3.11 or newer is required to inspect config.toml safely.")
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            parsed = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise RecoveryError(f"Cannot parse TOML at {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RecoveryError(f"Unexpected TOML root at {path}")
    return parsed


def configured_provider_ids(config_path: Path | None) -> set[str]:
    if config_path is None or not config_path.is_file():
        return set(BUILTIN_PROVIDERS)
    parsed = load_toml(config_path)
    tables = parsed.get("model_providers", {})
    if not isinstance(tables, dict):
        raise RecoveryError(f"model_providers is not a table in {config_path}")
    return set(BUILTIN_PROVIDERS) | {key for key, value in tables.items() if isinstance(value, dict)}


def compact_inventory(
    scan: dict[str, Any], config_path: Path | None = None, target_scan: dict[str, Any] | None = None
) -> dict[str, Any]:
    available = configured_provider_ids(config_path)
    source_providers = set(scan["providers"])
    session_file_counts = Counter(thread["category"] for thread in scan["threads"])
    report = {
        "tool_version": TOOL_VERSION,
        "source_context": scan["context_root"],
        "thread_count": scan["thread_count"],
        "session_file_counts": {category: session_file_counts.get(category, 0) for category in SESSION_CATEGORIES},
        "thread_ids": scan["thread_ids"],
        "duplicate_thread_ids": scan["duplicate_thread_ids"],
        "source_provider_counts": scan["providers"],
        "invalid_jsonl_lines": scan["invalid_jsonl_lines"],
        "session_index_records": len(scan["index_entries"]),
        "session_index_invalid_lines": scan["index_invalid_lines"],
        "duplicate_session_index_ids": scan["duplicate_index_ids"],
        "target_config": str(config_path) if config_path and config_path.is_file() else None,
        "target_provider_ids": sorted(available),
        "missing_target_provider_ids": sorted(source_providers - available),
    }
    if target_scan is not None:
        report.update(
            {
                "target_session_file_count": target_scan["thread_count"],
                "target_session_index_records": len(target_scan["index_entries"]),
                "target_invalid_jsonl_lines": target_scan["invalid_jsonl_lines"],
                "target_session_index_invalid_lines": target_scan["index_invalid_lines"],
                "target_duplicate_session_ids": target_scan["duplicate_thread_ids"],
                "target_duplicate_session_index_ids": target_scan["duplicate_index_ids"],
                "target_recovery_data_entries": len(target_scan["recovery_data_entries"]),
            }
        )
    return report


def print_report(report: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if "source_context" not in report:
        for key in sorted(report):
            value = report[key]
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True)
            print(f"{key}: {value}")
        return
    print(f"source context: {report['source_context']}")
    print(f"valid session files: {report['thread_count']}")
    category_counts = report["session_file_counts"]
    print(
        "session files by location: "
        + ", ".join(f"{category}={category_counts.get(category, 0)}" for category in SESSION_CATEGORIES)
    )
    print(f"session IDs: {len(report['thread_ids'])}")
    print(f"invalid JSONL lines: {report['invalid_jsonl_lines']}")
    print(f"session-index records: {report['session_index_records']}")
    print("source provider IDs: " + (", ".join(report["source_provider_counts"]) or "none recorded"))
    if report["target_config"]:
        print(f"target config: {report['target_config']}")
    missing = report["missing_target_provider_ids"]
    print("missing target provider IDs: " + (", ".join(missing) if missing else "none"))
    if report["duplicate_thread_ids"]:
        print("duplicate session IDs: " + ", ".join(report["duplicate_thread_ids"]))
    if report["duplicate_session_index_ids"]:
        print("duplicate session-index IDs: " + ", ".join(report["duplicate_session_index_ids"]))
    if "target_session_file_count" in report:
        print(f"target session files: {report['target_session_file_count']}")
        print(f"target session-index records: {report['target_session_index_records']}")
        print(f"target session/attachment files: {report['target_recovery_data_entries']}")


def select_threads(scan: dict[str, Any], requested: list[str] | None, select_all: bool) -> list[dict[str, Any]]:
    if not requested and not select_all:
        raise RecoveryError("Select one or more --thread IDs, or pass --all-sessions explicitly.")
    wanted = {item.lower() for item in requested or []}
    by_id = {thread["id"]: thread for thread in scan["threads"] if thread["id"]}
    missing = sorted(wanted - set(by_id))
    if missing:
        raise RecoveryError("Requested session IDs were not found: " + ", ".join(missing))
    selected = list(by_id.values()) if select_all else [by_id[thread_id] for thread_id in sorted(wanted)]
    selected_ids = {thread["id"] for thread in selected}
    duplicated = selected_ids & set(scan["duplicate_thread_ids"])
    if duplicated:
        raise RecoveryError(
            "Refusing to select duplicate session IDs without a manual forensic decision: "
            + ", ".join(sorted(duplicated))
        )
    if any(thread["invalid_jsonl_lines"] for thread in selected):
        broken = [thread["id"] for thread in selected if thread["invalid_jsonl_lines"]]
        raise RecoveryError("Refusing to package invalid JSONL session(s): " + ", ".join(broken))
    return selected


@dataclass(frozen=True)
class JsonNode:
    """A minimal JSON syntax tree that retains the byte spans of string tokens."""

    kind: str
    start: int
    end: int
    value: str | None = None
    object_pairs: list[tuple[str, "JsonNode"]] | None = None
    items: list["JsonNode"] | None = None


class JsonSpanParser:
    """Strict enough JSON walker to locate a registered slot without rewriting a line."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.position = 0

    def fail(self, message: str) -> None:
        raise ValueError(f"{message} at character {self.position}")

    def skip_whitespace(self) -> None:
        while self.position < len(self.text) and self.text[self.position] in " \t\r\n":
            self.position += 1

    def parse_document(self) -> JsonNode:
        self.skip_whitespace()
        node = self.parse_value()
        self.skip_whitespace()
        if self.position != len(self.text):
            self.fail("Unexpected trailing data")
        return node

    def parse_value(self) -> JsonNode:
        self.skip_whitespace()
        if self.position >= len(self.text):
            self.fail("Expected a JSON value")
        character = self.text[self.position]
        if character == '"':
            return self.parse_string()
        if character == "{":
            return self.parse_object()
        if character == "[":
            return self.parse_array()
        return self.parse_primitive()

    def parse_string(self) -> JsonNode:
        start = self.position
        self.position += 1
        while self.position < len(self.text):
            character = self.text[self.position]
            if character == '"':
                self.position += 1
                token = self.text[start : self.position]
                try:
                    value = json.loads(token)
                except json.JSONDecodeError as exc:
                    raise ValueError("Invalid JSON string token") from exc
                if not isinstance(value, str):  # Defensive; a quoted token is always a string.
                    self.fail("Expected a JSON string")
                return JsonNode("string", start, self.position, value=value)
            if character == "\\":
                self.position += 1
                if self.position >= len(self.text):
                    self.fail("Unterminated JSON escape")
                escape = self.text[self.position]
                if escape == "u":
                    digits = self.text[self.position + 1 : self.position + 5]
                    if len(digits) != 4 or any(digit not in "0123456789abcdefABCDEF" for digit in digits):
                        self.fail("Invalid JSON unicode escape")
                    self.position += 5
                    continue
                if escape not in '"\\/bfnrt':
                    self.fail("Invalid JSON escape")
                self.position += 1
                continue
            if ord(character) < 0x20:
                self.fail("Control character in JSON string")
            self.position += 1
        self.fail("Unterminated JSON string")

    def parse_object(self) -> JsonNode:
        start = self.position
        self.position += 1
        pairs: list[tuple[str, JsonNode]] = []
        self.skip_whitespace()
        if self.position < len(self.text) and self.text[self.position] == "}":
            self.position += 1
            return JsonNode("object", start, self.position, object_pairs=pairs)
        while True:
            self.skip_whitespace()
            if self.position >= len(self.text) or self.text[self.position] != '"':
                self.fail("Expected an object key")
            key = self.parse_string()
            self.skip_whitespace()
            if self.position >= len(self.text) or self.text[self.position] != ":":
                self.fail("Expected ':' after object key")
            self.position += 1
            value = self.parse_value()
            pairs.append((key.value or "", value))
            self.skip_whitespace()
            if self.position >= len(self.text):
                self.fail("Unterminated JSON object")
            separator = self.text[self.position]
            self.position += 1
            if separator == "}":
                return JsonNode("object", start, self.position, object_pairs=pairs)
            if separator != ",":
                self.fail("Expected ',' or '}' in object")

    def parse_array(self) -> JsonNode:
        start = self.position
        self.position += 1
        items: list[JsonNode] = []
        self.skip_whitespace()
        if self.position < len(self.text) and self.text[self.position] == "]":
            self.position += 1
            return JsonNode("array", start, self.position, items=items)
        while True:
            items.append(self.parse_value())
            self.skip_whitespace()
            if self.position >= len(self.text):
                self.fail("Unterminated JSON array")
            separator = self.text[self.position]
            self.position += 1
            if separator == "]":
                return JsonNode("array", start, self.position, items=items)
            if separator != ",":
                self.fail("Expected ',' or ']' in array")

    def parse_primitive(self) -> JsonNode:
        start = self.position
        while self.position < len(self.text) and self.text[self.position] not in " \t\r\n,]}":
            self.position += 1
        token = self.text[start : self.position]
        if not token:
            self.fail("Expected a JSON primitive")
        try:
            json.loads(token)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON primitive") from exc
        return JsonNode("primitive", start, self.position)


@dataclass(frozen=True)
class AttachmentSlot:
    line: int
    pointer: str
    source_reference: str
    start: int
    end: int


def object_values(node: JsonNode, key: str) -> list[JsonNode]:
    return [value for candidate, value in node.object_pairs or [] if candidate == key]


def registered_attachment_slots_from_line(raw_line: str, line_number: int, label: str) -> list[AttachmentSlot]:
    """Return only the documented `event_msg.payload.local_images[]` string slots."""
    try:
        root = JsonSpanParser(raw_line).parse_document()
    except ValueError as exc:
        raise RecoveryError(f"Cannot inspect attachment slots in {label} line {line_number}: {exc}") from exc
    if root.kind != "object":
        return []
    type_nodes = object_values(root, "type")
    if not type_nodes:
        return []
    event_message_types = [node for node in type_nodes if node.kind == "string" and node.value == "event_msg"]
    if len(type_nodes) != 1:
        if event_message_types:
            raise RecoveryError(f"Ambiguous event_msg type field in {label} line {line_number}.")
        return []
    if type_nodes[0].kind != "string" or type_nodes[0].value != "event_msg":
        return []
    payload_nodes = object_values(root, "payload")
    if not payload_nodes:
        return []
    if len(payload_nodes) != 1 or payload_nodes[0].kind != "object":
        raise RecoveryError(f"event_msg payload is not an unambiguous object in {label} line {line_number}.")
    local_images_nodes = object_values(payload_nodes[0], "local_images")
    if not local_images_nodes:
        return []
    if len(local_images_nodes) != 1 or local_images_nodes[0].kind != "array":
        raise RecoveryError(f"event_msg local_images is not an unambiguous array in {label} line {line_number}.")
    slots: list[AttachmentSlot] = []
    for index, item in enumerate(local_images_nodes[0].items or []):
        if item.kind != "string" or item.value is None:
            raise RecoveryError(f"event_msg local_images contains a non-string value in {label} line {line_number}.")
        slots.append(
            AttachmentSlot(
                line=line_number,
                pointer=f"/payload/local_images/{index}",
                source_reference=item.value,
                start=item.start,
                end=item.end,
            )
        )
    return slots


def registered_attachment_slots(session_path: Path) -> dict[tuple[int, str], AttachmentSlot]:
    """Read physical JSONL lines while retaining exact token locations for safe remapping."""
    slots: dict[tuple[int, str], AttachmentSlot] = {}
    try:
        with session_path.open("r", encoding="utf-8", newline="") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                if not raw_line.strip():
                    continue
                try:
                    json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise RecoveryError(f"Cannot inspect attachment slots in invalid JSONL: {session_path}") from exc
                for slot in registered_attachment_slots_from_line(raw_line, line_number, str(session_path)):
                    key = (slot.line, slot.pointer)
                    if key in slots:
                        raise RecoveryError(f"Duplicate attachment slot in {session_path} line {line_number}.")
                    slots[key] = slot
    except (OSError, UnicodeError) as exc:
        raise RecoveryError(f"Cannot read attachment slots from {session_path}") from exc
    return slots


def attachment_candidate_from_reference(raw_value: str, attachment_root: Path) -> Path | None:
    """Resolve only explicit attachment-root references, without accepting arbitrary paths."""
    value_path = Path(raw_value)
    if value_path.is_absolute():
        try:
            relative = value_path.resolve().relative_to(attachment_root.resolve())
        except (ValueError, OSError):
            return None
        return attachment_root / relative
    normalized = raw_value.replace("\\", "/")
    if not normalized.startswith("attachments/"):
        return None
    suffix = normalized.removeprefix("attachments/")
    return safe_bundle_path(attachment_root, suffix)


def attachment_location_record(slot: AttachmentSlot) -> dict[str, Any]:
    return {
        "line": slot.line,
        "pointer": slot.pointer,
        "source_reference": slot.source_reference,
    }


def attachment_location_sort_key(location: dict[str, Any]) -> tuple[int, str, str]:
    return (int(location["line"]), str(location["pointer"]), str(location["source_reference"]))


def referenced_attachments(session_path: Path, source_root: Path) -> tuple[dict[Path, list[AttachmentSlot]], int]:
    attachment_root = source_root / "attachments"
    if attachment_root.is_symlink():
        raise RecoveryError(f"Refusing to inspect a symlinked attachments directory: {attachment_root}")
    has_attachment_root = attachment_root.is_dir()
    found: dict[Path, list[AttachmentSlot]] = {}
    missing: set[tuple[int, str]] = set()
    for slot in registered_attachment_slots(session_path).values():
        candidate = attachment_candidate_from_reference(slot.source_reference, attachment_root)
        if candidate is None:
            continue
        if not has_attachment_root:
            missing.add((slot.line, slot.pointer))
            continue
        try:
            relative_to(candidate, attachment_root)
            if candidate.is_symlink():
                raise RecoveryError(f"Refusing to inspect a symlinked source attachment: {candidate}")
            if candidate.is_file():
                require_regular_unlinked_file(candidate, "source attachment")
                found.setdefault(candidate, []).append(slot)
            else:
                missing.add((slot.line, slot.pointer))
        except OSError:
            missing.add((slot.line, slot.pointer))
    return found, len(missing)


def make_manifest(source_root: Path, scan: dict[str, Any], selected: list[dict[str, Any]], include_attachments: bool) -> tuple[dict[str, Any], list[tuple[Path, str]]]:
    copies: list[tuple[Path, str]] = []
    attachment_records_by_path: dict[str, dict[str, Any]] = {}
    unresolved_attachment_references = 0
    selected_ids = {thread["id"] for thread in selected}
    for thread in selected:
        source = safe_bundle_path(source_root, thread["relative_path"])
        copies.append((source, thread["relative_path"]))
        if include_attachments:
            attachments, unresolved = referenced_attachments(source, source_root)
            unresolved_attachment_references += unresolved
            for attachment, references in sorted(attachments.items()):
                relative = f"attachments/{canonical_bundle_relative(attachment, source_root / 'attachments')}"
                record = attachment_records_by_path.get(relative)
                if record is None:
                    record = {
                        "relative_path": relative,
                        "bytes": attachment.stat().st_size,
                        "sha256": sha256_file(attachment),
                        "references_by_thread": {
                            thread["id"]: sorted(
                                (attachment_location_record(slot) for slot in references),
                                key=attachment_location_sort_key,
                            )
                        },
                    }
                    attachment_records_by_path[relative] = record
                    copies.append((attachment, relative))
                else:
                    existing_references = record["references_by_thread"].get(thread["id"], [])
                    combined_references = {
                        (entry["line"], entry["pointer"], entry["source_reference"]): entry
                        for entry in existing_references
                    }
                    for slot in references:
                        entry = attachment_location_record(slot)
                        combined_references[(entry["line"], entry["pointer"], entry["source_reference"])] = entry
                    record["references_by_thread"][thread["id"]] = sorted(
                        combined_references.values(), key=attachment_location_sort_key
                    )

    omitted_duplicate_index_ids = sorted(selected_ids & set(scan["duplicate_index_ids"]))
    selected_index = {
        thread_id: line
        for thread_id, line in scan["index_entries"].items()
        if thread_id in selected_ids and thread_id not in omitted_duplicate_index_ids
    }
    manifest = {
        "format": BUNDLE_FORMAT,
        "format_version": BUNDLE_VERSION,
        "tool_version": TOOL_VERSION,
        "created_at": utc_now(),
        "threads": selected,
        "thread_ids": sorted(selected_ids),
        "provider_contract": {
            "legacy_provider_ids": sorted(
                {thread["model_provider"] for thread in selected if thread.get("model_provider")}
            ),
            "note": "Provider IDs only; source config and credentials are intentionally excluded.",
        },
        "session_index": {
            "relative_path": "session_index.jsonl" if selected_index else None,
            "record_count": len(selected_index),
            "omitted_duplicate_thread_ids": omitted_duplicate_index_ids,
        },
        "attachments": [attachment_records_by_path[path] for path in sorted(attachment_records_by_path)],
        "unresolved_attachment_references": unresolved_attachment_references,
        "excluded": [
            "auth.json",
            "config.toml and config profiles",
            "SQLite state/history databases",
            "secrets, tokens, cookies, logs, caches, plugins",
        ],
    }
    return manifest, copies


def copy_file(source: Path, destination: Path) -> None:
    """Create a private new file without preserving source modes or xattrs."""
    require_regular_unlinked_file(source, "source payload")
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with source.open("rb") as source_handle, os.fdopen(descriptor, "wb") as destination_handle:
            shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
    except BaseException:
        if created:
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
        raise


def publish_new_file(temporary: str, destination: Path) -> None:
    """Publish a completed temp file only if the destination still does not exist."""
    try:
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise RecoveryError(f"Unexpected destination collision while restoring: {destination}") from exc
    except OSError as exc:
        raise RecoveryError(f"Cannot safely publish restored file: {destination}") from exc
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def remap_attachment_slots_in_line(
    raw_line: str,
    line_number: int,
    source_label: str,
    replacements: dict[tuple[int, str], tuple[str, str]],
) -> tuple[str, int, set[tuple[int, str]]]:
    """Replace only verified quoted tokens in registered attachment slots.

    The line is never parsed and re-serialized as a whole: number spellings,
    duplicate JSON keys, whitespace, and unrelated message text are retained
    byte-for-byte (subject only to the text decoding already required by JSONL).
    """
    spans: list[tuple[AttachmentSlot, str]] = []
    matched: set[tuple[int, str]] = set()
    for slot in registered_attachment_slots_from_line(raw_line, line_number, source_label):
        key = (slot.line, slot.pointer)
        replacement = replacements.get(key)
        if replacement is None:
            continue
        destination, expected_source_reference = replacement
        if slot.source_reference != expected_source_reference:
            raise RecoveryError(
                f"Attachment slot changed after bundle validation in {source_label} line {line_number}."
            )
        spans.append((slot, destination))
        matched.add(key)
    if not spans:
        return raw_line, 0, matched
    pieces: list[str] = []
    cursor = 0
    for slot, destination in sorted(spans, key=lambda item: item[0].start):
        pieces.append(raw_line[cursor : slot.start])
        pieces.append(json.dumps(destination, ensure_ascii=False))
        cursor = slot.end
    pieces.append(raw_line[cursor:])
    return "".join(pieces), len(spans), matched


def copy_session_with_attachment_remap(
    source: Path, destination: Path, replacements: dict[tuple[int, str], tuple[str, str]]
) -> int:
    """Copy a session atomically, remapping only registered attachment tokens."""
    if not replacements:
        copy_file(source, destination)
        return 0
    require_regular_unlinked_file(source, "source session payload")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
    changed = 0
    unmatched = set(replacements)
    try:
        with source.open("r", encoding="utf-8", newline="") as source_handle, os.fdopen(
            fd, "w", encoding="utf-8", newline=""
        ) as destination_handle:
            for line_number, raw_line in enumerate(source_handle, start=1):
                if not raw_line.strip():
                    destination_handle.write(raw_line)
                    continue
                rewritten, replacements_in_line, matched = remap_attachment_slots_in_line(
                    raw_line, line_number, str(source), replacements
                )
                destination_handle.write(rewritten)
                changed += replacements_in_line
                unmatched.difference_update(matched)
            if unmatched:
                missing_slots = ", ".join(f"line {line}, {pointer}" for line, pointer in sorted(unmatched))
                raise RecoveryError(
                    f"A verified attachment slot disappeared before restore in {source}: {missing_slots}"
                )
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        os.chmod(temporary, 0o600)
        publish_new_file(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return changed


def write_json_atomic(path: Path, value: Any, mode: int | None = None) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_text_atomic(path, payload, mode=mode)


def write_bytes_atomic(path: Path, payload: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        elif existing_mode is not None:
            os.chmod(temporary, existing_mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_text_atomic(path: Path, text: str, mode: int | None = None) -> None:
    write_bytes_atomic(path, text.encode("utf-8"), mode=mode)


def command_inventory(args: argparse.Namespace) -> int:
    source = resolve_context_root(args.source)
    scan = scan_context(source)
    target_root: Path | None = None
    target_config: Path | None = None
    target_scan: dict[str, Any] | None = None
    if args.target:
        target_root = Path(args.target).expanduser().resolve()
        if not target_root.is_dir():
            raise RecoveryError(f"Target CODEX_HOME does not exist: {target_root}")
        target_config = target_root / "config.toml"
        target_scan = scan_context(target_root)
    report = compact_inventory(scan, target_config, target_scan)
    print_report(report, args.json)
    return 0


def command_bundle(args: argparse.Namespace) -> int:
    source = resolve_context_root(args.source)
    scan = scan_context(source, include_archived=args.include_archived)
    selected = select_threads(scan, args.thread, args.all_sessions)
    output = Path(args.output).expanduser().resolve()
    try:
        output.relative_to(source)
    except ValueError:
        pass
    else:
        raise RecoveryError("Bundle output must not be placed inside the source CODEX_HOME.")
    git_root = enclosing_git_worktree(output.parent)
    if git_root is not None:
        raise RecoveryError(
            f"Bundle output is inside Git worktree {git_root}; choose a private staging directory outside version control."
        )
    manifest, copies = make_manifest(source, scan, selected, args.include_attachments)
    if args.include_attachments and manifest["unresolved_attachment_references"]:
        raise RecoveryError(
            "Selected sessions contain "
            f"{manifest['unresolved_attachment_references']} recognizable attachment reference(s) that are unavailable. "
            "Resolve them before creating a purportedly complete attachment bundle."
        )
    plan = {
        "operation": "bundle",
        "output": str(output),
        "thread_ids": manifest["thread_ids"],
        "session_files": len(selected),
        "attachment_files": len(manifest["attachments"]),
        "session_index_records": manifest["session_index"]["record_count"],
        "omitted_ambiguous_session_index_records": len(
            manifest["session_index"]["omitted_duplicate_thread_ids"]
        ),
        "unresolved_attachment_references": manifest["unresolved_attachment_references"],
        "will_write": bool(args.apply),
    }
    if not args.apply:
        print_report(plan, args.json)
        print("Dry run only. Re-run with --apply to create the new bundle directory.")
        return 0
    if output.exists():
        raise RecoveryError(f"Output already exists; refusing to merge or overwrite: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=output.name + ".tmp-", dir=output.parent))
    os.chmod(staging, 0o700)
    try:
        for source_file, relative in copies:
            destination = safe_bundle_path(staging, relative)
            ensure_private_destination_parent(staging, destination, "bundle")
            copy_file(source_file, destination)
        omitted_index_ids = set(manifest["session_index"]["omitted_duplicate_thread_ids"])
        index_lines = [
            scan["index_entries"][thread_id]
            for thread_id in manifest["thread_ids"]
            if thread_id in scan["index_entries"] and thread_id not in omitted_index_ids
        ]
        if index_lines:
            index_path = staging / "session_index.jsonl"
            write_text_atomic(index_path, "".join(index_lines))
            manifest["session_index"]["sha256"] = sha256_file(index_path)
        for payload in [*manifest["threads"], *manifest["attachments"]]:
            packaged = safe_bundle_path(staging, payload["relative_path"])
            if sha256_file(packaged) != payload["sha256"] or packaged.stat().st_size != payload["bytes"]:
                raise RecoveryError(f"Payload changed while packaging {payload['relative_path']}")
        write_json_atomic(staging / "manifest.json", manifest)
        os.replace(staging, output)
        os.chmod(output, 0o700)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    plan["manifest"] = str(output / "manifest.json")
    print_report(plan, args.json)
    return 0


def load_manifest(bundle: Path) -> dict[str, Any]:
    manifest_path = bundle / "manifest.json"
    if not manifest_path.is_file():
        raise RecoveryError(f"Missing bundle manifest: {manifest_path}")
    if manifest_path.is_symlink():
        raise RecoveryError("Bundle manifest may not be a symlink.")
    require_regular_unlinked_file(manifest_path, "bundle manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RecoveryError(f"Invalid bundle manifest: {manifest_path}") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != BUNDLE_FORMAT:
        raise RecoveryError("This is not a Codex account recovery bundle.")
    if manifest.get("format_version") != BUNDLE_VERSION:
        raise RecoveryError(f"Unsupported bundle format version: {manifest.get('format_version')}")
    if not isinstance(manifest.get("threads"), list):
        raise RecoveryError("Bundle manifest has no thread list.")
    return manifest


def manifest_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise RecoveryError(f"Bundle manifest has an invalid SHA-256 for {field}.")
    return value


def manifest_bytes(value: Any, field: str) -> int:
    if not isinstance(value, int) or value < 0:
        raise RecoveryError(f"Bundle manifest has an invalid byte count for {field}.")
    return value


def manifest_attachment_location(value: Any) -> tuple[int, str, str]:
    if not isinstance(value, dict) or set(value) != {"line", "pointer", "source_reference"}:
        raise RecoveryError("Bundle attachment has malformed task-scoped location metadata.")
    line = value.get("line")
    pointer = value.get("pointer")
    source_reference = value.get("source_reference")
    if (
        not isinstance(line, int)
        or line < 1
        or not isinstance(pointer, str)
        or not ATTACHMENT_POINTER_RE.fullmatch(pointer)
        or not isinstance(source_reference, str)
        or not source_reference
    ):
        raise RecoveryError("Bundle attachment has an invalid task-scoped location.")
    return line, pointer, source_reference


def validate_manifest_index(bundle: Path, manifest: dict[str, Any], thread_ids: set[str]) -> set[str]:
    index = manifest.get("session_index", {})
    if not isinstance(index, dict):
        raise RecoveryError("Bundle manifest has a malformed session-index record.")
    relative = index.get("relative_path")
    count = index.get("record_count")
    omitted_duplicate_ids = index.get("omitted_duplicate_thread_ids", [])
    if not isinstance(count, int) or count < 0:
        raise RecoveryError("Bundle manifest has an invalid session-index record count.")
    if (
        not isinstance(omitted_duplicate_ids, list)
        or any(
            not isinstance(thread_id, str)
            or not UUID_RE.fullmatch(thread_id)
            or thread_id != thread_id.lower()
            for thread_id in omitted_duplicate_ids
        )
        or len(set(omitted_duplicate_ids)) != len(omitted_duplicate_ids)
        or not set(omitted_duplicate_ids).issubset(thread_ids)
    ):
        raise RecoveryError("Bundle manifest has invalid omitted duplicate session-index IDs.")
    if relative is None:
        if count != 0 or "sha256" in index:
            raise RecoveryError("Bundle manifest has inconsistent session-index metadata.")
        return set()
    if relative != "session_index.jsonl":
        raise RecoveryError("Bundle manifest may contain only a root session_index.jsonl index file.")
    expected = manifest_sha256(index.get("sha256"), "session_index.jsonl")
    index_path = safe_bundle_path(bundle, relative)
    if index_path.is_symlink():
        raise RecoveryError("Bundle session_index.jsonl may not be a symlink.")
    if not index_path.is_file():
        raise RecoveryError("Bundle integrity check failed for session_index.jsonl")
    require_regular_unlinked_file(index_path, "bundle session index")
    if sha256_file(index_path) != expected:
        raise RecoveryError("Bundle integrity check failed for session_index.jsonl")

    seen: set[str] = set()
    with index_path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RecoveryError("Bundle session_index.jsonl is invalid.") from exc
            thread_id = entry.get("id") if isinstance(entry, dict) else None
            if not isinstance(thread_id, str) or not UUID_RE.fullmatch(thread_id):
                raise RecoveryError("Bundle session_index.jsonl contains an invalid task ID.")
            normalized = thread_id.lower()
            if normalized not in thread_ids or normalized in omitted_duplicate_ids or normalized in seen:
                raise RecoveryError("Bundle session_index.jsonl contains duplicate or unselected task records.")
            seen.add(normalized)
    if len(seen) != count:
        raise RecoveryError("Bundle session-index record count does not match its contents.")
    return {relative}


def validate_bundle_layout(bundle: Path, allowed_files: set[str]) -> None:
    allowed_directories: set[str] = set()
    for relative in allowed_files:
        parts = Path(relative).parts[:-1]
        for length in range(1, len(parts) + 1):
            allowed_directories.add(Path(*parts[:length]).as_posix())
    for artifact in bundle.rglob("*"):
        relative = canonical_bundle_relative(artifact, bundle)
        if artifact.is_symlink():
            raise RecoveryError(f"Bundle contains a symlink, which is not allowed: {relative}")
        if artifact.is_dir():
            if relative not in allowed_directories:
                raise RecoveryError(f"Bundle contains an unexpected directory: {relative}")
        elif artifact.is_file():
            if relative not in allowed_files:
                raise RecoveryError(f"Bundle contains an unexpected file: {relative}")
        else:
            raise RecoveryError(f"Bundle contains an unsupported filesystem object: {relative}")


def validate_bundle(bundle: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    if not bundle.is_dir():
        raise RecoveryError(f"Recovery bundle directory does not exist: {bundle}")
    checked = 0
    seen_thread_ids: set[str] = set()
    seen_paths: set[str] = set()
    session_attachment_slots_by_thread: dict[str, dict[tuple[int, str], AttachmentSlot]] = {}
    allowed_files = {"manifest.json"}
    for thread in manifest["threads"]:
        if not isinstance(thread, dict):
            raise RecoveryError("Malformed thread record in bundle manifest.")
        thread_id = thread.get("id")
        relative = thread.get("relative_path")
        expected = manifest_sha256(thread.get("sha256"), "a session file")
        expected_bytes = manifest_bytes(thread.get("bytes"), "a session file")
        if not isinstance(thread_id, str) or not UUID_RE.fullmatch(thread_id):
            raise RecoveryError("Thread record is missing a valid task ID.")
        thread_id = thread_id.lower()
        if thread.get("id") != thread_id:
            raise RecoveryError("Bundle task IDs must use canonical lowercase UUIDs.")
        if thread_id in seen_thread_ids:
            raise RecoveryError("Bundle manifest contains duplicate task IDs.")
        if not isinstance(relative, str):
            raise RecoveryError("Thread record is missing a session path.")
        session_relative_path(relative)
        if relative in seen_paths:
            raise RecoveryError("Bundle manifest contains duplicate session paths.")
        seen_thread_ids.add(thread_id)
        seen_paths.add(relative)
        path = safe_bundle_path(bundle, relative)
        if path.is_symlink():
            raise RecoveryError(f"Bundle session file may not be a symlink: {relative}")
        if not path.is_file():
            raise RecoveryError(f"Bundle integrity check failed for {relative}")
        require_regular_unlinked_file(path, "bundle session file")
        if sha256_file(path) != expected or path.stat().st_size != expected_bytes:
            raise RecoveryError(f"Bundle integrity check failed for {relative}")
        records, invalid = read_json_lines(path)
        if invalid or not records:
            raise RecoveryError(f"Bundle JSONL validation failed for {relative}")
        session_attachment_slots_by_thread[thread_id] = registered_attachment_slots(path)
        actual_id, actual_provider = session_metadata(records, thread_id_from_path(path))
        if actual_id != thread_id:
            raise RecoveryError(f"Bundle task ID does not match session metadata for {relative}")
        declared_provider = thread.get("model_provider")
        if declared_provider is not None and (not isinstance(declared_provider, str) or not declared_provider):
            raise RecoveryError(f"Bundle manifest has an invalid provider ID for {relative}")
        if declared_provider != actual_provider:
            raise RecoveryError(f"Bundle provider metadata does not match session metadata for {relative}")
        allowed_files.add(relative)
        checked += 1
    if not seen_thread_ids:
        raise RecoveryError("Bundle manifest contains no task records.")
    declared_ids = manifest.get("thread_ids")
    if (
        not isinstance(declared_ids, list)
        or any(not isinstance(item, str) or item != item.lower() for item in declared_ids)
        or sorted(declared_ids) != sorted(seen_thread_ids)
    ):
        raise RecoveryError("Bundle manifest thread_ids do not match its task records.")
    provider_contract = manifest.get("provider_contract")
    if not isinstance(provider_contract, dict) or not isinstance(provider_contract.get("legacy_provider_ids"), list):
        raise RecoveryError("Bundle manifest has a malformed provider contract.")
    declared_providers = provider_contract["legacy_provider_ids"]
    actual_providers = sorted(
        {thread.get("model_provider") for thread in manifest["threads"] if thread.get("model_provider")}
    )
    if (
        any(not isinstance(provider, str) or not provider for provider in declared_providers)
        or sorted(declared_providers) != actual_providers
    ):
        raise RecoveryError("Bundle provider contract does not match its task records.")
    if not isinstance(manifest.get("attachments", []), list):
        raise RecoveryError("Bundle manifest has a malformed attachment list.")
    seen_attachment_slots: dict[tuple[str, int, str], str] = {}
    for attachment in manifest.get("attachments", []):
        if not isinstance(attachment, dict):
            raise RecoveryError("Malformed attachment record in bundle manifest.")
        relative = attachment.get("relative_path")
        expected = manifest_sha256(attachment.get("sha256"), "an attachment")
        expected_bytes = manifest_bytes(attachment.get("bytes"), "an attachment")
        references_by_thread = attachment.get("references_by_thread")
        if not isinstance(relative, str):
            raise RecoveryError("Attachment record is missing a path.")
        attachment_relative_path(relative)
        if not isinstance(references_by_thread, dict) or not references_by_thread:
            raise RecoveryError("Bundle attachment has no task-scoped source-reference metadata.")
        for owner_id, references in references_by_thread.items():
            if (
                not isinstance(owner_id, str)
                or not UUID_RE.fullmatch(owner_id)
                or owner_id != owner_id.lower()
                or owner_id not in seen_thread_ids
                or not isinstance(references, list)
                or not references
            ):
                raise RecoveryError("Bundle attachment has malformed task-scoped source-reference metadata.")
            locations = [manifest_attachment_location(reference) for reference in references]
            if locations != sorted(locations):
                raise RecoveryError("Bundle attachment task-scoped locations must be unique and sorted.")
            for line, pointer, source_reference in locations:
                actual_slot = session_attachment_slots_by_thread[owner_id].get((line, pointer))
                if (
                    actual_slot is None
                    or actual_slot.source_reference != source_reference
                    or not attachment_reference_matches_path(source_reference, relative)
                ):
                    raise RecoveryError(
                        "Bundle attachment location does not match its declared selected task attachment path."
                    )
                slot_key = (owner_id, line, pointer)
                previous_relative = seen_attachment_slots.get(slot_key)
                if previous_relative is not None:
                    raise RecoveryError(
                        "One task attachment location is assigned to multiple bundle attachments."
                    )
                seen_attachment_slots[slot_key] = relative
        if relative in seen_paths:
            raise RecoveryError("Bundle manifest contains duplicate or conflicting file paths.")
        seen_paths.add(relative)
        path = safe_bundle_path(bundle, relative)
        if path.is_symlink():
            raise RecoveryError(f"Bundle attachment may not be a symlink: {relative}")
        if not path.is_file():
            raise RecoveryError(f"Bundle integrity check failed for {relative}")
        require_regular_unlinked_file(path, "bundle attachment")
        if sha256_file(path) != expected or path.stat().st_size != expected_bytes:
            raise RecoveryError(f"Bundle integrity check failed for {relative}")
        allowed_files.add(relative)
    allowed_files.update(validate_manifest_index(bundle, manifest, seen_thread_ids))
    validate_bundle_layout(bundle, allowed_files)
    return {"operation": "verify", "bundle": str(bundle), "verified_thread_files": checked, "valid": True}


def command_verify(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle).expanduser().resolve()
    report = validate_bundle(bundle, load_manifest(bundle))
    print_report(report, args.json)
    return 0


def select_manifest_threads(manifest: dict[str, Any], requested: list[str] | None, select_all: bool) -> list[dict[str, Any]]:
    if not requested and not select_all:
        raise RecoveryError("Select one or more --thread IDs, or pass --all-sessions explicitly.")
    by_id = {
        item.get("id"): item
        for item in manifest["threads"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    wanted = {item.lower() for item in requested or []}
    missing = sorted(wanted - set(by_id))
    if missing:
        raise RecoveryError("Requested bundle session IDs were not found: " + ", ".join(missing))
    return list(by_id.values()) if select_all else [by_id[thread_id] for thread_id in sorted(wanted)]


def selected_attachments(attachments: list[dict[str, Any]], selected_ids: set[str]) -> list[dict[str, Any]]:
    """Keep only attachments that have a verified reference in a selected task."""
    return [
        attachment
        for attachment in attachments
        if selected_ids & set(attachment["references_by_thread"])
    ]


def attachment_replacements(
    attachments: list[dict[str, Any]], thread_id: str, target: Path
) -> dict[tuple[int, str], tuple[str, str]]:
    """Map only this task's verified attachment locations to target paths."""
    replacements: dict[tuple[int, str], tuple[str, str]] = {}
    for attachment in attachments:
        relative = attachment["relative_path"]
        destination = str(safe_bundle_path(target, relative))
        for location in attachment["references_by_thread"].get(thread_id, []):
            line, pointer, source_reference = manifest_attachment_location(location)
            key = (line, pointer)
            previous = replacements.get(key)
            if previous is not None and previous != (destination, source_reference):
                raise RecoveryError(
                    "One task-scoped source attachment location maps to multiple destination files; refusing to guess."
                )
            replacements[key] = (destination, source_reference)
    return replacements


def unique_backup_path(path: Path) -> Path:
    base = path.with_name(f"{path.name}.pre-codex-account-recovery-{timestamp_label()}.bak")
    candidate = base
    counter = 1
    while candidate.exists():
        candidate = base.with_name(base.name + f".{counter}")
        counter += 1
    return candidate


def command_restore(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle).expanduser().resolve()
    manifest = load_manifest(bundle)
    validate_bundle(bundle, manifest)
    selected = select_manifest_threads(manifest, args.thread, args.all_sessions)
    target = Path(args.target).expanduser().resolve()
    if not target.is_dir():
        raise RecoveryError(f"Target CODEX_HOME does not exist: {target}")

    target_scan = scan_context(target)
    if (
        target_scan["recovery_data_entries"]
        or target_scan["index_entries"]
        or target_scan["index_invalid_lines"]
        or target_scan["duplicate_index_ids"]
    ):
        raise RecoveryError(
            "Automatic restore deliberately requires an empty, clean target CODEX_HOME conversation store. "
            "Do not merge conversation files or attachments into an existing account's local state. "
            "For an account switch on the same machine, keep the existing home and use inventory/provider-alias instead."
        )
    existing_ids = set(target_scan["thread_ids"]) | set(target_scan["index_entries"])
    collisions = sorted({thread["id"] for thread in selected} & existing_ids)
    if collisions:
        raise RecoveryError("Target already contains selected session IDs; refusing to overwrite: " + ", ".join(collisions))

    source_provider_ids = {thread.get("model_provider") for thread in selected if thread.get("model_provider")}
    available_providers = configured_provider_ids(target / "config.toml")
    missing_providers = sorted(source_provider_ids - available_providers)
    if missing_providers:
        raise RecoveryError(
            "Target config lacks legacy provider IDs: "
            + ", ".join(missing_providers)
            + ". Confirm a mapping and run provider-alias before restore."
        )

    selected_ids = {thread["id"] for thread in selected}
    selected_index_lines: list[str] = []
    source_index = bundle / "session_index.jsonl"
    if source_index.is_file():
        with source_index.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                try:
                    entry = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise RecoveryError("Bundle session_index.jsonl is invalid.") from exc
                thread_id = entry.get("id") if isinstance(entry, dict) else None
                if isinstance(thread_id, str) and thread_id.lower() in selected_ids:
                    selected_index_lines.append(raw_line if raw_line.endswith("\n") else raw_line + "\n")
    all_attachments = manifest.get("attachments", [])
    attachments = selected_attachments(all_attachments, selected_ids) if args.include_attachments else []
    attachment_replacements_by_thread = {
        thread["id"]: attachment_replacements(attachments, thread["id"], target) for thread in selected
    }
    attachment_collisions: list[str] = []
    for attachment in attachments:
        relative = attachment["relative_path"]
        destination = safe_bundle_path(target, relative)
        if destination.exists() or destination.is_symlink():
            attachment_collisions.append(relative)
    if attachment_collisions:
        raise RecoveryError("Target attachment paths conflict; refusing to overwrite: " + ", ".join(attachment_collisions))

    plan = {
        "operation": "restore",
        "bundle": str(bundle),
        "target": str(target),
        "thread_ids": sorted(selected_ids),
        "session_files": len(selected),
        "attachment_files": len(attachments),
        "attachment_reference_paths_to_remap": sum(
            len(replacements) for replacements in attachment_replacements_by_thread.values()
        ),
        "missing_target_provider_ids": missing_providers,
        "will_write": bool(args.apply),
    }
    if not args.apply:
        print_report(plan, args.json)
        print("Dry run only. Close Codex Desktop, then re-run with --i-confirm-codex-is-closed --apply.")
        return 0
    if not args.i_confirm_codex_is_closed:
        raise RecoveryError("Refusing to write while Codex may be running; pass --i-confirm-codex-is-closed after closing it.")

    target_index = target / "session_index.jsonl"
    index_original = target_index.read_bytes() if target_index.exists() else None
    index_original_mode = stat.S_IMODE(target_index.stat().st_mode) if target_index.exists() else None
    index_backup_path: Path | None = None
    created_paths: list[Path] = []
    rewritten_attachment_references = 0
    index_mutation_started = False
    try:
        ensure_private_directory(target, "target CODEX_HOME")
        if selected_index_lines and index_original is not None:
            index_backup_path = unique_backup_path(target_index)
            copy_file(target_index, index_backup_path)
        for thread in selected:
            relative = thread["relative_path"]
            source_file = safe_bundle_path(bundle, relative)
            destination = safe_bundle_path(target, relative)
            if destination.exists():
                raise RecoveryError(f"Unexpected session collision during restore: {relative}")
            ensure_private_destination_parent(target, destination, "target CODEX_HOME")
            rewritten_attachment_references += copy_session_with_attachment_remap(
                source_file, destination, attachment_replacements_by_thread[thread["id"]]
            )
            created_paths.append(destination)
        for attachment in attachments:
            relative = attachment["relative_path"]
            source_file = safe_bundle_path(bundle, relative)
            destination = safe_bundle_path(target, relative)
            if destination.exists():
                raise RecoveryError(f"Unexpected attachment collision during restore: {relative}")
            ensure_private_destination_parent(target, destination, "target CODEX_HOME")
            copy_file(source_file, destination)
            created_paths.append(destination)

        if selected_index_lines:
            existing_text = target_index.read_text(encoding="utf-8", errors="replace") if target_index.exists() else ""
            index_mutation_started = True
            write_text_atomic(target_index, existing_text + "".join(selected_index_lines), mode=0o600)
    except BaseException:
        rollback_error: BaseException | None = None
        if index_mutation_started:
            try:
                if index_original is None:
                    target_index.unlink(missing_ok=True)
                else:
                    write_bytes_atomic(target_index, index_original, mode=index_original_mode)
            except BaseException as exc:
                rollback_error = exc
        for created in reversed(created_paths):
            try:
                created.unlink()
            except FileNotFoundError:
                pass
            except BaseException as exc:
                rollback_error = rollback_error or exc
        if rollback_error is None and index_backup_path is not None:
            try:
                index_backup_path.unlink()
            except FileNotFoundError:
                pass
            except BaseException as exc:
                rollback_error = exc
        if rollback_error is not None:
            raise RecoveryError(
                "Restore failed and automatic rollback was incomplete; leave Codex closed and inspect the target manually."
            ) from rollback_error
        raise

    plan["session_index_backup"] = str(index_backup_path) if index_backup_path is not None else None
    plan["rewritten_attachment_references"] = rewritten_attachment_references
    plan["next_step"] = "Run `codex migrate-rollouts --thread <TASK_ID> --json`; use --apply only after approval."
    print_report(plan, args.json)
    return 0


def toml_literal(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    raise RecoveryError(f"Unsupported non-secret provider setting type: {type(value).__name__}")


def provider_alias_block(
    legacy: str,
    active: str,
    active_table: dict[str, Any],
    *,
    allow_implicit_openai_url: bool = False,
) -> str:
    forbidden = [field for field in SENSITIVE_PROVIDER_FIELDS if field in active_table]
    if forbidden:
        raise RecoveryError(
            f"Active provider {active!r} depends on credential or header fields ({', '.join(forbidden)}); "
            "configure the legacy provider manually instead of copying a partial alias."
        )
    if active_table.get("requires_openai_auth") is not True:
        raise RecoveryError(
            f"Active provider {active!r} is not explicitly configured to use the target OpenAI login; "
            "configure the legacy provider manually instead of copying a partial alias."
        )
    copied: dict[str, Any] = {}
    active_name = active_table.get("name")
    copied["name"] = f"{active_name if isinstance(active_name, str) else active} (legacy {legacy} tasks)"
    for field in ALIAS_FIELDS:
        value = active_table.get(field)
        if isinstance(value, (str, bool, int, float)):
            copied[field] = value
    if "base_url" not in copied and not allow_implicit_openai_url:
        raise RecoveryError(f"Active provider {active!r} has no base_url; refusing to create an alias.")
    if "base_url" in copied:
        parsed_url = urlparse(copied["base_url"])
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
            or parsed_url.username
            or parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise RecoveryError(
                "Active provider base_url includes unsupported credentials, query data, or an invalid scheme; "
                "configure the legacy provider manually instead."
            )
    lines = [f"# Compatibility alias for legacy local tasks that record provider {legacy!r}.", f"[model_providers.{legacy}]"]
    for key, value in copied.items():
        lines.append(f"{key} = {toml_literal(value)}")
    return "\n".join(lines) + "\n"


def command_provider_alias(args: argparse.Namespace) -> int:
    config = Path(args.config).expanduser().resolve()
    legacy = args.legacy_provider
    active = args.active_provider
    if not PROVIDER_ID_RE.fullmatch(legacy) or not PROVIDER_ID_RE.fullmatch(active):
        raise RecoveryError("Provider IDs may contain only letters, digits, underscores, and hyphens.")
    if legacy == active:
        raise RecoveryError("Legacy and active provider IDs must be different.")
    if legacy in BUILTIN_PROVIDERS:
        raise RecoveryError(f"{legacy!r} is a reserved built-in provider ID and cannot be aliased.")
    if not config.is_file():
        raise RecoveryError(f"Target config.toml does not exist: {config}")
    parsed = load_toml(config)
    providers = parsed.get("model_providers", {})
    if not isinstance(providers, dict):
        raise RecoveryError("model_providers is not a TOML table.")
    if legacy in providers:
        raise RecoveryError(f"Provider {legacy!r} already exists; refusing to alter it.")
    active_provider_kind = "configured"
    allow_implicit_openai_url = False
    if active == "openai":
        active_provider_kind = "built-in"
        allow_implicit_openai_url = True
        active_table = dict(BUILTIN_OPENAI_ALIAS)
    elif active in BUILTIN_PROVIDERS:
        raise RecoveryError(
            f"Built-in provider {active!r} does not use the target OpenAI login and cannot back a legacy alias."
        )
    else:
        active_table = providers.get(active)
        if not isinstance(active_table, dict):
            raise RecoveryError(f"Active target provider {active!r} was not found in {config}.")
    block = provider_alias_block(
        legacy,
        active,
        active_table,
        allow_implicit_openai_url=allow_implicit_openai_url,
    )
    report = {
        "operation": "provider-alias",
        "config": str(config),
        "legacy_provider": legacy,
        "active_provider": active,
        "active_provider_kind": active_provider_kind,
        "will_write": bool(args.apply),
        "copied_fields": [line.split(" =", 1)[0] for line in block.splitlines() if " =" in line],
    }
    if not args.apply:
        print_report(report, args.json)
        print("Dry run only. Confirm the mapping, then re-run with --apply.")
        return 0
    backup = unique_backup_path(config)
    shutil.copy2(config, backup)
    original = config.read_text(encoding="utf-8")
    if not original.endswith("\n"):
        original += "\n"
    write_text_atomic(config, original + "\n" + block)
    report["backup"] = str(backup)
    print_report(report, args.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory", help="Read-only local session and provider preflight")
    inventory.add_argument("--source", required=True, help="Source CODEX_HOME, live-context, or recovery bundle")
    inventory.add_argument("--target", help="Optional target CODEX_HOME for provider compatibility")
    inventory.add_argument("--json", action="store_true", help="Emit a machine-readable report")
    inventory.set_defaults(func=command_inventory)

    bundle = subparsers.add_parser("bundle", help="Create a selective, credential-free recovery bundle")
    bundle.add_argument("--source", required=True, help="Source CODEX_HOME or live-context")
    bundle.add_argument("--output", required=True, help="New bundle directory; it must not exist")
    bundle.add_argument("--thread", action="append", help="Task/session UUID to include; repeatable")
    bundle.add_argument("--all-sessions", action="store_true", help="Explicitly select every eligible session")
    bundle.add_argument("--include-archived", action="store_true", help="Allow archived session files to be selected")
    bundle.add_argument("--include-attachments", action="store_true", help="Copy only attachments referenced by selected sessions")
    bundle.add_argument("--apply", action="store_true", help="Create the new bundle directory")
    bundle.add_argument("--json", action="store_true", help="Emit a machine-readable report")
    bundle.set_defaults(func=command_bundle)

    verify = subparsers.add_parser("verify", help="Read-only integrity validation for a recovery bundle")
    verify.add_argument("--bundle", required=True, help="Recovery bundle directory")
    verify.add_argument("--json", action="store_true", help="Emit a machine-readable report")
    verify.set_defaults(func=command_verify)

    restore = subparsers.add_parser("restore", help="Copy selected bundle files into a stopped target CODEX_HOME")
    restore.add_argument("--bundle", required=True, help="Validated recovery bundle directory")
    restore.add_argument("--target", required=True, help="Existing target CODEX_HOME")
    restore.add_argument("--thread", action="append", help="Task/session UUID to restore; repeatable")
    restore.add_argument("--all-sessions", action="store_true", help="Explicitly restore every session in the bundle")
    restore.add_argument("--include-attachments", action="store_true", help="Restore packaged attachments too")
    restore.add_argument("--i-confirm-codex-is-closed", action="store_true", help="Required with --apply")
    restore.add_argument("--apply", action="store_true", help="Copy files after all collision/preflight checks pass")
    restore.add_argument("--json", action="store_true", help="Emit a machine-readable report")
    restore.set_defaults(func=command_restore)

    alias = subparsers.add_parser("provider-alias", help="Add a narrow non-secret legacy provider alias")
    alias.add_argument("--config", required=True, help="Target user-level config.toml")
    alias.add_argument("--legacy-provider", required=True, help="Provider ID recorded by legacy session(s)")
    alias.add_argument(
        "--active-provider",
        required=True,
        help="Existing compatible provider in target config, or the built-in openai provider",
    )
    alias.add_argument("--apply", action="store_true", help="Back up and append the compatibility alias")
    alias.add_argument("--json", action="store_true", help="Emit a machine-readable report")
    alias.set_defaults(func=command_provider_alias)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except RecoveryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
