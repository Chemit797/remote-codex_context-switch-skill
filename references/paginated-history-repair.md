# Paginated-history repair

Use this procedure only when a task is locally present and opens, but Codex
renders an old prefix while the rollout JSONL still contains later original
turns. This is different from a missing provider alias or an un-migrated legacy
rollout.

## Failure signature

A paginated rollout has one top-level integer `ordinal` per physical JSONL
record. App Server projects those records in strict `0, 1, 2, ...` order. An
account/provider switch can leave a later append segment starting with an
already-used ordinal. App Server then stops at the prefix and can log a message
like:

~~~text
thread history projection ... expected ordinal 4825, got 4824
~~~

The transcript after that point may still be intact. `migrate-rollouts`
returning `already_paginated` only describes the rollout format; it does not
check that ordinals are continuous or that projection reached the end.

## Non-destructive diagnosis

1. Identify the exact task ID, source CODEX_HOME, rollout path, and installed
   Desktop/App Server binary. Do not assume the `codex` found first on `PATH`
   is the same version used by Desktop.
2. Inventory or bundle the selected task before any repair. Record the rollout
   SHA-256, size, line count, first/last timestamps, and task/turn boundary
   counts without printing message content.
3. Compare raw evidence with the App Server's paged `thread/turns/list` result.
   If later completed turn IDs exist in JSONL but not in the page, inspect App
   Server warnings for an ordinal projection error.
4. Run the read-only audit:

   ~~~bash
   python3 scripts/repair_paginated_rollout.py audit \
     --rollout /path/to/rollout.jsonl \
     --json
   ~~~

The helper validates every JSONL record and requires `history_mode` to be
`paginated`. It reports only structural metadata. A repair is eligible only
when ordinal 0 is present, every mismatch is a duplicate/rewind relative to the
physical record position, and there is no forward gap. A forward gap can mean
records are missing; preserve the evidence and stop rather than inventing
ordinals.

Do not send a prompt, append a summary, call `thread/inject_items`, or otherwise
create a new turn as a visibility workaround. Those operations mutate the
conversation and do not restore the hidden turns.

## Isolated proof before live repair

For a valuable or ambiguous task, test the repair on a selective bundle
restored into a fresh temporary CODEX_HOME:

1. Restore the selected rollout and its unambiguous index record to the clean
   home.
2. Run the repair helper on that copy while no App Server uses the temporary
   home.
3. Launch the exact installed App Server with `CODEX_HOME` set to the temporary
   home. Initialize with `experimentalApi: true`, resume the task with
   `excludeTurns: true`, and request an `initialTurnsPage` or call
   `thread/turns/list` explicitly.
4. Verify the original turn IDs, statuses, ordering, count, and latest final
   answer. Do not send a model turn. The temporary `thread_history_*.sqlite`
   is disposable proof and must not be copied into the real CODEX_HOME.

This isolated check is the acceptance test for the diagnosis. In particular,
it proves that changing only ordinal metadata allows the installed App Server
to project the original records.

## Live repair

Before applying, fully close Codex Desktop and every CLI/App Server process
using the target CODEX_HOME. Merely navigating away from the task is
insufficient because an idle task can keep its rollout open.

Run a dry repair first:

~~~bash
python3 scripts/repair_paginated_rollout.py repair \
  --rollout /path/to/rollout.jsonl \
  --json
~~~

If `planned` is true and the isolated proof passed, apply it:

~~~bash
python3 scripts/repair_paginated_rollout.py repair \
  --rollout /path/to/rollout.jsonl \
  --i-confirm-codex-is-closed \
  --apply \
  --json
~~~

The helper creates a timestamped sibling backup, rewrites only serialized
top-level ordinal integers, atomically replaces the rollout, and validates the
result. It refuses symlinks, hard links, malformed JSONL, non-paginated files,
nonzero first ordinals, and forward gaps.

Reopen Desktop, resume the existing task, and page history without sending a
prompt. App Server should continue its own projection from the repaired
rollout. Verify that the formerly hidden original turns appear in chronological
order and that no synthetic recovery turns were added. Do not edit, replace,
or copy `state_*.sqlite` or `thread_history_*.sqlite`.

## Removing a mistaken synthetic recovery turn

Prefer restoring the exact pre-intervention rollout from the verified bundle,
then apply the ordinal repair to that copy before the live replacement. If no
pre-intervention copy exists, use an installed, supported thread-history revert
operation only after verifying its boundary turn on an isolated copy. Do not
manually delete arbitrary JSONL lines: tool calls, turn completion records, and
ordinal continuity must remain coherent.

## Rollback

If validation fails after reopening Codex, close all processes using the home
again and atomically restore the helper's exact
`before-ordinal-repair-*.bak` file. Keep the bundle, audit reports, and failed
rollout for diagnosis. A failed repair is not permission to rebuild or edit the
SQLite schema directly.
