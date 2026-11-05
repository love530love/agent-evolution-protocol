# AEP 项目上下文

Agent: hal
时间: 2026-11-05

## 项目概览

**Agent Evolution Protocol (AEP)** — 证据驱动的多 Agent 自治协作与反协调崩溃协议。

- **版本**: 0.6.21
- **路径**: K:/PythonProjects5/agent-evolution-protocol
- **核心设计**: 红皇后 — 鲶鱼 — 创造性破坏

## 核心概念

1. **双平面架构**:
   - 协调平面: 公共事实、所有权、预算、哈希、验证结果
   - 竞争平面: commit-reveal 隔离探索、证伪、证据选择

2. **Leader 负责执行顺序，不垄断技术真理**
3. **鲶鱼挑战**: 必须提出替代假设、预测签名、最便宜证伪方法、预算和停止条件
4. **安全否决**仅限合规、安全、不可逆损害和数据污染
5. **技术胜负由可复现实验决定，非投票**
6. **普通消息不唤醒模型**，执行需要显式事件

## hal 已执行的操作

- ✅ 读取 README.md, AEP_JOIN.md, AGENTS.md, CHANGELOG.md
- ✅ 安装检查: Python 3.10, PYTHONPATH=src 可导入
- ✅ `aep route`: 匹配 project-orientation
- ✅ `aep inspect`: 获取协作区完整状态
- ✅ `aep coord-digest --agent hal`: 绑定 hal 会话策略 (sticky, forbid-by-default)
- ✅ `aep join --agent hal --task AEP-ROBUSTNESS-20261028`: 加入任务
- ✅ `aep coord-onboarding`: 获取 onboarding 指令
- ✅ `aep coord-claim`: 任务已被 codex 拥有 (phase: needs-review)
- ✅ `aep coord-delivery-audit --summary-only`: 64 messages, 41 CLIENT_BOUND 缺失

## 协作区当前状态

- claims: 13
- task_states: 11 (9 stale)
- unread_total: 60
- pending_wake_receipts: 3
- missing_sticky_session_ids: 14 agents

## 关键路径
- 命令入口: `src/agent_evolution_protocol/cli.py`
- 协调根: `coordination/`
- 工作区配置: `.aep/workspace.json`
- 任务状态: `coordination/task_states/`
