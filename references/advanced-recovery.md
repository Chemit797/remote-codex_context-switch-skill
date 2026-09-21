# Advanced: moving tasks to a new machine, host, or clean CODEX_HOME

You almost never need this to switch an account or relay: those swaps happen in
place and history stays where it is (see [SKILL.md](../SKILL.md)). Use this only
when the tasks must physically move, and never bundle a `CODEX_HOME` onto
itself.

The tools in `scripts/codex_account_recovery.py` package selected local rollout
files, their unambiguous index records, and known task-scoped attachments into a
plaintext bundle, verify it, and restore it into a stopped, empty target. Read
[the recovery protocol](recovery-protocol.md) for the guarantees and rollback.
A bundle contains conversation text: keep it out of Git, shared drives and
cloud-synced folders, and transfer it only over a private channel you approved.

`inventory` scans every rollout (about 30 s for 550 tasks) and lists all task
IDs, so use `python scripts/codex_fuel.py` for diagnosis and this only to pick
the tasks to move.

1. Choose task IDs, then inspect source and optional target:

   ~~~bash
   python3 scripts/codex_account_recovery.py inventory \
     --source /path/to/source/.codex --target /path/to/target/.codex --json
   ~~~

2. Package and verify on the source host:

   ~~~bash
   python3 scripts/codex_account_recovery.py bundle \
     --source /path/to/source/.codex --output /private/staging/bundle \
     --thread TASK_ID --include-attachments --apply --json
   python3 scripts/codex_account_recovery.py verify --bundle /private/staging/bundle --json
   ~~~

   Omit `--include-attachments` unless attachments are needed. Add
   `--include-archived` if the ID exists only in archived sessions.

3. If the target lacks a provider ID the tasks carry, alias it after the user
   confirms the mapping ([history-and-providers](history-and-providers.md)).

4. With Codex Desktop and every App Server on the target closed, restore into a
   clean target:

   ~~~bash
   python3 scripts/codex_account_recovery.py restore \
     --bundle /private/staging/bundle --target /path/to/target/.codex \
     --thread TASK_ID --include-attachments --i-confirm-codex-is-closed --apply --json
   ~~~

5. Let Codex publish the restored rollouts through its own migration, dry run
   first, `--apply` only after approval:

   ~~~bash
   CODEX_HOME=/path/to/target/.codex codex migrate-rollouts --thread TASK_ID --json
   CODEX_HOME=/path/to/target/.codex codex migrate-rollouts --thread TASK_ID --apply --json
   ~~~

   If the CLI lacks the command or marks the task ineligible, keep the verified
   bundle and stop. If it reports `already_paginated` but the task renders
   truncated, see [paginated-history-repair](paginated-history-repair.md).

Boundaries: this cannot recover ChatGPT cloud conversations, and it never copies
`auth.json`, keys, cookies or a full `config.toml`. Log in on the target and set
its route with the normal playbook.
