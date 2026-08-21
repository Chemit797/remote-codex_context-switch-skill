---
name: remote-codex-provider-switch
description: Safely switch any provider or account used by a remote SSH Codex host while keeping the desktop conversation history and continuation context intact.
---

# Remote Codex Provider Switch

Use this skill when a desktop Codex connection controls a remote host over SSH and the user needs to inspect, change, verify, or repair which provider/account handles remote turns. It covers account-to-account, account-to-relay, relay-to-account, and relay-to-relay changes, persisted rollout metadata, App Server caches, provider-specific history compatibility, and optional archived-history cleanup.

Read [the operational playbook](references/operational-playbook.md) before changing a remote host. Use its provider model, safety gates, verification checklist, and recovery rules.

Core invariants:

- Treat the desktop host and remote SSH host as separate Codex installations. The desktop's provider does not determine the remote App Server's provider.
- Model every change as `source provider/account -> target provider/account`. The source and target may both be official accounts, both be relays, or be different kinds of provider.
- Provider selection has three layers that must agree: `~/.codex/config.toml`, persisted rollout/session metadata, and the running App Server/SQLite state.
- A successful login proves authentication, not routing. Confirm the effective target provider from config, environment, process arguments, and a no-generation App Server read.
- Never use `turn/start`, send a test prompt, or otherwise generate a model response merely to verify routing. Read-only thread APIs and local metadata inspection are sufficient.
- Never rewrite an entire JSONL rollout through a parse-and-reserialize cycle. Preserve all bytes except the explicitly migrated metadata fields; use a temporary file plus atomic replacement and keep a backup.
- Preserve thread IDs, turn IDs, message bodies, tool outputs, and project paths. Do not recreate conversations to solve a provider mismatch.
- A changed first-line provider is insufficient when old `session_meta` records still contain the source provider; those records can repopulate SQLite on the next load.
- Keeping visible history is separate from being able to append a new turn. Provider-specific response IDs, encrypted reasoning records, tool-call schemas, or account-scoped references may need a compatibility migration. Preserve the original history even when a particular provider cannot continue it natively.
- Before destructive archive deletion, require explicit user authorization and a complete backup. Check spawn edges so an archived parent is not hiding an active descendant. Prefer the App Server's documented delete semantics when available; otherwise delete database rows and rollout files only with a recorded, reversible backup.
- After changing persisted state, restart only the exact remote user's Codex App Server/proxy processes. Do not kill other users' Codex processes or broad process patterns.

When the user asks only how to switch, explain the commands and verification gates without mutating the host. When the user authorizes a change, perform read-only inventory and backup first, then make the smallest provider/account change and re-verify after restarting the remote App Server. Success means the same desktop task ID opens, prior turns remain readable, and a continuation routes to the target account without silently falling back to the source.
