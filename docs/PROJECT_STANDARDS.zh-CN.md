# AEP 项目体系、标准与规范 v1

本文是 Agent Evolution Protocol（AEP）的工程基线。它把“红皇后—鲶鱼—创造性破坏”从理念固化为可检查的开发、派工、接力、Provider 接入和发布规则。

## 1. 架构边界

```text
业务 Agent / 模型
        ↓ A2A/适配器
AEP 工作区与调度内核（Workspace、DAG、租约、事件、快照、ACK）
        ↓ MCP / Skill / Connector
执行 Provider（浏览器、UIA、终端、移动端、REA、第三方 Agent）
```

- 工作区是唯一事实源，Agent 会话只是可替换执行者；
- Provider 只提供执行能力，不拥有任务真相、工作区状态或完成判定；
- “已输入”“窗口出现”“HTTP 200”“工具可发现”都不是完成证据；
- 完成必须形成 `MESSAGE_VISIBLE → AGENT_ACK → RESULT_VERIFIED` 证据链；
- 任何跨工作区、跨会话、跨 Agent 的事件必须携带 `workspace_id`、`session_id` 和 `event_id`。

## 2. 状态与证据标准

Provider 与任务统一使用以下晋级语义：

| 等级 | 含义 | 可以做什么 |
|---|---|---|
| `declared` / L0 | 目录登记或能力声明 | 规划候选，不执行 |
| `probed` / L1 | 工具、版本、依赖探测通过 | 允许隔离探针 |
| `session-accepted` / L2 | 原窗口/标签/设备/会话唯一绑定 | 允许无副作用回读 |
| `task-tested` / L3 | 同事件真实派工并回读 ACK/结果 | 进入路由优先级 |
| `blocked` | 缺证据、会话歧义或资源冲突 | 只允许补证据，不盲重试 |
| `retired` | 过时、失败或被替代 | 不得自动重新启用 |

状态晋级必须写入回执和证据路径；任何 Agent 不得用自然语言自行声称晋级。

## 3. 开发与变更规范

1. 新能力先写 Schema/接口和验收门，再写适配器；
2. Schema ID 一旦发布不可复用，破坏性变更必须新版本；
3. 代码提交使用 Conventional Commits；一项逻辑变更对应一个可回滚提交；
4. 任何桌面动作先 `observe/readiness`，后输入，再验证发送和回读；
5. 不把临时日志、模型权重、登录态、Token、桌面截图写入版本库；
6. 长路径、Windows 编译、GPU/WSL 等经验必须进入可检索文档或命令记忆，不靠口头交接；
7. 依赖升级必须保留来源、版本、哈希、许可证状态和回滚版本。

## 4. 第三方 Provider 接入规范

第三方项目按照 `L0 → L1 → L2 → L3` 接入。目录见 [execution-provider-catalog.json](../examples/execution-provider-catalog.json)。

- L0：记录仓库、用途、运行时、限制和许可证核验状态；
- L1：在隔离环境探测版本、命令、MCP 工具和依赖；
- L2：绑定唯一原会话/浏览器 Target/PID/设备 ID，执行只读回读；
- L3：使用 AEP dispatch envelope 做一次真实任务，取得同事件 ACK 和结果证据；
- 没有许可证核验或来源哈希时，只能“引用/实验”，不得复制代码、打包发布或宣称兼容；
- Provider 的强弱由实测证据和失败类型评分，不由项目热度、Star 数或多数投票决定。

## 5. 红皇后—鲶鱼—创造性破坏落地规则

- 红皇后：外部 Provider、模型或平台变化必须产生有来源的变更事件；
- 鲶鱼：对“已接通”“已稳定”“已生产”的结论安排低成本独立反证；
- 创造性破坏：连续无法通过 L2/L3 或重复制造同类故障的 Provider 自动降级/退役；
- 反平庸：候选假设先隔离形成，事实共享不等于结论统一；
- 激励以可复现突破、提前发现风险、有效接管和节省额度为准，不奖励无证据的忙碌。

## 6. 发布与回滚

每个版本必须完成：

```text
代码审查 → Schema/文档检查 → 全量测试 → 运行时冒烟 → CHANGELOG
→ 版本号一致 → Git commit → annotated tag → 远程分支/Tag 推送
```

回滚只使用已发布 Tag，不在工作区执行破坏性 reset。发布报告必须列明：测试数量、未验收 Provider、已知限制、第三方来源和回滚 Tag。

## 7. 最小验收命令

```powershell
python -m pytest -q
pwsh.exe -File .\scripts\install-capability-runtime.ps1 -WhatIf
& .\.tools\agent-runtime\Scripts\python.exe -m pip check
& .\.tools\agent-runtime\Scripts\python.exe -m agent_evolution_protocol coord-provider-catalog
```

本规范与 [第三方执行生态接入策略](THIRD_PARTY_EXECUTION_ECOSYSTEM.zh-CN.md)、[能力运行时安装](CAPABILITY_RUNTIME_INSTALL.zh-CN.md)、[派工标准](DISPATCH_STANDARD.zh-CN.md) 一起构成当前工程基线。
