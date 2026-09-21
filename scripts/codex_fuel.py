#!/usr/bin/env python3
"""Read-only "fuel" check for Codex: route, credential, and history tags.

In a few seconds, without printing a secret or any conversation text, it shows:
  * which CODEX_HOME, codex binaries, provider, model and endpoint are in effect
  * where the credential lives, whether auth.json parses, and the last 5 chars
    of the stored key (so you can confirm a swap really took effect)
  * which provider IDs the saved tasks carry and whether each still resolves
  * with --probe: whether the provider accepts the stored key (GET /models,
    which costs no tokens)

`check` (the default) is read-only. `set-key` is the only command that writes: it
reads a new API key from a hidden prompt or stdin, strips BOMs and whitespace,
backs up auth.json, writes the key through `codex login --with-api-key`, and
re-checks the result. It exists because piping a key from PowerShell prepends an
invisible U+FEFF to it: the login "succeeds" and every request then fails.

Standard library only, Python 3.8+. To inspect an SSH host that owns its own
CODEX_HOME, no install is needed:

    ssh HOST python3 - --json < scripts/codex_fuel.py
"""
from __future__ import annotations

import argparse
import getpass
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BUILTIN = {"openai", "ollama", "lmstudio"}
AUTH_EXTRAS = ("experimental_bearer_token", "http_headers", "env_http_headers", "query_params", "auth")
ENV_NAMES = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY", "CODEX_HOME")


def resolve_home(arg):
    return Path(arg or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def fingerprint(key):
    key = key.strip()
    return {"prefix": key[:3], "tail": key[-5:], "length": len(key)}


def redact(text):
    return re.sub(r"sk-[A-Za-z0-9_\-*]{6,}", "sk-***", text)


def age(ts):
    seconds = max(0, int(time.time() - ts))
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return f"{seconds // size} {unit} ago"
    return f"{seconds} s ago"


# --- config.toml ------------------------------------------------------------

_KV = re.compile(r'^\s*([A-Za-z0-9_.\-"]+)\s*=\s*(.+?)\s*$')


def _scalar(value):
    value = value.strip()
    if value[:1] in ("'", '"'):
        end = value.find(value[0], 1)
        return value[1:end] if end > 0 else value[1:]
    if value in ("true", "false"):
        return value == "true"
    try:
        return int(value)
    except ValueError:
        return value


def mini_toml(text):
    """Tiny fallback for Python < 3.11: scalars and [a.b] tables only."""
    data, cur = {}, None
    cur = data
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        header = re.match(r"^\[([^\[\]]+)\]$", s)
        if header:
            cur = data
            for quoted_d, quoted_s, bare in re.findall(r'"([^"]+)"|\'([^\']+)\'|([^.\s]+)', header.group(1)):
                cur = cur.setdefault(quoted_d or quoted_s or bare, {})
            continue
        kv = _KV.match(s)
        if kv and not s.startswith("["):
            cur[kv.group(1).strip('"')] = _scalar(kv.group(2))
    return data


def load_config(path):
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}, "missing"
    except OSError as exc:
        return {}, str(exc)
    try:
        import tomllib
    except ImportError:
        return mini_toml(text), None
    try:
        return tomllib.loads(text), None
    except tomllib.TOMLDecodeError as exc:
        return {}, f"invalid TOML: {exc}"


def inspect_config(home):
    path = home / "config.toml"
    data, error = load_config(path)
    providers = {}
    for pid, table in (data.get("model_providers") or {}).items():
        if isinstance(table, dict):
            providers[pid] = {
                "name": table.get("name"),
                "base_url": table.get("base_url"),
                "wire_api": table.get("wire_api"),
                "requires_openai_auth": table.get("requires_openai_auth"),
                "env_key": table.get("env_key"),
                "custom_auth_keys": [k for k in AUTH_EXTRAS if k in table],
            }
    active_id = data.get("model_provider") or "openai"
    if active_id in providers and active_id not in BUILTIN:
        kind, endpoint = "custom", providers[active_id]["base_url"]
    elif active_id == "openai":
        kind, endpoint = "builtin", data.get("openai_base_url") or "(built-in default)"
    elif active_id in BUILTIN:
        kind, endpoint = "builtin", "(built-in default)"
    else:
        kind, endpoint = "undefined", None
    return {
        "path": str(path),
        "error": error,
        "active_provider": active_id,
        "active_kind": kind,
        "endpoint": endpoint,
        "model": data.get("model"),
        "openai_base_url": data.get("openai_base_url"),
        "disable_response_storage": data.get("disable_response_storage"),
        "auth_store": data.get("cli_auth_credentials_store"),
        "providers": providers,
        "overrides_builtin": sorted(set(providers) & BUILTIN),
    }


