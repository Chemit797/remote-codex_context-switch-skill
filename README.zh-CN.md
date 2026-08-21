# Remote Codex Provider Switch

[English](README.md) | 简体中文

通过 SSH 连接远端 Codex App Server 时，安全地更换 provider 或账号，同时保留桌面端当前任务、thread 历史和后续对话上下文。

可以把它理解成“车子继续行驶，只更换燃料”：

```text
桌面 Codex -> SSH -> 远端 Codex App Server
                         原 provider/账号
                                  ↓ 切换
                         目标 provider/账号
```

支持任意组合：

- 官方账号 -> 官方账号
- 官方账号 -> 中转站
- 中转站 -> 官方账号
- 中转站 -> 中转站

## 为什么需要它

桌面端 Codex 和远端 SSH Codex 是两个独立运行时。修改桌面端配置，不一定会改变远端 App Server；远端登录成功，也不代表旧对话和后续请求已经使用了新的账号。

provider 状态可能同时存在于：

- `~/.codex/config.toml`
- 环境变量和进程启动参数
- JSONL rollout/session 元数据
- 旧对话后续的 `session_meta` 记录
- SQLite App Server 状态
- App Server 内存缓存

这个 skill 会检查并迁移这些层，不重建对话，也不会为了测试路由而偷偷发送模型请求。

## 功能

- 识别当前 source provider/account 和目标 target provider/account；
- 不调用 `turn/start` 或测试问题，验证远端实际路由；
- 修改前创建带时间戳的完整备份；
- 保留 thread ID、turn ID、消息、工具输出、项目路径和历史字节；
- 迁移 rollout 文件和 SQLite 中的 provider 元数据；
- 检查会让 App Server 恢复旧 provider 的后续 `session_meta`；
- 检查 provider 特有的 response ID、推理记录和工具记录；
- 仅重启目标远端用户自己的 App Server/proxy 进程；
- 验证同一个桌面任务仍能打开，并且后续继续对话会使用目标账号；
- 只有在用户明确授权、完成备份并通过关系检查后，才处理归档对话删除；
- 验证失败时从备份恢复，而不是删除或重建上下文。

## 一个重要区别

“保留历史”与“可以继续对话”是两个不同条件：

1. 原任务和历史 turn 必须仍然可读；
2. 目标 provider 必须接受这份历史格式，才能追加新 turn。

某些中转站会写入 provider 特有的 ID、加密 reasoning 记录或工具调用记录。只有在明确知道目标 provider 格式时，skill 才会使用按类型处理的兼容迁移。如果目标 provider 无法原样继续，它仍会保留完整历史，并明确报告不兼容原因，不会把“能打开”误报成“能继续”。

## 安装

将整个 skill 目录复制到：

```text
~/.codex/skills/remote-codex-provider-switch/
```

目录应包含：

```text
SKILL.md
README.md
README.zh-CN.md
DESCRIPTION.md
agents/openai.yaml
references/operational-playbook.md
```

Windows 默认路径为：

```text
%USERPROFILE%\.codex\skills\remote-codex-provider-switch\
```

## 使用示例

只检查远程连接，不修改配置：

```text
使用 $remote-codex-provider-switch，检查远端 SSH Codex 当前实际使用的 provider 和账号。
```

在授权后切换，同时保留当前任务：

```text
使用 $remote-codex-provider-switch，把远端 Codex 从当前 provider/账号切换到目标 provider/账号。保留同一个 thread ID 和完整历史，先备份，不发送测试 turn，并验证后续继续对话确实走目标账号。
```

如果目标是中转站或另一个账号，应提供真实的目标配置。不要凭空猜 provider 名称、endpoint、API key 或登录命令。

## 安全原则

- 每次修改前先只读盘点；
- 有正在运行的 turn 时先等待或停止，不能边追加边改 rollout；
- JSONL 通过原子替换修改，保留无关字节；
- 不使用 `turn/start` 作为路由测试；
- 不会把“删除归档对话”扩大成“删除未归档历史”；
- 不使用宽泛的 `killall codex`，先确认进程归属和精确 PID；
- 报告中隐藏密钥；
- 变更后的验证失败时停止继续生成并进入备份恢复流程。

## 文档

- [SKILL.md](SKILL.md)：Codex skill 入口和调用规则；
- [English README](README.md)：英文项目首页；
- [项目描述与命名候选](DESCRIPTION.md)：中英文 GitHub 简介和 skill 名称建议；
- [运行手册](references/operational-playbook.md)：provider 检测、迁移、兼容性、归档、验证和恢复。

## 限制

这个 skill 不能强行让一个不支持目标历史格式的 provider 接受旧记录。它可以保留原始对话并定位阻塞记录，但精确继续对话可能需要目标 provider 提供导入格式，或人工重建一份上下文。它也不会替你判断账号的实际计费、额度或套餐权益，只能根据远端主机上可见的路由证据进行验证。

## License

当前 skill 目录尚未声明许可证。公开发布前，请根据你的分发意图添加 MIT、Apache-2.0 或其他明确许可证。
