# Project Description

## Recommended presentation

The existing public skill identifier remains:

~~~text
remote-codex-provider-switch
~~~

Its user-facing purpose is now **Remote Codex Context Recovery**. Retaining the
identifier avoids breaking existing installations while making the actual
recovery boundary explicit.

## GitHub description

### English

Safely recover selected local Codex tasks after an account, provider, machine,
or SSH-host switch—without copying credentials or editing SQLite state.

### 简体中文

在切换 Codex 账号、provider、机器或 SSH 远端主机后，安全恢复指定的本地任务；
不复制凭据，也不修改 SQLite 状态。

## Short descriptions

### English

- Recover local Codex tasks after an account switch.
- Move selected Codex task files safely between hosts.
- Repair a legacy provider ID without moving credentials.

### 简体中文

- 切换账号后安全恢复本地 Codex 任务。
- 在主机之间安全迁移指定的 Codex 任务文件。
- 不搬运凭据，修复旧任务的 provider ID。

## Scope statement

This skill handles local Codex task artifacts. A remote SSH host simply owns a
separate local CODEX_HOME. It does not migrate ChatGPT cloud history,
entitlements, subscriptions, account access, OAuth state, cookies, API keys,
or arbitrary remote state databases.

## Suggested GitHub topics

~~~text
codex
codex-skill
conversation-recovery
account-switch
provider-switch
ssh
remote-codex
context-preservation
~~~

## Publishing notes

- Do not publish real task JSONL files, attachments, bundles, state databases,
  authentication files, private provider endpoints, server addresses, or keys.
- The bundle produced by the helper is plaintext conversation data. Keep it in
  a private location and transfer it only via a user-approved private channel.
- The repository includes an MIT license.
