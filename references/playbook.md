# Playbook: exact steps per switch

`CH` is the `CODEX_HOME` you are changing (`~/.codex` by default, or the value
of `$CODEX_HOME`). Run everything on the host that owns it. Commands assume the
skill directory is the working directory; `set-key` and `check` accept
`--home CH` for any other location.

Verification status is marked where it matters. "Verified" means it was run
against real Codex CLIs (0.146.0 and 0.155.0-alpha.2.6 on Windows 11) with fake
keys in an isolated `CODEX_HOME`, or against a real relay with a real key.

## 0. Baseline

~~~bash
python scripts/codex_fuel.py --probe
~~~

Note the active provider ID, the endpoint, the model, the key tail and the
`History` counts. This is your "before". Editing while Codex Desktop is open is
fine; restart it afterwards. Keep any editor that has `auth.json` or
`config.toml` open saved and closed: a stale buffer with autosave can write the
old content back over your change (an `auth.json` was seen reverted hours after
a hand edit).

## A. Same relay, new key

~~~bash
python scripts/codex_fuel.py set-key            # hidden prompt
~~~

It backs up `auth.json` as `auth.json.bak-<timestamp>`, writes the key through
the newest `codex login --with-api-key`, and immediately runs the check with
`--expect-tail` and `--probe`. A healthy result ends with `Probe ... -> 200` and
no error findings. A 401 means the new key is wrong, expired, out of quota, or
belongs to a different relay.

Non-interactive: `printf '%s' "$KEY" | python scripts/codex_fuel.py set-key --key-stdin`.
This is also safe from PowerShell (`$k | python scripts\codex_fuel.py set-key --key-stdin`)
because the script strips BOMs.

Do not:

