# Remote Codex Context Switch

English | [简体中文](README.zh-CN.md)

## In plain language

Switch the account, API key, or relay behind Codex, on your machine or on an SSH
host, **without losing a single conversation**. Think of it as changing the fuel,
not throwing away the car.

It covers every direction: official to official, official to relay, relay to
official, and relay to another relay. In most cases the whole job is one new key
and, at most, one line of `config.toml`. Your history stays exactly where it is.

## What you get

- **`scripts/codex_fuel.py`** - a one-second, read-only report: which
  `CODEX_HOME`, provider, endpoint and model are active; which key (last five
  characters) is stored; which provider IDs your saved tasks carry and whether
  each still resolves. `--probe` asks the provider whether it accepts the stored
  key (a free `GET /models`); `--doctor` adds Codex's own verdict.
- **`codex_fuel.py set-key`** - swaps the key safely: hidden prompt, strips BOMs,
  backs up `auth.json`, writes through the official `codex login`, then re-checks.
- **A playbook** ([references/playbook.md](references/playbook.md)) with the
  exact steps for each switch, for Windows and for an SSH host.
- **A symptom table** in [SKILL.md](SKILL.md): 401, "model not found", tasks
  missing, a config that silently stops loading, and more.
- **Advanced tools**, rarely needed: moving tasks to a new machine
  ([advanced-recovery](references/advanced-recovery.md)) and repairing a
  truncated task ([paginated-history-repair](references/paginated-history-repair.md)).

## The model behind it

| Layer | Lives in | A swap means |
|---|---|---|
| Credential | `auth.json` | new key or login |
| Route | `config.toml` (`base_url`, `openai_base_url`, `model`) | new endpoint |
| History | rollouts and `state_*.sqlite`, each task tagged with a provider ID | stays put; its tag must keep resolving |

Keep provider IDs stable and change only the credential and the route. An SSH
host is a separate `CODEX_HOME` with its own `auth.json` and `config.toml`; run
the check on the host that holds the tasks you care about.

## Lessons this skill encodes

- `codex login` does not validate the key. "Successfully logged in" proves
  nothing; `--probe` does.
- Piping a key from PowerShell into `codex login --with-api-key` stores it with an
  invisible leading U+FEFF, so every request fails with 401. `set-key` avoids it
  and `check` detects it.
- The flag is `--with-api-key` (stdin), not `--api-key KEY`.
- JSON has no comments. Commenting out the old key in `auth.json` breaks the file.
  Keep old keys in a `.bak-*` file.
- A `[model_providers.openai]` table stops the whole config from loading and
  Codex quietly falls back to ChatGPT defaults. To send the built-in `openai`
  provider through a relay use the top-level `openai_base_url`.
- Changes get overwritten (an editor autosaving an old buffer, for one). Verify
  with `--expect-tail`, do not assume.
- Relays offer different model names; take `model` from the probe output.

## Quick start

~~~bash
python scripts/codex_fuel.py --probe                 # what is in effect, and does the key work
python scripts/codex_fuel.py set-key                 # new key: backup, write, re-check
python scripts/codex_fuel.py --expect-tail ab12X --probe   # confirm the swap took effect
ssh HOST python3 - --probe < scripts/codex_fuel.py   # same check on an SSH host, nothing to install
~~~

## What it does not do

- Recover ChatGPT cloud conversations, or transfer subscriptions, account
  ownership, cookies or OAuth state.
- Send a test prompt to prove a switch worked (it costs tokens and appends a turn).
- Edit SQLite or rollout files as part of the normal flow. The two documented
  exceptions need explicit approval and backups.
- Guess an SSH alias, path or provider mapping.

Verified on Windows 11 with codex-cli 0.146.0 and 0.155.0-alpha.2.6. The SSH
path, ChatGPT-to-ChatGPT login and `--device-auth` follow from the CLI's own
help but were not exercised end to end; confirm them on first use.

## Installation

The repository root is the skill directory. Install or copy it as:

~~~text
~/.codex/skills/remote-codex-provider-switch/     # Codex (reads agents/openai.yaml too)
~/.claude/skills/remote-codex-provider-switch/    # Claude Code (reads SKILL.md directly)
~~~

`codex_fuel.py` needs Python 3.8 or newer and only the standard library. The
history-moving helper needs Python 3.11 or newer. Codex picks up new or changed
skills automatically; restart the app if a new skill does not appear. The
identifier `remote-codex-provider-switch` is unchanged so existing installs keep
working.

## Validation

~~~bash
python3 -m unittest discover -s tests -v
~~~

## License

[MIT](LICENSE).
