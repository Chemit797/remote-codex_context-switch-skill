# Remote Codex Provider Switch

English | [简体中文](README.zh-CN.md)

Safely change the provider or account used by a Codex App Server reached through SSH while keeping the same desktop task, thread history, and continuation context.

Think of it as changing fuel while the vehicle keeps driving:

```text
desktop Codex -> SSH -> remote Codex App Server
                         source provider/account
                                  ↓ switch
                         target provider/account
```

The source and target can be any combination:

- official account -> official account
- official account -> relay
- relay -> official account
- relay -> relay

## Why This Exists

The desktop Codex installation and the remote SSH installation are separate runtimes. Changing the desktop configuration does not necessarily change the remote App Server. Likewise, a successful remote login does not prove that existing conversations or future turns use the newly selected account.

Provider state can be present simultaneously in:

- `~/.codex/config.toml`
- environment variables and process arguments
- JSONL rollout/session metadata
- later `session_meta` records inside old conversations
- SQLite App Server state
- the App Server's in-memory cache

This skill checks and migrates those layers without rebuilding conversations or silently sending a test request.

## What It Does

- identifies the source and target provider/account;
- verifies the effective remote route without `turn/start` or a test prompt;
- creates a complete, timestamped backup before mutation;
- preserves thread IDs, turn IDs, messages, tool output, project paths, and history bytes;
- migrates provider metadata in rollout files and SQLite state;
- detects later `session_meta` records that can restore stale provider state;
- audits provider-specific response IDs and tool records before attempting compatibility conversion;
- restarts only the target remote user's App Server/proxy processes;
- verifies that the same desktop task still opens and that future continuation targets the new account;
- optionally removes archived conversations, but only with explicit authorization and a verified backup;
- restores from backup instead of deleting or recreating history when verification fails.

## Important Distinction

Preserving history and continuing history are related but different:

1. The original task and all prior turns must remain readable.
2. The target provider must accept the persisted history format for a new turn.

Some relays write provider-specific IDs, encrypted reasoning items, or tool-call records. The skill uses a type-aware adapter only when the target contract is known. If an exact continuation is impossible, it keeps the complete history and reports the incompatibility instead of claiming success.

## Installation

Copy the skill directory into the user's Codex skills directory:

```text
~/.codex/skills/remote-codex-provider-switch/
```

The directory must contain:

```text
SKILL.md
README.md
README.zh-CN.md
DESCRIPTION.md
agents/openai.yaml
references/operational-playbook.md
```

On Windows, the equivalent default location is:

```text
%USERPROFILE%\.codex\skills\remote-codex-provider-switch\
```

## Usage

Inspect a remote connection without changing it:

```text
Use $remote-codex-provider-switch to inspect which provider/account the remote SSH Codex is actually using.
```

Perform an authorized switch while preserving the current task:

```text
Use $remote-codex-provider-switch to switch the remote Codex from the current provider/account to the target provider/account. Keep the same thread ID and complete history, make a backup first, do not send a test turn, and verify that continuation routes to the target.
```

For a relay or account snapshot, provide the real target configuration. Do not invent provider names, endpoints, API keys, or login commands.

## Safety Model

- Read-only inventory comes before every mutation.
- Active turns must be stopped or allowed to finish before rollout edits.
- JSONL files are changed through atomic replacement; unrelated bytes are preserved.
- The skill never uses `turn/start` merely to test routing.
- It never broadens a request from archived deletion to active-history deletion.
- It never uses a broad `killall codex`; process ownership and exact PIDs are checked first.
- Secrets are redacted from reports.
- Every failed post-change verification stops further generation and triggers recovery from backup.

## Documentation

- [SKILL.md](SKILL.md): Codex skill entrypoint and invocation rules.
- [简体中文 README](README.zh-CN.md): Chinese project overview and usage guide.
- [Project descriptions and naming options](DESCRIPTION.md): copy-ready bilingual GitHub descriptions and name candidates.
- [Operational playbook](references/operational-playbook.md): provider detection, migration, compatibility checks, archive handling, verification, and recovery.

## Limitations

This skill cannot make an incompatible provider accept a history format it does not support. It can preserve the original conversation and identify the blocking record, but exact continuation may require a provider-supported import format or a manually reconstructed context. It also does not determine billing, quota, or account entitlements beyond routing evidence visible on the configured host.

## License

No license is declared in this skill directory yet. Add a repository license before publishing if redistribution terms are required.