# --- auth.json --------------------------------------------------------------

def inspect_auth(home):
    path = home / "auth.json"
    info = {"path": str(path), "exists": path.exists(), "problems": []}
    if not info["exists"]:
        return info
    raw = path.read_bytes()
    stat = path.stat()
    info.update(size=stat.st_size, modified=age(stat.st_mtime))
    text = raw.decode("utf-8-sig", errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        info["problems"].append("file starts with a UTF-8 BOM (PowerShell Set-Content/Out-File -Encoding utf8 adds one)")
    if re.search(r"^\s*(//|/\*|#)", text, re.M):
        info["problems"].append("contains comment lines, but JSON has no comments")
    try:
        data = json.loads(text)
    except ValueError as exc:
        info.update(valid=False)
        info["problems"].append(f"not valid JSON: {exc}")
        return info
    if not isinstance(data, dict):
        info.update(valid=False)
        info["problems"].append("top level is not a JSON object")
        return info
    key = data.get("OPENAI_API_KEY")
    has_key = isinstance(key, str) and key.strip() != ""
    info.update(
        valid=True,
        fields=sorted(data),
        auth_mode=data.get("auth_mode"),
        has_chatgpt_tokens="tokens" in data,
        key=fingerprint(key) if has_key else None,
        key_clean=(key.isascii() and key.isprintable() and key == key.strip()) if has_key else None,
    )
    return info


# --- environment, binaries, doctor, history tags ----------------------------

def inspect_env():
    return {name: name in os.environ for name in ENV_NAMES}


def find_codex(explicit=None):
    candidates = [explicit] if explicit else []
    which = shutil.which("codex")
    if which:
        candidates.append(which)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates += sorted(glob.glob(os.path.join(local, "OpenAI", "Codex", "bin", "*", "codex.exe")))
    seen, found = set(), []
    for cand in candidates:
        norm = os.path.normcase(os.path.realpath(cand))
        if norm in seen:
            continue
        seen.add(norm)
        try:
            out = subprocess.run([cand, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            out = "?"
        found.append({"path": cand, "version": out})
    return found


def newest(binaries):
    def rank(item):
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", item["version"])
        return tuple(int(x) for x in match.groups()) if match else (0, 0, 0)
    return max(binaries, key=rank)


def collect_checks(node, out):
    if isinstance(node, dict):
        if isinstance(node.get("id"), str) and "status" in node:
            out[node["id"]] = node
        for value in node.values():
            collect_checks(value, out)
    elif isinstance(node, list):
        for value in node:
            collect_checks(value, out)


def run_doctor(codex, home):
    env = dict(os.environ, CODEX_HOME=str(home))
    try:
        proc = subprocess.run([codex, "doctor", "--json"], capture_output=True, text=True, timeout=120, env=env)
        data = json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": str(exc)}
    except ValueError:
        return {"error": "codex doctor did not return JSON (older CLI?)"}
    checks = {}
    collect_checks(data, checks)
    auth = (checks.get("auth.credentials") or {}).get("details", {})
    reach = checks.get("network.provider_reachability") or {}
    parity = (checks.get("state.rollout_db_parity") or {}).get("details", {})
    tags = {}
    for name, count in re.findall(r"(\S+?)=(\d+)", str(parity.get("rollout DB model providers", ""))):
        tags[name] = int(count)
    return {
        "config_load": (checks.get("config.load") or {}).get("status"),
        "auth_storage": auth.get("auth storage mode"),
        "stored_api_key": auth.get("stored API key"),
        "stored_chatgpt_tokens": auth.get("stored ChatGPT tokens"),
        "reachability": reach.get("status"),
        "reachability_mode": (reach.get("details") or {}).get("reachability mode"),
        "tags": tags,
    }


def sqlite_tags(home):
    best = None
    for db in home.glob("state_*.sqlite"):
        match = re.fullmatch(r"state_(\d+)\.sqlite", db.name)
        if match and (best is None or int(match.group(1)) > best[0]):
            best = (int(match.group(1)), db)
    if best is None:
        return None
    try:
        con = sqlite3.connect(best[1].resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute("SELECT model_provider, COUNT(*) FROM threads GROUP BY 1").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return {str(name or "(none)"): count for name, count in rows}


def probe_models(base_url, key, timeout=20):
    url = base_url.rstrip("/") + "/models"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": "curl/8.5.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            status, body = resp.status, resp.read(2_000_000)
    except urllib.error.HTTPError as exc:
        exc.close()
        return {"url": url, "status": exc.code}
    except (urllib.error.URLError, OSError) as exc:
        return {"url": url, "error": redact(str(getattr(exc, "reason", exc)))}
    models = []
    try:
        models = [m.get("id") for m in json.loads(body).get("data", []) if isinstance(m, dict)]
    except (ValueError, AttributeError):
        pass
    return {"url": url, "status": status, "models": models}


def read_probe_key(home, config, auth):
    active = config["providers"].get(config["active_provider"]) or {}
    env_key = active.get("env_key")
    if env_key:
        return os.environ.get(env_key)
    try:
        data = json.loads((home / "auth.json").read_text(encoding="utf-8-sig"))
        key = data.get("OPENAI_API_KEY")
        return key.strip() if isinstance(key, str) else None
    except (OSError, ValueError):
        return None


# --- analysis ---------------------------------------------------------------

def analyze(report, expect_tail=None):
    findings = []

    def add(level, message, fix=None):
        findings.append({"level": level, "message": message, "fix": fix})

    cfg, auth, env = report["config"], report["auth"], report["env"]
    active = cfg["providers"].get(cfg["active_provider"]) or {}

    if cfg["error"] and cfg["error"] != "missing":
        add("error", f"config.toml cannot be loaded: {cfg['error']}",
            "Fix the syntax or restore the newest config.toml.bak-*. Codex silently falls back to ChatGPT defaults when the config does not load.")
    if cfg["overrides_builtin"]:
        names = ", ".join(cfg["overrides_builtin"])
        add("error", f"config.toml defines [model_providers.{names}], a reserved built-in provider ID; Codex refuses to load such a config",
            "Delete that table. To send the built-in openai provider through a relay use the top-level key openai_base_url = \"https://RELAY/v1\".")
    if cfg["active_kind"] == "undefined":
        add("error", f"model_provider = \"{cfg['active_provider']}\" has no [model_providers.{cfg['active_provider']}] table",
            "Add the table or point model_provider at an existing one.")

    if active.get("env_key"):
        var = active["env_key"]
        if not os.environ.get(var):
            add("warn", f"active provider reads its key from env var {var}, which is not set in this shell",
                "Export it in the shell/service that launches Codex; the desktop app may not inherit your terminal's environment.")
    elif not auth["exists"]:
        add("warn", "no auth.json: Codex is not logged in", "python scripts/codex_fuel.py set-key")
    elif auth.get("valid") is False:
        add("error", "auth.json is unusable: " + "; ".join(auth["problems"]),
            "Restore auth.json.bak-*, or rewrite it with: python scripts/codex_fuel.py set-key")
    elif auth["problems"]:
        add("warn", "auth.json: " + "; ".join(auth["problems"]), "Rewrite it with: python scripts/codex_fuel.py set-key (clean UTF-8, no BOM).")
    if auth.get("key_clean") is False:
        add("error", "the stored key contains invisible or non-ASCII characters (typically U+FEFF from a key piped in through PowerShell), so every request will fail with 401",
            "python scripts/codex_fuel.py set-key")

    if expect_tail:
        stored = (auth.get("key") or {}).get("tail")
        if stored is None:
            add("error", f"expected a stored key ending {expect_tail[-5:]}, but auth.json holds no API key")
        elif stored != expect_tail[-5:]:
            add("error", f"auth.json key ends {stored}, expected {expect_tail[-5:]} (last written {auth.get('modified')})",
                "The swap did not apply or something overwrote it (editor autosave, sync, another login). Re-run the login, then re-check.")

    if cfg["auth_store"] in ("keyring", "auto"):
        add("warn", f"cli_auth_credentials_store = \"{cfg['auth_store']}\": the key may live in the OS keyring, so editing auth.json may not change what Codex uses",
            "Prefer `codex login --with-api-key`, then confirm with `codex login status`.")

    if env["OPENAI_API_KEY"] or env["CODEX_API_KEY"]:
        add("warn", "an API key is exported in the environment; a stale exported key can shadow auth.json",
            "Confirm it matches the key you intend to use, or unset it.")
    if env["OPENAI_BASE_URL"]:
        add("warn", "OPENAI_BASE_URL is set in the environment and may redirect the built-in openai provider", "Unset it or make it match.")
    if env["CODEX_HOME"]:
        add("info", "CODEX_HOME is set in the environment, so this is not necessarily ~/.codex")

    tags = report["tags"]["counts"]
    for tag, count in sorted(tags.items(), key=lambda item: -item[1]):
        if tag in cfg["providers"] and tag not in BUILTIN:
            continue
        if tag == "openai" and cfg["active_provider"] != "openai":
            if not cfg["openai_base_url"] and cfg["endpoint"]:
                add("warn", f"{count} tasks carry the built-in provider ID 'openai', which routes to the official endpoint rather than the active provider",
                    f"To send them through the same endpoint add: openai_base_url = \"{cfg['endpoint']}\" (top-level). Test by opening one task; do not send a prompt just to test.")
            continue
        if tag in BUILTIN:
            continue
        target = cfg["active_provider"]
        add("error", f"{count} tasks carry provider ID '{tag}', which config.toml does not define, so they cannot resume",
            f"python scripts/codex_account_recovery.py provider-alias --config \"{cfg['path']}\" --legacy-provider {tag} --active-provider {target} --apply --json")

    versions = {v["version"] for v in report["codex"] if v["version"] not in ("", "?")}
    if len(versions) > 1:
        add("info", "several codex binaries with different versions are installed: " + ", ".join(sorted(versions)),
            "They share CODEX_HOME, but a flag or subcommand may exist in only one; check --help of the binary you actually run.")

    doctor = report["doctor"] or {}
    if doctor.get("config_load") == "fail":
        add("error", "codex doctor reports the config could not be loaded")
    probe = report["probe"]
    if probe:
        status = probe.get("status")
        if status == 200:
            pass
        elif status in (401, 403):
            add("error", f"the provider answered HTTP {status} to the stored key", "The key is invalid, expired, out of quota, or not valid for this relay.")
        elif status == 404:
            add("warn", "the endpoint has no /models route, so the key could not be verified this way")
        elif status:
            add("warn", f"GET /models returned HTTP {status}")
        else:
            add("warn", f"could not reach the endpoint: {probe.get('error')}")

    if report["secrets"]:
        add("info", "other credential-like files exist in CODEX_HOME/secrets: " + ", ".join(report["secrets"]),
            "When rotating a key, check whether one of these holds a stale copy.")
    return findings


# --- report -----------------------------------------------------------------

def build_report(args):
    home = resolve_home(args.home)
    config = inspect_config(home)
    auth = inspect_auth(home)
    codex = find_codex(args.codex)
    doctor = run_doctor(newest(codex)["path"], home) if args.doctor and codex else None
    counts = sqlite_tags(home)
    source = "sqlite"
    if counts is None and doctor and doctor.get("tags"):
        counts, source = doctor["tags"], "doctor"
    if counts is None:
        counts, source = {}, "none"
    probe = None
    if args.probe:
        base = config["endpoint"] if config["endpoint"] and config["endpoint"].startswith("http") else "https://api.openai.com/v1"
        key = read_probe_key(home, config, auth)
        probe = probe_models(base, key) if key else {"url": base, "error": "no API key available to probe with"}
    secrets_dir = home / "secrets"
    secrets = sorted(p.name for p in secrets_dir.iterdir() if p.is_file()) if secrets_dir.is_dir() else []
    report = {
        "home": str(home), "codex": codex, "config": config, "auth": auth, "env": inspect_env(),
        "doctor": doctor, "tags": {"source": source, "counts": counts}, "probe": probe, "secrets": secrets,
    }
    report["findings"] = analyze(report, args.expect_tail)
    return report


def render(report):
    cfg, auth, doctor, probe = report["config"], report["auth"], report["doctor"], report["probe"]
    lines = [f"CODEX_HOME   {report['home']}"]
    if report["codex"]:
        lines.append("codex CLI    " + "; ".join(f"{c['version']} ({c['path']})" for c in report["codex"]))
    else:
        lines.append("codex CLI    not found on PATH")
    lines.append(f"Route        provider={cfg['active_provider']} ({cfg['active_kind']})  model={cfg['model']}")
    lines.append(f"             endpoint={cfg['endpoint']}  disable_response_storage={cfg['disable_response_storage']}")
    if auth["exists"]:
        key = auth.get("key")
        shown = f"{key['prefix']}...{key['tail']} (length {key['length']})" if key else "none"
        lines.append(f"Credential   auth.json mode={auth.get('auth_mode')} key={shown} chatgpt_tokens={auth.get('has_chatgpt_tokens')} modified {auth.get('modified')}")
    else:
        lines.append("Credential   no auth.json")
    counts = report["tags"]["counts"]
    lines.append("History      " + ("  ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda i: -i[1])) or "no tagged tasks found")
                 + f"   (source: {report['tags']['source']})")
    if doctor:
        lines.append("Doctor       " + ("  ".join(f"{k}={v}" for k, v in doctor.items() if k != "tags") if "error" not in doctor else doctor["error"]))
    if probe:
        if probe.get("status") == 200:
            models = [m for m in probe.get("models", []) if m]
            lines.append(f"Probe        GET {probe['url']} -> 200, {len(models)} models: {', '.join(models[:8])}")
        else:
            lines.append(f"Probe        GET {probe['url']} -> {probe.get('status') or probe.get('error')}")
    lines.append("")
    if not report["findings"]:
        lines.append("Findings     none")
    for item in report["findings"]:
        lines.append(f"[{item['level'].upper()}] {item['message']}")
        if item["fix"]:
            lines.append(f"        fix: {item['fix']}")
    return "\n".join(lines)


# --- set-key ----------------------------------------------------------------

def clean_key(raw):
    """Decode a key that may carry a BOM, a UTF-16 encoding, whitespace, or a newline."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    text = text.replace("﻿", "").replace("\x00", "").strip()
    if len(text) < 16 or not (text.isascii() and text.isprintable()) or any(c.isspace() for c in text):
        raise ValueError("that does not look like an API key (expected one printable ASCII token of at least 16 characters)")
    return text


def write_auth_directly(home, key):
    home.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": key}, indent=2) + "\n"
    (home / "auth.json").write_bytes(payload.encode("ascii"))


def set_key(args):
    home = resolve_home(args.home)
    try:
        if args.key_stdin or not sys.stdin.isatty():
            key = clean_key(sys.stdin.buffer.read())
        else:
            key = clean_key(getpass.getpass("New API key (input hidden): ").encode("utf-8"))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    auth_path = home / "auth.json"
    if auth_path.exists():
        backup = auth_path.with_name("auth.json.bak-" + time.strftime("%Y%m%d%H%M%S"))
        shutil.copy2(auth_path, backup)
        print(f"backed up {auth_path.name} -> {backup.name}")
    codex = find_codex(args.codex)
    wrote_with = "direct write (no codex binary found)"
    if codex:
        best = newest(codex)
        env = dict(os.environ, CODEX_HOME=str(home))
        proc = subprocess.run([best["path"], "login", "--with-api-key"], input=(key + "\n").encode("ascii"),
                              capture_output=True, env=env, timeout=120)
        if proc.returncode == 0:
            wrote_with = f"{best['version']} login --with-api-key"
        else:
            print("codex login failed (" + redact(proc.stderr.decode("utf-8", "replace"))[:200].strip() + "); writing auth.json directly", file=sys.stderr)
            write_auth_directly(home, key)
    else:
        write_auth_directly(home, key)
    print(f"wrote key ending {key[-5:]} via {wrote_with}")
    args.expect_tail, args.probe, args.doctor = key[-5:], True, False
    report = build_report(args)
    print(render(report))
    return 1 if any(f["level"] == "error" for f in report["findings"]) else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Codex route/credential/history check (read-only), or set-key to swap the API key safely.")
    parser.add_argument("command", nargs="?", choices=("check", "set-key"), default="check")
    parser.add_argument("--home", help="CODEX_HOME to use (default: $CODEX_HOME or ~/.codex)")
    parser.add_argument("--codex", help="codex binary to use (default: newest found)")
    parser.add_argument("--expect-tail", help="check: last chars of the key you just installed; reports a mismatch")
    parser.add_argument("--probe", action="store_true", help="check: GET <endpoint>/models with the stored key (no tokens spent)")
    parser.add_argument("--doctor", action="store_true", help="check: also run `codex doctor --json` (3-30 s; adds Codex's own config/auth verdict)")
    parser.add_argument("--json", action="store_true", help="check: machine-readable output")
    parser.add_argument("--key-stdin", action="store_true", help="set-key: read the key from stdin instead of a hidden prompt (automatic when stdin is not a terminal)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if args.command == "set-key":
        return set_key(args)
    report = build_report(args)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 1 if any(f["level"] == "error" for f in report["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main())
