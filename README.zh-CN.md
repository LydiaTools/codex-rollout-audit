# Codex Rollout Audit

**找出哪组 Codex 会话文件占用了磁盘空间，不打开对话正文。**

Codex Rollout Audit 是一个只读命令行工具，用来统计本机 Codex rollout 文件大小，并根据会话元数据中的线程关系进行归组。

## 为什么做这个工具

Codex 仓库的公开问题中，已有用户报告本地 rollout 文件增长到几十或数百 GB，部分案例涉及子 Agent 与会话分支。这个工具回答一个更具体的问题：**磁盘上的字节分别落在哪组有记录关系的会话文件里？**

相关个案包括[本地 rollout 占用增长报告](https://github.com/openai/codex/issues/34337)和[子 Agent 占用报告](https://github.com/openai/codex/issues/34061)。它们是用户提交的问题，不代表发生率。扫描器以 Codex 仓库公开的 [rollout 元数据格式](https://github.com/openai/codex/blob/main/codex-rs/app-server/tests/common/rollout.rs#L1498-L1576)作为字段参考。

目前是早期预览。公开问题中的数字是用户提交的个案，不代表问题发生率；本工具也尚未在大量真实 Codex 安装环境中验证。

## 功能

- 扫描 .jsonl rollout 并统计文件系统记录的大小。
- 每个普通 JSONL 文件只读取首行元数据。
- 根据共享 session ID、父线程和 fork 关系归组。
- 按组显示总大小、父线程关联文件大小和 fork 关联文件大小。
- 统计 .jsonl.zst 的文件大小，但不解压；无法识别其线程关系。
- 跳过符号链接，不写入、删除、截断或上传文件。
- 支持文本和 JSON 输出；可选择显示本地相对路径。

## 快速开始

需要 Python 3.9 或更新版本；运行时只依赖 Python 标准库。

~~~sh
python -m pip install .
~~~

默认扫描 ~/.codex/sessions：

~~~sh
codex-rollout-audit
~~~

指定目录、查看更大的分组列表，或生成 JSON：

~~~sh
codex-rollout-audit --root "$HOME/.codex/sessions" --top 30 --show-paths
codex-rollout-audit --json > rollout-audit.json
~~~

报告只显示扫描目录的简短名称。使用 --show-paths 时会包含相对文件路径；分享前先检查内容。

## 合成样例

仓库内的演示夹具由虚构元数据组成，不含真实对话：

~~~sh
python -m codex_rollout_audit --root examples/demo/sessions --show-paths
~~~

示例输出见 [examples/demo/report.txt](examples/demo/report.txt)。

## 归组方式

工具读取 rollout 开头的 session_meta 记录及其 ID。共享 session ID 的文件会归为一组；如果父线程或 fork 所指向的 rollout 也存在于扫描目录中，则会继续合并到同一关系组。终端中显示的短组名是单向哈希，不是 Codex 会话 ID。

如果父文件不在扫描目录、元数据缺失或文件经过压缩，归组信息可能不完整。报告会指出无法确认的情况，不会猜测。文件大小是扫描时的文件系统大小；关系归因取决于实际可用的元数据。

## 范围与限制

- 这是本地磁盘审计工具，不提供清理功能，也不会改动或删除数据。
- 不读取消息、提示词、工具输出、附件或数据库内容。
- 不检查 SQLite/WAL、会话目录以外的日志或云端存储。
- 统计 .jsonl.zst 文件大小，但不会解压或判断其归属。
- 元数据关系不等于因果关系，不能证明某个 rollout 导致另一个文件变大。
- 不承诺能节省特定空间，也不假设所有 Codex 安装使用完全相同的文件布局。

## 反馈

提交问题时请附上操作系统、Codex 版本、运行命令和脱敏后的结果摘要。请勿上传会话文件或对话正文。Bug 与格式支持请求可使用 GitHub Issues。

## 许可

MIT。本项目为独立社区工具，并非 OpenAI 官方产品。
