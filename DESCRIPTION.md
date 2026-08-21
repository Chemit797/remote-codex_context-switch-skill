# Project Description and Naming Options

This file contains copy-ready GitHub metadata in English and Simplified Chinese.

## Recommended Name

### Skill/repository name

`remote-codex-provider-switch`

Why this is recommended:

- clearly describes the remote SSH and provider-switching scope;
- works for account-to-account and relay-to-relay changes;
- easy to discover when users search for Codex provider or account switching;
- matches the current skill folder and `$remote-codex-provider-switch` invocation.

## GitHub Description

### English

Safely switch remote Codex providers or accounts over SSH while preserving conversation history and continuation context.

### 简体中文

通过 SSH 安全切换远端 Codex 的 provider 或账号，同时保留原有对话历史和继续对话上下文。

## Short Descriptions

### English

- Switch remote Codex providers without losing context.
- Change Codex accounts over SSH while preserving threads.
- Keep the same Codex task while changing its provider.

### 简体中文

- 远端切换 Codex provider，不丢失上下文。
- SSH 切换 Codex 账号，保留原有对话。
- 更换额度来源，继续使用同一个 Codex 任务。

## Alternative Skill Names

| Name | Best use | Tradeoff |
| --- | --- | --- |
| `remote-codex-provider-switch` | Recommended general-purpose name | Slightly technical, but precise |
| `remote-codex-context-switch` | Emphasize preserving conversation context | Provider/account routing is less explicit |
| `codex-ssh-account-switch` | Emphasize SSH and account changes | Less suitable for relay-to-relay provider changes |
| `codex-context-migration` | Emphasize history migration and compatibility | Could be confused with migrating between machines |
| `codex-fuel-switch` | Emphasize the “change fuel, keep driving” metaphor | Less discoverable and less formal for a public repository |
| `remote-codex-routing` | Emphasize route and quota verification | Does not clearly promise history preservation |

## Suggested GitHub Topics

```text
codex
remote-codex
ssh
provider-switch
account-switch
conversation-migration
context-preservation
app-server
chatgpt
relay-provider
```

## Suggested Repository Subtitle

### English

Change the provider or account behind a remote SSH Codex session without replacing the conversation.

### 简体中文

更换远端 SSH Codex 会话背后的 provider 或账号，同时保留原有对话。

## Publishing Notes

- Keep `README.md` as the default English GitHub landing page.
- Link `README.zh-CN.md` from the top of the English README.
- Add a clear open-source license before publishing; no license is assumed here.
- Do not publish real server IPs, usernames, API keys, relay URLs, session files, SQLite databases, or migration backups.
- Do not include private conversation content in examples or screenshots.
