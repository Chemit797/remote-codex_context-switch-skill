# History and provider IDs

Why conversations survive a key or relay swap, and the one way they stop
resolving.

## How a task is keyed

Every task records the provider ID it was created under. You can see the counts
without reading any conversation:

- `python scripts/codex_fuel.py` prints them on the `History` line (read from
  `state_*.sqlite`, `threads.model_provider`, opened read-only);
- `codex doctor --json` reports the same numbers as `rollout DB model providers`.

When a task is opened, Codex resolves that ID (as observed):

| Task tag | Resolves to |
|---|---|
| an ID with a `[model_providers.ID]` table | that table's endpoint |
| `openai` (built-in) | the official endpoint, or `openai_base_url` when set |
| `ollama`, `lmstudio` (built-in) | the local defaults |
| anything else | nothing: the task is listed but cannot resume |

The built-in IDs are reserved. A `[model_providers.openai]` table makes the
whole config fail to load (reproduced with
`codex doctor --json -c 'model_providers.openai.base_url="..."'`); Codex then
falls back to ChatGPT defaults without an obvious error.

## What follows from that

- Changing the key, the endpoint of an existing table, or the model never
  touches history. This is the "fuel swap".
- Renaming or replacing the provider table, or pointing `model_provider` at a
  new ID, orphans every task tagged with the old ID. Alias the old ID
  ([playbook section F](playbook.md)) or, better, do not change it.
- The official-to-relay direction is the awkward one because `openai` cannot be
  aliased. `openai_base_url` routes the built-in ID through the relay without
  touching any tag: verified to load and to make `codex doctor` report provider
  `openai` with the relay as endpoint and API-key auth. Resuming an old task
  through it was not verified; test with one task before relying on it.

## Last resort: retagging `openai` tasks

Only if `openai_base_url` does not let old `openai`-tagged tasks resume, and
only with explicit user approval. This is not automated by the skill and is only
partly verified.

One machine solved it by renaming the tag `openai` to another ID of the same
length (`mirror`, six characters) and adding a `[model_providers.mirror]` alias.
Length matters because the byte offsets of rollout records are indexed in
`state_*.sqlite` and `thread_history_*.sqlite`; a longer or shorter name
shifts them. On that machine the SQLite tag was renamed for every task while
some rollout files still carried `openai`, and the tasks were used afterwards,
but this skill did not verify resuming them in the app. If you go down this
path: close Codex Desktop and every CLI first, back up the whole `state_*`
and `thread_history_*` file sets (including `-wal` and `-shm`) and the affected
rollouts, change nothing else, and keep the backups until the tasks have been
opened and continued successfully.

## Provider aliases

`scripts/codex_account_recovery.py provider-alias` adds a
`[model_providers.<legacy id>]` table for an ID that no longer resolves. With a
custom active provider it copies only non-secret transport fields; with
`--active-provider openai` it writes `requires_openai_auth = true` and no
`base_url`. It never changes the top-level `model_provider`, refuses providers
that carry secrets, headers or auth commands, and refuses to alias a reserved ID.
It backs up `config.toml` before writing. `python scripts/codex_fuel.py` prints
the ready-to-run command for each unresolved tag.

## Where this was checked

Windows 11, codex-cli 0.146.0 (npm) and 0.155.0-alpha.2.6 (desktop bundle), one
relay, real history of about 530 tasks across three provider tags. Not checked:
an SSH host, ChatGPT-to-ChatGPT login, `--device-auth`, and resuming tasks after
`openai_base_url`. Treat those as "expected, confirm on first use".