- pipe a key from PowerShell straight into `codex login --with-api-key`
  (verified: the stored key gets a leading U+FEFF; the login says "Successfully
  logged in" and every request then fails);
- keep the old key as a `//` comment in `auth.json` (JSON has no comments; the
  file stops parsing). Old keys belong in the `auth.json.bak-*` file;
- pass the key as a command-line argument (`--api-key` does not exist; it would
  also land in shell history).

`codex login --with-api-key` performs no online validation (verified: fake keys
are accepted), so "login succeeded" proves nothing. `--probe` does.

## B. Relay to another relay

1. `python scripts/codex_fuel.py`; note the active provider ID, for example `relay`.
2. In `config.toml` change only `base_url` inside the existing table. Keep the ID:

   ~~~toml
   [model_providers.relay]
   base_url = "https://NEW-RELAY/v1"
   ~~~

3. `python scripts/codex_fuel.py set-key`.
4. Read the models in the `Probe` line and set a top-level `model` the new relay
   offers. Relays offer different model names; a name the relay lacks gives
   "model not found".

## C. Official to relay

Both shapes below keep every existing task resolvable. Top-level keys must sit
above the first `[table]` header, or TOML makes them keys of that table.

**Shape 1: stay on the built-in `openai` ID (simplest, when tasks are tagged `openai`).**

~~~toml
model_provider = "openai"
openai_base_url = "https://RELAY/v1"
disable_response_storage = true
model = "MODEL-FROM-PROBE"
~~~

Then `set-key`. Verified: the config loads and `codex doctor` reports provider
`openai`, endpoint = the relay, `API key auth`. `disable_response_storage = true`
is what the working relay configuration seen here uses; drop it if your relay
supports response storage. Not verified: resuming an old `openai`-tagged task
through this route; open one to confirm.

**Shape 2: a named provider.**

~~~toml
model_provider = "relay"
openai_base_url = "https://RELAY/v1"     # also carries old `openai`-tagged tasks over
disable_response_storage = true
model = "MODEL-FROM-PROBE"

[model_providers.relay]
name = "relay"
base_url = "https://RELAY/v1"
wire_api = "responses"
requires_openai_auth = true
~~~

Never add `[model_providers.openai]`: verified to make config loading fail,
after which Codex silently behaves as if logged out with ChatGPT defaults
(`codex doctor` shows `reachability mode = ChatGPT auth`). Tasks tagged with
other old IDs (for example `codex`) need an alias, see section F.

## D. Relay to official

1. Back up: `cp CH/auth.json CH/auth.json.bak-$(date +%Y%m%d%H%M%S)`. `codex logout`
   deletes the stored login (verified), and the backup is how you return to the relay.
2. In `config.toml`: set `model_provider = "openai"` (or delete the line), delete
   `openai_base_url`, delete `disable_response_storage` if only the relay needed
   it, set `model` to one the account offers, and delete the relay's
   `[model_providers.<id>]` table (a backup of the config is enough to bring it back).
3. `codex logout`, then `codex login` (browser). On a headless or SSH host
   `codex login --device-auth` (listed by `codex login --help`; not exercised here).
4. Old relay-tagged tasks lost their table, so alias each ID to the built-in
   `openai` route (section F, with `--active-provider openai`).
5. Verify: run the check and `codex login status` (it should say it is logged in
   with ChatGPT), and look for no error findings. There is no key tail to compare.

## E. Official to official

~~~bash
codex logout
codex login            # or: codex login --device-auth   (headless / SSH)
python scripts/codex_fuel.py
~~~

Tasks tagged `openai` keep resolving; nothing else changes. Not exercised here
(needs a real account).

## F. History: tags that no longer resolve

The check lists each unresolved provider ID with the command to fix it:

~~~bash
python scripts/codex_account_recovery.py provider-alias \
  --config CH/config.toml --legacy-provider OLD_ID --active-provider ACTIVE_ID          # dry run
python scripts/codex_account_recovery.py provider-alias \
  --config CH/config.toml --legacy-provider OLD_ID --active-provider ACTIVE_ID --apply
~~~

The alias table copies only non-secret transport fields from the active
provider, or, with `--active-provider openai`, creates `requires_openai_auth =
true` with no `base_url` so it follows the current official login (verified as a
dry run). It refuses secret-bearing providers and refuses `openai` as the legacy
ID, because `openai` is reserved. For tasks tagged `openai` that must ride a
relay, use `openai_base_url` (section C) and see
[history-and-providers](history-and-providers.md).

## G. An SSH host

The host has its own `CODEX_HOME`, `auth.json` and `config.toml`. Not exercised
end to end in this session; confirm the first time.

~~~bash
ssh HOST python3 - --probe < scripts/codex_fuel.py                 # diagnose, nothing installed
scp scripts/codex_fuel.py HOST:/tmp/codex_fuel.py
ssh -t HOST python3 /tmp/codex_fuel.py set-key                     # -t gives the hidden prompt a terminal
ssh HOST rm /tmp/codex_fuel.py
~~~

A non-interactive SSH shell often lacks the login shell's `PATH` (npm-global or
nvm `codex`). If the report says the codex CLI was not found, `set-key` falls
back to writing `auth.json` directly in the same format (`auth_mode` plus
`OPENAI_API_KEY`, no BOM); otherwise use `ssh -t HOST bash -lc '...'`. Edit the
remote `config.toml` in place after a `cp` backup rather than copying the
desktop's file over it: the two hosts have different paths and plugins.

## Windows notes

- Several `codex` binaries can coexist: the npm shim (`%APPDATA%\npm\codex.cmd`)
  and the desktop-bundled `%LOCALAPPDATA%\OpenAI\Codex\bin\<hash>\codex.exe`.
  They share `~/.codex` but may differ in version and flags. `check` lists them,
  `set-key` uses the newest.
- `codex doctor --json` took about 30 s on the 0.146 shim and about 4 s on 0.155,
  so `check` runs it only with `--doctor`.
- Windows PowerShell 5.1 `Set-Content`/`Out-File -Encoding utf8` write a BOM, and
  piping into a native command can prepend U+FEFF. Prefer `set-key` for anything
  that ends up in `auth.json`.
- `secrets\` inside `CODEX_HOME` can hold a plaintext copy of a relay key. When
  rotating, update or remove stale copies; `check` lists the file names.
