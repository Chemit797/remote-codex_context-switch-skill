# Recovery protocol

## Scope and limits

Codex rollout/session files are local artifacts. A new ChatGPT account can
open a locally restored task only if the target installation has compatible
local configuration and valid authentication for that new account. This does
not migrate remote ChatGPT history, entitlements, cloud tasks, subscriptions,
or credentials.

### SSH remote hosts

When Codex runs over SSH, the remote host's CODEX_HOME is a local artifact
store on that remote host; it is independent of the desktop CODEX_HOME. Run the
helper where the source or target files live, or against paths made available
through a user-approved secure mount. Do not assume that a desktop login,
desktop config, or local task file applies to the remote host. For a
cross-host move, create the bundle on the source host and transfer the
plaintext bundle only through a user-approved private channel.

The portable unit created by this skill contains selected JSONL session files,
matching **unambiguous** index records, an integrity manifest, and optionally
only attachments referenced by those sessions. If the source index contains
multiple records for a selected ID, it excludes that ambiguous index record and
records the omission rather than guessing; the supported migration step is then
responsible for publication. It deliberately excludes `auth.json`, all source
`config.toml` files, SQLite state/history databases, secrets, logs, caches,
and plugins. It is a **plaintext directory**, not an encrypted archive: create
it only in a private, user-owned staging location, keep its mode at `0700`, and
transfer it only through a user-approved private channel. Never create it in a
Git repository (the helper refuses this), shared folder, or cloud-sync directory
unless that location is explicitly approved for the sensitive transcript data.

Attachment recovery is deliberately narrow rather than a recursive scrape of
conversation text. The current supported Codex layout is a string item in
`event_msg.payload.local_images[]`. Each packaged attachment records the exact
task ID, physical JSONL line, and slot that references it. A selective restore
therefore copies only attachments owned by the selected task(s), and rewrites
only that quoted JSON token in the target copy. Ordinary message text, number
spellings, duplicate JSON keys, formatting, and line endings remain untouched.
An unknown attachment layout is preserved as raw JSONL but is not claimed as a
portable attachment; a malformed `local_images` slot makes an
`--include-attachments` bundle fail rather than guessing. Recreate a bundle
with this skill if an older bundle lacks task-scoped attachment locations.

## Recovery levels

1. **Evidence recovery** — JSONL and referenced attachments are valid and can
   be inspected without changing the target.
2. **Visibility recovery** — selected files and index entries are copied into
   a stopped target Codex home without overwriting anything.
3. **History migration** — the target Codex CLI publishes compatible legacy
   rollouts using `codex migrate-rollouts`; inspect first, use `--apply` only
   after the user authorizes it.
4. **Continuation recovery** — the task opens in Desktop with a target-side
   provider configuration and target-account authentication. This is an
   acceptance check, not a transfer of the old account.

## Provider compatibility

A historic session can record a provider ID that no longer exists in the
target config. The provider ID is a local compatibility contract, not a
credential. The inventory reports source provider IDs and target provider
tables without exposing secret values.

When the user explicitly confirms a mapping, `provider-alias` adds a new
`[model_providers.<legacy-id>]` table by copying only non-secret settings from
an existing target provider that explicitly uses the target's OpenAI login. It
refuses providers that rely on `env_key`, bearer tokens, query/header settings,
or auth commands rather than copying a partial, broken alias. It also leaves
the target's global `model_provider` untouched so new tasks keep their existing
behavior.

The relevant Codex configuration rule is that `model_provider` names a
provider ID from `model_providers`; provider-related keys belong in the
user-level config, not a project config. See the
[official configuration reference](https://developers.openai.com/codex/config-reference).

## Safe restore procedure

1. Keep the destination Codex Desktop/app server closed while files are being
   restored. Use a dedicated empty target CODEX_HOME; do not delete a nonempty
   account's data to make room and do not attempt a blind merge. The target must
   be the home the intended Codex installation will actually use (normally its
   freshly logged-in `~/.codex`), rather than an arbitrary staging directory.
2. Run inventory against both locations and preserve its report with the
   transfer bundle. It reports target task/index counts as well as provider
   compatibility, so an existing local task store is visible before an attempt
   to restore.
3. Create a bundle with `--apply`, transfer it privately, and run inventory on
   the bundle before restoring.
4. Resolve provider compatibility only after the user confirms a concrete
   source-to-target mapping.
5. Run `restore --thread <id> --i-confirm-codex-is-closed --apply` (or make an
   explicit `--all-sessions` choice). The helper creates a backup of the target
   `session_index.jsonl` before adding entries, keeps recovery-data directories
   private, and rolls back newly copied payloads plus index changes if its own
   write transaction fails.
6. Run `CODEX_HOME=<target> codex migrate-rollouts --thread <id> --json`.
   Review its eligibility result. Only then run the same command with `--apply`
   if authorized. Setting `CODEX_HOME` is essential when the target is not the
   current default home.
7. Reopen one task in Desktop and confirm it renders. If it shows a provider
   error, re-run inventory; do not alter SQLite databases by hand.

## Rollback

The restore helper never replaces session files or attachments. Its only
mutable shared file is `session_index.jsonl`, for which it creates a timestamped
backup if one already exists. The provider alias helper similarly backs up
`config.toml`. Roll back only those specific, timestamped backups after closing
Codex; do not restore old state databases or old authentication files over a
new account. If a filesystem failure occurs during restore, stop and inspect
the target before retrying; do not bypass the tool's nonempty-target refusal by
deleting files blindly.

## Supported and unsupported outcomes

The tool proves that selected local files are intact, that they are copied
without collisions, and that the target config recognizes the historic provider
ID. It cannot prove that a particular Codex app version will display every
legacy JSONL layout. The supported `migrate-rollouts` dry run is the next
compatibility gate. If it cannot publish the task, preserve the bundle as
evidence and seek a compatible Codex version or official support; do not modify
`state_*.sqlite` or `thread_history_*.sqlite` by hand.
