# Project Description

## Recommended presentation

The public skill identifier stays:

~~~text
remote-codex-provider-switch
~~~

Its user-facing purpose is **switching the account, API key, or relay behind
Codex without losing conversations**. Retaining the identifier keeps existing
installations working.

## GitHub description

### English

Swap the Codex account, API key, or relay, locally or over SSH, without losing a
conversation: a one-second read-only check, a safe key swap, and a playbook.

### 简体中文

给 Codex 换账号、API key 或中转站（本机或 SSH 远端），一条对话都不丢：
一秒体检、安全换 key、逐场景操作手册。

## Short descriptions

### English

- Switch a Codex key or relay and keep every conversation.
- Find out in one second what is in effect and whether the key works.
- Swap an API key without a BOM, a broken auth.json, or a stale copy.

### 简体中文

- 换 Codex 的 key 或中转站，保留全部对话。
- 一秒看清当前什么在生效、key 好不好用。
- 换 key 不再遇到 BOM、auth.json 损坏或旧 key 残留。

## Scope statement

The skill changes only the credential (`auth.json`) and the route (`config.toml`)
and keeps the provider IDs that saved tasks carry resolvable. A remote SSH host
simply owns a separate `CODEX_HOME`. Moving tasks to another machine and
repairing truncated rollouts remain available as advanced, rarely needed tools.
It does not migrate ChatGPT cloud history, entitlements, subscriptions, account
access, OAuth state or cookies.

## Suggested GitHub topics

~~~text
codex
codex-skill
account-switch
provider-switch
api-key
relay
ssh
remote-codex
context-preservation
~~~

## Publishing notes

- Do not publish real task JSONL files, attachments, bundles, state databases,
  authentication files, private provider endpoints, server addresses, or keys.
- Examples in the docs use placeholders (`RELAY`, `HOST`, `LAST5_OF_KEY`).
- The bundle produced by the advanced helper is plaintext conversation data.
  Keep it in a private location and transfer it only via a user-approved private
  channel.
- The repository includes an MIT license.
