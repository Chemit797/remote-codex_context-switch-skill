# 远端 Codex 换号不丢上下文

[English](README.md) | 简体中文

## 一句话说明

当你在本地使用 Codex，并通过 SSH 连接远端服务器上的 Codex 时，这个 skill
帮你安全地更换账号、API key 或中转站，同时保留原来的对话和上下文。

简单说就是：**换账号或换中转站，旧对话保留——相当于换号只是给车换燃料，换了继续开。**

四个方向都覆盖：官号切官号、官号切中转站、中转站切官号、中转站切另一个中转站。
大多数情况下，整件事只是换一个新 key，最多再改 `config.toml` 里的一行。历史对话原地不动。

## 你能得到什么

- **`scripts/codex_fuel.py`**：一秒出结果、只读的体检报告。当前生效的
  `CODEX_HOME`、provider、地址、模型；存着的是哪个 key（只显示后 5 位）；
  你的历史任务带着哪些 provider ID，每个还能不能解析。`--probe` 会直接问服务商
  "这个 key 你认不认"（免费的 `GET /models`）；`--doctor` 再加上 Codex 自己的判断。
- **`codex_fuel.py set-key`**：安全地换 key。隐藏输入、去掉 BOM、先备份
  `auth.json`、通过官方 `codex login` 写入，最后自动复查。
- **操作手册**（[references/playbook.md](references/playbook.md)）：每种切换的
  精确步骤，含 Windows 和 SSH 远端。
- **症状对照表**（在 [SKILL.md](SKILL.md)）：401、"model not found"、任务消失、
  配置悄悄失效等等。
- **高级工具**（很少用到）：把任务搬到新机器
  （[advanced-recovery](references/advanced-recovery.md)）、修复被截断的任务
  （[paginated-history-repair](references/paginated-history-repair.md)）。

## 背后的模型

| 层 | 存在哪 | 换号意味着 |
|---|---|---|
| 凭据 | `auth.json` | 新 key 或新登录 |
| 路由 | `config.toml`（`base_url`、`openai_base_url`、`model`） | 新地址 |
| 历史 | rollout 文件和 `state_*.sqlite`，每个任务带一个 provider ID 标签 | 不动，但标签必须一直能解析 |

保持 provider ID 不变，只改凭据和路由。SSH 远端是独立的 `CODEX_HOME`，有自己的
`auth.json` 和 `config.toml`；到"存着你关心的那些任务"的那台机器上跑检查。

## 这个 skill 里沉淀的教训

- `codex login` 不校验 key。"Successfully logged in"说明不了任何事，`--probe` 才能。
- 在 PowerShell 里用管道把 key 传给 `codex login --with-api-key`，存进去的 key 前面会多一个
  看不见的 U+FEFF，之后每个请求都 401。`set-key` 能避开，`check` 能检出。
- 参数是 `--with-api-key`（从 stdin 读），不是 `--api-key KEY`。
- JSON 没有注释。把旧 key 用 `//` 注释掉会让 `auth.json` 直接坏掉，旧 key 请放进 `.bak-*` 备份文件。
- 写 `[model_providers.openai]` 会让整个配置加载失败，Codex 还会悄悄退回 ChatGPT 默认设置。
  想让内置的 `openai` 走中转站，用顶层的 `openai_base_url`。
- 改动会被覆盖（比如编辑器自动保存了旧缓冲区）。用 `--expect-tail` 验证，别想当然。
- 不同中转站提供的模型名不一样，`model` 以 probe 输出为准。

## 快速上手

~~~bash
python scripts/codex_fuel.py --probe                 # 现在什么生效、key 好不好用
python scripts/codex_fuel.py set-key                 # 换 key：备份、写入、复查
python scripts/codex_fuel.py --expect-tail ab12X --probe   # 确认换号真的生效了
ssh HOST python3 - --probe < scripts/codex_fuel.py   # 在 SSH 远端做同样检查，无需安装
~~~

## 它不做什么

- 找回 ChatGPT 云端对话，或迁移订阅、账号所有权、cookie、OAuth 状态。
- 发一条测试消息来证明切换成功（既花 token，又会追加一轮对话）。
- 在常规流程里改 SQLite 或 rollout 文件。文档里记录的两个例外都需要明确同意并先备份。
- 猜测 SSH 别名、路径或 provider 对应关系。

已在 Windows 11、codex-cli 0.146.0 和 0.155.0-alpha.2.6 上验证。SSH 路径、官号到官号的登录和
`--device-auth` 来自 CLI 自身帮助，尚未端到端实测，首次使用时请确认。

## 安装

仓库根目录就是 skill 目录，安装或复制为：

~~~text
~/.codex/skills/remote-codex-provider-switch/     # Codex（同时读取 agents/openai.yaml）
~/.claude/skills/remote-codex-provider-switch/    # Claude Code（直接读取 SKILL.md）
~~~

`codex_fuel.py` 需要 Python 3.8 及以上，只用标准库。搬历史任务的工具需要 Python 3.11 及以上。
Codex 会自动发现新增或变更的 skill；没出现的话重启应用。标识符 `remote-codex-provider-switch`
保持不变，已有安装不受影响。

## 验证

~~~bash
python3 -m unittest discover -s tests -v
~~~

## 许可证

[MIT](LICENSE)。
