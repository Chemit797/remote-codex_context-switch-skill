# Operational Playbook

This playbook is for a remote Codex host reached from the desktop app with SSH. It treats provider/account changes as replacing the fuel while keeping the same vehicle and trip: the remote task ID and history stay in place while future turns use a selected target provider/account. Substitute the user's SSH alias and remote home directory; do not assume a particular IP, username, model, or relay URL.

## 1. Establish the topology

Write down the two independent runtimes before touching anything:

```text
Desktop Codex -> SSH -> remote Codex App Server -> provider/account
```

The desktop configuration controls local tasks. The remote `~/.codex/config.toml`, remote login state, and remote App Server control remote tasks. A local relay configuration cannot safely be inferred to apply to the remote host.

Use read-only checks on the remote host:

```bash
hostname
date
codex --version
codex login status
grep -E '^\s*(model_provider|base_url)\s*=' ~/.codex/config.toml
env | grep -E '^(OPENAI_BASE_URL|OPENAI_API_BASE|OPENAI_API_KEY|CODEX_API_KEY|CODEX_HOME)='
ps -u "$USER" -o pid=,ppid=,lstart=,args= | grep -E '[c]odex|[a]pp-server'
```

Redact API keys in all reports. An official ChatGPT session normally reports `Logged in using ChatGPT`; that is an authentication fact, not proof that every existing rollout is routed through the official provider.

## 2. Determine the effective provider

Use all of these signals, in this order:

1. `config.toml`: identify the current provider name, provider block, model, and endpoint. Do not assume that a provider name such as `openai` or `codex` alone proves where traffic goes.
2. Environment and process arguments: an `OPENAI_BASE_URL`, API-key override, wrapper, or `-c` argument can override the file.
3. Rollout first records: inspect the JSONL metadata without printing secrets. Count `payload.model_provider` by active and archived directory.
4. SQLite state: inspect `threads.model_provider` and `threads.archived` read-only.
5. App Server read: use `thread/list` or `thread/read` with `includeTurns` only as needed. Do not use `turn/start`.

Do not call a provider “switched” until the file metadata, SQLite active rows, and a read-only App Server load agree. `thread/resume` can select a runtime provider for that invocation but does not necessarily rewrite historical rollout metadata.

## 3. Switching a remote host to a target provider/account

Before mutation:

- Confirm the source provider/account and the target provider/account. The target may be an official account, another official account, a relay, or a different relay.
- Confirm the target credentials/login or API configuration is complete.
- Save `~/.codex/config.toml`, `state_5.sqlite*`, `sessions/`, `archived_sessions/`, and `session_index.jsonl` in a timestamped backup outside the live directories. Verify backup file counts and hashes where practical.
- Inventory active and archived threads, provider counts, rollout paths, and thread spawn edges.
- Stop or wait for active turns. Do not edit a rollout while it is being appended.

For the provider/account migration itself:

- Parse every active rollout first. Abort the whole batch on a malformed first record, database/file path mismatch, path escape, or unexpected provider.
- Change only the exact provider field(s) needed to identify the target. In JSONL, preserve every line and byte except the intended provider/account metadata; use atomic temp-file replacement.
- Update the corresponding active SQLite `threads.model_provider` values in one transaction. Recheck the expected ID set before committing.
- Scan all `session_meta` records, not just the first line. Legacy merged/restored conversations can contain later source-provider records that cause App Server to repopulate stale SQLite state.
- Inspect provider-specific history contracts. If the history contains synthetic IDs, encrypted reasoning items, or provider-specific tool records, use a type-aware compatibility adapter only when the target provider's contract is known. Never blindly replace all strings. Preserve event references, check for collisions, and back up those files separately.
- Restart only the exact remote user's App Server and proxy process tree after all files and SQLite changes are complete. Let the desktop reconnect.

## 4. Keeping a target provider as the long-term default

Use separate, explicit config snapshots rather than manually editing a large file repeatedly. For example:

```toml
# target.config.toml: provider selection and model settings for one target
model_provider = "<target-provider>"
model = "<model>"
model_reasoning_effort = "medium"
```

Each provider/account snapshot should contain the actual provider block, endpoint, and credential mode used by that installation. Do not invent a provider name or URL. A switch script should:

1. verify the target snapshot exists and parses;
2. back up the current config;
3. install the target config atomically;
4. restart only the user's remote App Server;
5. run the read-only provider checklist;
6. report the effective endpoint without exposing keys.

If the desktop should remain on one provider while the remote host uses another, apply the target snapshot only on the remote host. Never copy the remote config back to the desktop unless that is explicitly the requested source/target change.

## 5. Archived conversations

Archiving is a visibility/state operation, not proof that a rollout has been erased. A previous apparent “conversation disappearance” can be a sidebar index, pin, or archive-state mismatch while the original rollout files remain intact.

If the user authorizes deleting all archived conversations:

- Confirm the scope means `archived = 1` only. Never infer permission to delete active threads.
- Make and verify a complete backup first.
- Check `thread_spawn_edges` for archived-parent -> active-child relationships. Stop if any exist.
- Record counts and IDs before deletion.
- Prefer the supported `thread/delete` operation when it is available and its descendant semantics match the requested scope. Otherwise delete archived database rows and archived rollout files together, preserving the backup.
- Recheck: archived rows are zero, archived files are zero, active rows/files are unchanged, and all active IDs remain present.

## 6. Verification without spending quota

Run all of the following after restart:

```bash
# no model generation
codex login status
grep -E '^\s*(model_provider|base_url)\s*=' ~/.codex/config.toml
python3 - <<'PY'
# Count active/archived JSONL metadata and report provider values only.
PY
sqlite3 -readonly ~/.codex/state_5.sqlite \
  "SELECT archived, model_provider, COUNT(*) FROM threads GROUP BY archived, model_provider;"
```

Then use read-only `thread/list` and `thread/read` against at least two old active thread IDs, including one that previously failed. Verify that the title, ID, turn history, and project path are present. Do not send a new user message as a routing test.

The final report should state:

- desktop provider and endpoint status (without secrets);
- source and target provider/account, login mode, and effective endpoint evidence;
- active/archived counts before and after;
- number of migrated metadata records;
- backup locations;
- exact App Server restart scope;
- whether old history can both open and continue under the target; any residual history that can open but cannot continue because it contains provider-specific incompatible IDs.

## 7. Recovery

If a post-change check fails, stop generating turns. Do not delete more history and do not recreate threads. Restore the affected rollout files and SQLite/config backups atomically, restart only the remote user's App Server, then re-run the read-only inventory. Keep every migration backup until the user confirms that old conversations open and the intended account is being used.

## 8. Common failure modes learned from prior incidents

- “Login succeeded” but old threads still use the source: login state and historical provider metadata are separate.
- The target provider is present in the config but the desktop reports the old provider: old rollout first records or session metadata still point at the source.
- A runtime provider override appears to work but does not migrate history: runtime override is not persistent migration.
- Only the first JSONL record was changed: later `session_meta` records can reintroduce the source provider into SQLite.
- A successful read is mistaken for a successful continuation: source-specific IDs or records may be rejected by the target on the next turn.
- Broad `killall codex` disrupts unrelated users or servers: identify exact PIDs and ownership first.
- Deleting archived files without a state/index plan makes the sidebar appear empty or inconsistent: keep the full backup and validate indexes/state.
