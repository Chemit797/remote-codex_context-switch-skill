# 远端 Codex 上下文恢复

[English](README.md) | 简体中文

## 一句话说明

当你在本地使用 Codex，并通过 SSH 连接远端服务器上的 Codex 时，这个 skill
可以帮助你安全更换远端使用的账号或中转站，同时尽量保留原来的对话和上下文。

简单说就是：**换账号或换中转站，旧对话保留——相当于换号只是给车换燃料，换了继续开**

支持这些常见切换：

- 官号 → 官号
- 官号 → 中转站
- 中转站 → 官号
- 中转站 → 中转站

这里的“官号”指直接使用 OpenAI/ChatGPT 登录；“中转站”指在 Codex 中配置的
第三方兼容 provider。它也适用于同一台机器上只更换 Codex 账号或 provider，
以及把选定的本地对话迁移到另一台机器或另一台 SSH 主机。

前提是旧对话仍保存在原来那台机器的 `CODEX_HOME` 中。这个 skill 不能从云端
找回本地已经不存在的对话，也不会复制登录凭据、API key 或 cookie。

## 它实际帮你做什么

它会先找出旧对话使用的 provider，再根据你明确确认的映射修复兼容配置；如果
需要换机器或换 SSH 主机，它可以只打包并恢复你选中的对话和附件。整个过程不会
为了“强行恢复”而直接改写 Codex 的 SQLite 数据库。

为避免破坏已有安装，skill 的调用名仍保留为
**remote-codex-provider-switch**。但实现已经大幅修正：它不再声称可以靠
改写 rollout 元数据或 SQLite，让任意旧对话都安全地在另一个账号下继续。

## 能做什么

- 只读盘点本地 Codex JSONL 任务文件，不输出任务正文；
- 打包用户明确选定的任务、无歧义索引记录、完整性元数据，以及可选的、
  已知且属于该任务的附件；
- 只向关闭中的、干净的目标 CODEX_HOME 恢复已经验证的 bundle；
- 检测旧任务中的 provider ID；只有用户明确确认源到目标的映射后，才可
  添加受限的无密钥兼容别名，包括安全映射到 Codex 内置 `openai` provider；
- 使用目标机器已安装 Codex CLI 的受支持迁移路径，单独进行需要授权的
  可见性检查。

SSH 主机也有自己的本地 CODEX_HOME。桌面端和远端主机的任务存储、认证、
provider 设置彼此独立。应在拥有任务文件的主机上运行辅助脚本，或使用用户
明确批准的安全挂载路径。

~~~text
桌面 Codex  <->  SSH 远端 Codex 主机  ->  远端 CODEX_HOME
本地 CODEX_HOME                               远端任务历史
~~~

## 它刻意不做什么

- 不迁移 ChatGPT 云端对话、订阅、账号所有权、登录状态、auth.json、API
  key、cookie 或完整的源 config.toml；
- 不直接修改 state/history SQLite 数据库，也不手工重写 JSONL rollout；
- 不合并到已有的目标任务存储、不覆盖任务、不递归复制磁盘上所有附件；
- 不根据 provider 名称、endpoint 或账号标签猜测映射；
- 不为了验证恢复或路由而发送测试提示词；
- 不承诺目标账号一定能原生继续所有包含 provider 特有记录的旧历史。

## 安全流程

1. 阅读 [SKILL.md](SKILL.md) 和
   [恢复协议](references/recovery-protocol.md)。
2. 盘点源和目标。报告仅限任务 ID、JSONL 有效性、附件覆盖、索引歧义、
   目标任务数和缺失 provider ID 等安全元数据。
3. 正确判断场景：
   - **同一主机、同一个 CODEX_HOME，只换账号/provider**：保留现有本地
     任务文件，绝不能把它打包后再恢复到自己。先通过正常 Codex 登录流程
     登录目标账号；如有必要，只在用户明确确认后修复旧 provider 别名，再
     检查迁移资格。
   - **新机器或新的远端主机**：在源端创建并验证选择性 bundle，经用户批准的
     私密通道传输明文 bundle，只恢复到干净目标。
   - 如果本地任务和归档都不存在，本仓库不能从另一个 ChatGPT 账号取回仅存在
     于云端的历史。
4. 先检查受支持的迁移命令，再决定是否允许写入。若命令不可用或任务不符合
   资格，应保留验证过的 bundle 并停止，不能修改 SQLite 强行恢复可见性。

## 命令

从已安装的 skill 目录运行辅助脚本：

~~~bash
python3 scripts/codex_account_recovery.py inventory \
  --source /path/to/source/.codex \
  --target /path/to/target/.codex \
  --json
~~~

用户明确确认旧 provider 到当前 provider 的映射后，可将旧任务映射到内置
OpenAI provider：

~~~bash
python3 scripts/codex_account_recovery.py provider-alias \
  --config /path/to/target/.codex/config.toml \
  --legacy-provider LEGACY_ID \
  --active-provider openai \
  --apply \
  --json

codex doctor --json -c 'model_provider="LEGACY_ID"'
~~~

生成的别名不会读取凭据或写死 endpoint，而是复用目标安装当前的 ChatGPT 或
API key 登录；顶层默认 provider 保持不变。

创建并验证一个私密、无凭据的 bundle：

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

关闭目标 Codex Desktop 或 App Server 后，只恢复到专用的空目标：

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

随后使用目标机器上受支持的迁移检查：

~~~bash
CODEX_HOME=/path/to/target/.codex \
  codex migrate-rollouts --thread TASK_ID --json
~~~

只有得到明确授权后，才为最后一条命令增加 --apply。provider-alias 的命令和
判断规则见 [SKILL.md](SKILL.md)。

## 安装

仓库根目录就是 skill 目录。把它安装或复制到：

~~~text
~/.codex/skills/remote-codex-provider-switch/
~~~

辅助脚本需要 Python 3.11 或更高版本。Codex 通常会自动发现新/更新的 skill；
若没有显示，请重启应用。

## 验证

~~~bash
python3 -m unittest discover -s tests -v
~~~

测试覆盖了选择性附件恢复、重复索引、内置 OpenAI 别名、provider 别名限制、
路径穿越/符号链接/硬链接拒绝、保留 JSON 原始字节的 token 重映射、回滚，
以及 Windows manifest 路径。

## 许可证

[MIT](LICENSE)。
