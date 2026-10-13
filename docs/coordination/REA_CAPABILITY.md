# REA 能力接入与证据升级

REA 指 [morluto/rea](https://github.com/morluto/rea)（Reverse Engineer Anything）。它提供本地 MCP/CLI 分析入口，适用于已授权的二进制、Electron/JavaScript、.NET、APK、网页和运行行为取证；并不让模型本身自动变强，也不保证所有分析引擎已安装。对于有完整源码的普通架构问题，先用正常源码阅读工具；只有结论依赖发布制品、反编译或实际运行行为时才调用 REA。

## 本机接入状态（2026-10-13）

- Node `v24.18.0`；全局 `rea-agents@6.4.0`，`rea.cmd --version` 通过。
- Codex：`rea doctor --client codex --json` 的注册检查健康；备份 `C:\Users\love\.codex\config.toml.rea.backup`。
- Hermes：`rea doctor --client hermes --json` 的注册检查健康；备份 `F:\PythonProjects1\Hermes\config.yaml.rea.backup`。
- 当前 Codex 会话**尚未重连**，所以不能声称 MCP 工具已经在本会话可用；Hermes 也需重启/重连并实测工具调用。
- Ghidra/Hopper/IDA 引擎尚未在 REA doctor 中就绪，因此深度原生反编译未验收。静态 JavaScript/.NET 等不一定需要原生引擎，按具体操作检查。
- WorkBuddy、Qoder、CodeBuddy、GitHub Copilot 桌面扩展等客户端未逐个验收 REA MCP 配置；它们可先在有命令权限时使用全局 `rea.cmd`，不能因 CLI 已安装就声称桌面内工具可用。

## 军团调用规则

1. 明确目标、授权范围、要回答的问题和最便宜的证据。读取源码可解的问题不启动反编译。
2. 先查同一目标 hash/版本的已有报告，避免多 Agent 重复分析。需要 REA 时先检查当前客户端是否已有工具；工具不可用才运行 `rea.cmd doctor --client <受支持客户端> --json`，不要每轮重复 setup。
3. REA 结果按【实：实际运行/平台回表】【源：源码或反编译位置】【推：需实验验证的假设】分档。记录目标 SHA-256、工具与版本、证据 ID/路径、覆盖范围、未解问题；静态证据不能写成运行时已证。
4. 每项发现提出最小反证或对照实验。另一 Agent 独立复核后，才把可复用结论写入共享知识库与决策表，并用现有协作事件 ID 通报。REA 报告、聊天转述、已读都不等于 Agent ACK。
5. 每次重大新错误/新制品触发一次有界分析；静默时不轮询、不自动升级包、不自动运行目标。更新 REA 版本前先比较变更、固定版本并回归验证，不让远端最新版未经审查进入作战链。

## 已有可复用的昆仑案例

FlagGems 协作区 `competition/coordination/knowledge/WSLC_KUNLUN_CONTAINER.md` 与 `competition/coordination/XPU3_XRE5371_CONTAINER_DEEP_DIVE_20261013.md` 记录了 `wslc.exe` 容器 `azure_ellsworth` 内的 XPU 后端源码取证。这是源码级证据，不是 REA MCP 实测；不能为了“用 REA”重新跑一遍。该容器是昆仑编译门，不是 OCTO 服务栈。较新复测已证实镜像在管理员 session；普通会话的 `wslc ls/images` 空表是权限/存储隔离的预期表现。Agent 不代为提权，也不使用 `wslc run` 重拉镜像。

## 上线验收

- 重连 Codex/Hermes 后列出实际 REA 工具，并用**用户明确指定的已授权目标**运行一个最小、只读的分析；保留 Evidence ID 与局限。
- 为一个 WorkBuddy/Qoder 会话实测 `rea.cmd` CLI 可达，若它们支持自定义 MCP 再做独立注册预览与备份。
- 把同一证据事件投递到本地信箱，等目标 Agent 明确 ACK；检验另一 Agent 能复述结论及边界，而非只看到通知。
