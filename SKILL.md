---
name: remote-codex-provider-switch
description: Change the account, API key, or relay/provider behind Codex (on this machine or an SSH host) while keeping every conversation. Use for switch/change key, 换号, 换 key, 换中转站, official<->relay, 401 or invalid key, model not found, tasks missing from the sidebar, or provider errors after a switch. Most swaps are a one-file change; run the bundled check first instead of moving history around.
metadata:
  short-description: Swap Codex key or provider without losing history
---

# Codex fuel swap

Changing account is changing the fuel, not the engine. Conversations live in
`CODEX_HOME` and stay where they are. Two layers change; the third only has to
keep resolving.

| Layer | Lives in | A swap means |
|---|---|---|
| Credential | `CODEX_HOME/auth.json` (or the env var named by a provider's `env_key`) | new key / new login |
| Route | `config.toml`: `model_provider`, `[model_providers.X].base_url`, `openai_base_url`, `model` | new endpoint or model |
| History | rollouts + `state_*.sqlite`; every task is tagged with the provider ID it was created under | never moved; its tag must still resolve |

**Rule 1 - keep provider IDs stable.** Change a table's `base_url` and the key.
Do not invent a new ID for a new relay: every task tagged with the old ID is
orphaned. If the ID must change, alias the old one (see the playbook).

**Rule 2 - prove it, do not assume it.** A swap is finished only when step 3
passes. `codex login` accepts any string, an editor can save an old buffer over
`auth.json`, and a hand-edited file may not even parse.

## 1. Diagnose (about a second, read-only)

Paths below are relative to this skill's directory; use `python3` on Linux/macOS.
This skill works the same whether it is loaded by Codex or by Claude Code.

~~~bash
python scripts/codex_fuel.py            # --probe tests the key against the endpoint, --doctor adds Codex's own verdict
~~~

Run it on the host that owns the `CODEX_HOME` whose task history you want to
keep (the `History` line is non-zero there). For an SSH host, no install needed:
`ssh HOST python3 - --probe < scripts/codex_fuel.py`. Never guess an SSH alias,
user, or path; ask. The desktop app and an SSH host are separate `CODEX_HOME`s
with separate `auth.json` and `config.toml`; run the check on both if unsure
which one serves the tasks.

Read the `Route`, `Credential` and `History` lines, then the findings. Each
finding carries its fix.

## 2. Apply the smallest change

| From -> to | Do (exact commands in [the playbook](references/playbook.md)) |
|---|---|
| same relay, new key | `python scripts/codex_fuel.py set-key`. Nothing else changes. |
| relay -> another relay | edit `base_url` in the existing table, `set-key`, pick a `model` from the probe list |
| official -> relay | keep `model_provider = "openai"`, add top-level `openai_base_url`, `set-key` |
| relay -> official | remove `openai_base_url`/relay provider, `codex logout` + `codex login`, alias old tags |
| official -> official | `codex logout`, `codex login` (`--device-auth` on a headless host) |

Back up before editing config: `cp config.toml config.toml.bak-$(date +%Y%m%d%H%M%S)`.

`set-key` asks for the key at a hidden prompt, which an agent cannot answer. Either
ask the user to run it in their own terminal (in Claude Code: type it after `!`), or,
if they gave you the key, pipe it in: `printf '%s' "$KEY" | python scripts/codex_fuel.py set-key`
(stdin is read automatically when it is not a terminal; from PowerShell it is safe too).

## 3. Verify

~~~bash
python scripts/codex_fuel.py --expect-tail LAST5_OF_KEY --probe
~~~

Done means: no error findings, the key tail matches, the probe answers 200.
Then restart Codex Desktop or reconnect the SSH session and open one old task
without sending a prompt. Do not send a test prompt to "check it works"; it
costs tokens and appends a turn.

## Symptoms

| You see | Cause | Fix |
|---|---|---|
| 401 / invalid key after a "successful" login | key piped from PowerShell carries a hidden U+FEFF; wrong key; stale env var | `check` flags it; use `set-key` |
| `auth.json` unreadable after a hand edit | `//` comment or BOM; JSON has no comments | remove it; keep old keys in a `.bak-*` file, never as a comment |
| `codex login --api-key` "unexpected argument" | the flag is `--with-api-key` and reads stdin | use `set-key` |
| Asks for a ChatGPT login / `codex doctor` says config load failed | broken TOML or a `[model_providers.openai]` table | delete the table; use `openai_base_url` |
| model not found / 404 after changing endpoint | the new relay does not offer that `model` | pick one from `--probe` output |
| old tasks missing or "provider not found" | their provider ID is no longer defined | alias the ID (playbook, "History") |
| tasks open but stop at an old turn | ordinal gap in a paginated rollout | [paginated-history-repair](references/paginated-history-repair.md) |
| `login status` shows the old key tail | write did not land, was overwritten, or another `CODEX_HOME`/env applies | re-run `set-key`, then `check --expect-tail` |

## Rules

- Never print a whole key: last 5 characters only. If the user has pasted a key
  into chat, use it as given and do not lecture.
- Prefer `set-key` over hand-editing `auth.json` or piping into `codex login`.
  It hides input, strips BOMs, backs up, writes through the official CLI, and
  re-checks. Confirm flags with `codex login --help`; they differ across versions.
- Do not edit JSONL rollouts or SQLite. The two documented exceptions live in
  the references and need explicit user approval, backups, and Codex closed.
- Ask before touching a remote host, and before deleting a credential
  (`codex logout` removes the stored login; back up `auth.json` first).
- The history-moving tools are rarely needed, so keep them out of the way:
  a new machine or host needs [advanced-recovery](references/advanced-recovery.md);
  how tags and aliases work is in [history-and-providers](references/history-and-providers.md).
  ChatGPT cloud conversations cannot be recovered locally.
- After changing the helpers, run `python -m unittest discover -s tests -v`.
