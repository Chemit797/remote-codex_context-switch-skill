# Remote Codex Context Recovery

English | [简体中文](README.zh-CN.md)

Safely recover selected **local Codex tasks** after changing a Codex account,
provider, machine, or SSH remote host—without moving credentials or rewriting
Codex SQLite state.

The repository keeps the existing invocation name
**remote-codex-provider-switch** so current installations keep working. The
workflow has been substantially corrected: it no longer claims that editing
rollout metadata or SQLite can safely make any old conversation continue under
another account.

## What it does

- inventories local Codex JSONL task files without printing task contents;
- packages explicitly selected tasks, unambiguous index records, integrity
  metadata, and optionally only known task-scoped attachments;
- restores a verified bundle only into a clean, stopped target CODEX_HOME;
- detects a legacy provider ID and can add a narrow non-secret compatibility
  alias after the user confirms the source-to-target mapping;
- uses the installed Codex CLI's supported migration path as a separate,
  user-approved visibility check.

An SSH host has its own local CODEX_HOME. The desktop's task store,
authentication, and provider settings are independent of the remote host's
store. Run the helper on the host that owns the task files, or use a
user-approved secure mount.

~~~text
desktop Codex  <->  SSH remote Codex host  ->  remote CODEX_HOME
local CODEX_HOME                              remote task history
~~~

## What it deliberately does not do

- move ChatGPT cloud conversations, subscriptions, account ownership, login
  state, auth.json, API keys, cookies, or a complete source config.toml;
- directly edit state/history SQLite databases or hand-rewrite JSONL rollouts;
- merge into an existing target task store, overwrite a task, or copy all
  attachments found on disk;
- infer a provider mapping from a name, endpoint, or account label;
- send a test prompt merely to prove recovery or routing;
- promise that a target account can natively continue every provider-specific
  legacy history.

## Safe workflow

1. Read [SKILL.md](SKILL.md) and the
   [recovery protocol](references/recovery-protocol.md).
2. Inventory the source and destination. Report only safe metadata such as
   task IDs, JSONL validity, attachment coverage, index ambiguity, target task
   count, and missing provider IDs.
3. Route the case:
   - For a **same-host account/provider switch**, retain the existing
     CODEX_HOME. Do not bundle and restore it onto itself. Complete the normal
     login on the intended account, repair only an explicitly confirmed legacy
     provider alias if needed, then inspect migration eligibility.
   - For a **new machine or remote host**, create and verify a selective
     bundle on the source, transfer the plaintext bundle through a
     user-approved private channel, and restore it only into a clean target.
   - If no local task/archived session exists, this repository cannot retrieve
     cloud-only history from a different ChatGPT account.
4. Inspect the supported migration command before allowing it to write. If it
   is unavailable or marks a task ineligible, preserve the verified bundle and
   stop instead of touching SQLite.

## Commands

Run the helper from the installed skill directory:

~~~bash
python3 scripts/codex_account_recovery.py inventory \
  --source /path/to/source/.codex \
  --target /path/to/target/.codex \
  --json
~~~

Create and validate one private, credential-free bundle:

~~~bash
python3 scripts/codex_account_recovery.py bundle \
  --source /path/to/source/.codex \
  --output /private/staging/recovery-bundle \
  --thread TASK_ID \
  --include-attachments \
  --apply \
  --json

python3 scripts/codex_account_recovery.py verify \
  --bundle /private/staging/recovery-bundle \
  --json
~~~

With the target Codex app server or Desktop closed, restore only to a dedicated
empty target:

~~~bash
python3 scripts/codex_account_recovery.py restore \
  --bundle /private/staging/recovery-bundle \
  --target /path/to/target/.codex \
  --thread TASK_ID \
  --include-attachments \
  --i-confirm-codex-is-closed \
  --apply \
  --json
~~~

Then use the target installation's supported migration check:

~~~bash
CODEX_HOME=/path/to/target/.codex \
  codex migrate-rollouts --thread TASK_ID --json
~~~

Add --apply to that last command only after explicit approval. See
[SKILL.md](SKILL.md) for the provider-alias command and decision rules.

## Installation

The repository root is the skill directory. Install or copy it as:

~~~text
~/.codex/skills/remote-codex-provider-switch/
~~~

It requires Python 3.11 or newer for the bundled helper. Codex detects
new/changed skills automatically; restart the app if a newly installed skill
does not appear.

## Validation

~~~bash
python3 -m unittest discover -s tests -v
~~~

The test suite includes selective attachment recovery, duplicate index
handling, provider-alias restrictions, path traversal/symlink/hard-link
rejection, byte-preserving JSON token remapping, rollback, and Windows manifest
paths.

## License

[MIT](LICENSE).
