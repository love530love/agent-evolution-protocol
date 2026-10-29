# Agent Evolution Protocol

红皇后—鲶鱼—创造性破坏：多 Agent 自治协作与进化体系。

[验证结果接力](docs/VALIDATION_HANDOFF.zh-CN.md)：实际测试的失败、成功和晋级受阻都会留下绑定成员与阶段的不可覆盖回执；接替者的 `brief` 自动带上同阶段失败证据。同一提案禁止并发验证，运行中断且无回执时阻止盲目重跑；`validation-runs --id <ID>` 可只读核查。

[接力归属与历史回执](docs/HANDOFF_OWNERSHIP.zh-CN.md)：区分目标认领、他人认领、过期和释放；保留历史 ACK，但不以此冒充完成或触发重发。

`coord-improvement reroute` 支持已证明尚未输入的订单受控改派：默认预览、显式应用、保留原事件和审计；不确定送达或已开始执行的任务不能直接改派。

`coord-improvement checkpoint` 保存绑定有效租约的中途成果说明和文件哈希；接替者的 `brief` 自动校验、获取同阶段检查点，减少上下文交接损失。检查点不等于执行停止确认。

`coord-improvement inspect-handoff --id <ID>` 按需只读诊断交接阻塞：汇总证据、检查点和租约问题及恢复步骤；不自动认领、不泄露租约 token。

调度遇到 `NO_ELIGIBLE_MEMBER` 会列出逐成员阻塞原因，区分能力未声明、在线记录不足、占用及复核独立性限制，不自动放宽这些条件。

改进订单禁用通用自动增援，并在桌面投递前核对有效认领；已认领则延后，租约损坏则停火，避免绕过改进流程生成重复任务。

[Windows 便携 Python 核心](docs/PORTABLE_PYTHON.zh-CN.md)：无需系统 Python，支持固定哈希在线/离线安装及 `./aep.ps1` 统一入口；核心不捆绑可选 MCP/浏览器/WSL。

[可选 MCP SDK 离线安装](docs/OFFLINE_DESKTOP_SDK.zh-CN.md)：固定 Windows CPython 3.13 的 30 个 wheel 与哈希，预下载后断开索引安装，独立入口 `./aep-desktop.ps1`；仍需完整 Python 创建 venv。

[依赖候选锁与审查](docs/DEPENDENCY_CANDIDATES.zh-CN.md)：`coord-dependencies` 只读检查 wheel，生成差异和许可证声明；候选锁可在新环境验证，不自动替换生产版本。

[依赖已知漏洞查询](docs/DEPENDENCY_VULNERABILITY_AUDIT.zh-CN.md)：`coord-dependency-audit` 显式开启 OSV 查询并保存逐包覆盖证据；未知、失败、分页及已知通报均返回非零，不把空结果当安全认证。

[SDK 实物对账](docs/SDK_ENVIRONMENT_INVENTORY.zh-CN.md)：`coord-dependency-inventory` 不启动目标解释器，比对锁与安装元数据，可核验 RECORD 文件哈希；引导包和未验证范围单独报告。

[原始 wheel 载荷对照](docs/WHEEL_PAYLOAD_COMPARISON.zh-CN.md)：`coord-wheel-compare` 用锁定归档的实际字节对照安装文件，不信任可一同改写的本地 RECORD；未知映射不静默放行。

[依赖证据一致性门](docs/DEPENDENCY_EVIDENCE_GATE.zh-CN.md)：`coord-dependency-gate` 核对漏洞、安装和 wheel 快照的锁哈希、环境、原始数据及有效期，防止误拼接旧报告；不授权部署。

[Orca 可选运行时适配器](docs/coordination/ORCA_RUNTIME_ADAPTER.zh-CN.md)：新增 `coord-orca` 原终端探测/绑定/读写预览，显式发送去重并拒绝未知结果重试；尚未真实宿主验收。

[团队执行与自进化契约 v1.0](docs/EVOLUTION_EXECUTION_PACT.zh-CN.md)：新增 `coord-improvement` 证据绑定的改进生命周期；不自动部署、不自行扩权。

`coord-improvement ingest` 可将完成/失败事件转为去重提案；`validate` 实际运行可信工作区的 unittest 并保存证据，通过后推进至 VALIDATED（非安全沙箱）。

`coord-improvement enqueue` 配合 `scripts/watch-improvements.ps1` 提供持久事件信箱与本地文件事件监听；离线积压可恢复，不消耗模型轮询额度，不自动执行事件中的命令。

`coord-improvement overview` 汇总积压、错误与待复核事项；监听器支持独占单实例及 `-Stop` 控制，停止后不会自行重启。

`coord-improvement pending|claim|complete|release` 提供接力工作项、限时认领与过期补位；token 和日志头检查拒绝陈旧完成，不代表 Agent 已被唤醒。

`coord-improvement schedule --request-file <成员能力名单>` 默认预览在线成员匹配；加 `--apply` 接入现有 commander 队列，按提案阶段去重。不直接发消息、不自动创建会话；监听器可选 `-RosterFile` 启用排队。

`coord-improvement delivery-status` 关联订单、ACK、认领和提案阶段；过时改进订单会在 commander 实际投递前被阻止，不把 ACK 当完成。

`coord-improvement retire` 可审计地退役“已过时且从未开始输入”的订单，释放路由名额；未知送达拒绝退役，不会自动换人重发。

认领后用 `brief --id <ID> --actor <成员> --token <token>` 核对证据；`renew` 可限额续租。`validate --token <token>` 在测试前后检查租约，过期结果不会自动晋级。

`coord-improvement policy --mode enforced --actor <操作者> --note <原因>` 可显式启用强制 token 晋级，覆盖旧 advance/validate 入口；默认仍兼容模式，模式切换留审计记录。

提案日志和事件去重索引采用完整写入后原子发布，避免半截正式记录；需要支持硬链接的文件系统，并发冲突不会覆盖已有证据。

```powershell
# Isolated workflow acceptance: real test subprocess, scripted review roles, no desktop messages
aep --coord-root .tools/acceptance coord-improvement self-test
```

This preserves a machine-readable report and evidence under a unique run directory. It does not change production policy or certify real Agent delivery/ACK. CI covers Ubuntu/Windows and Python 3.10/3.12.

[总体系与能力状态](docs/SYSTEM_OVERVIEW.zh-CN.md) · [协同推理工作流](docs/COLLABORATIVE_REASONING_FRAMEWORK.zh-CN.md) · [v0.4.0 发布说明](docs/releases/v0.4.0.md)

An evidence-driven governance protocol for multi-agent systems that need diversity without coordination collapse.

## Portable desktop capabilities (preview)

See the [portable architecture and delivery roadmap](docs/PORTABLE_CAPABILITY_ROADMAP.zh-CN.md) for dependency setup, DIY interfaces, implemented capabilities and future milestones.

```powershell
# PowerShell 7: preview first, then opt in to an isolated AEP + MCP SDK install
./scripts/bootstrap-desktop.ps1 -Python python.exe
./scripts/bootstrap-desktop.ps1 -Python python.exe -Install
```

This installs the protocol and optional SDK, not all third-party desktop servers. Provider sources are pinned in [the provenance manifest](examples/desktop-provider-sources.lock.json); offline bundles and clean-machine provider installers remain roadmap items.

A [single-provider installer preview](scripts/install-desktop-provider.ps1) now covers pinned sbroenne source builds, dependency preflight, local registration and tool discovery. Full build acceptance is pending .NET 10 availability; preview and `-WhatIf` do not install anything.

**New here?** Start with the plain-language [Chinese introduction](docs/INTRODUCTION.zh-CN.md), which explains the failure mode, design rationale, roles, one complete round, and a beginner walkthrough.

The design combines three forces:

- **Red Queen pressure** — respond to verified external change instead of manufacturing urgency.
- **Catfish challenges** — keep a bounded, event-triggered dissenter that can fund one cheap falsification test.
- **Creative destruction** — select candidates through preregistered evidence, not authority or majority vote.

## Why

Connecting more agents often improves message throughput while reducing cognitive diversity. Shared context creates anchoring, imitation, premature consensus, and compromise solutions that are difficult to attribute or falsify.

This project separates:

- a **coordination plane** for public facts, ownership, budgets, hashes, and verified results;
- a **competition plane** for isolated exploration, commit–reveal, falsification, and evidence-based selection.

## Core rules

1. The leader owns execution order, not technical truth.
2. Candidate hypotheses remain isolated until every cell commits.
3. A changed artifact or prediction creates a new candidate ID.
4. A catfish challenge must state an alternative, predicted signature, cheapest falsification, budget, and stop condition.
5. Safety vetoes are limited to compliance, security, irreversible damage, and data contamination.
6. Technical winners are chosen by reproducible experiments, never by vote count.
7. Ordinary messages never wake a model. Execution requires an explicit event or human action.

## Shadow CLI

The included standard-library CLI records local commit–reveal rounds. It does **not** call models, poll, use accelerators, modify candidate code, merge changes, or submit to external platforms.

```powershell
python -m agent_evolution_protocol init --round-id demo --task example --leader lead --cells alpha beta catfish --budget 10
python -m agent_evolution_protocol commit --round-id demo --card alpha.json
python -m agent_evolution_protocol status --round-id demo
python -m agent_evolution_protocol audit --round-id demo
```

Version 0.2 adds a tamper-evident event hash chain, ledger audit, enforced result budgets, explicit challenge/threat/decision events, and human-gated HOLD/resume.

## Coordination kernel

This repository also includes a local reference implementation for event-driven
multi-agent coordination:

- [collaboration evolution record](docs/coordination/MULTI_AGENT_COLLABORATION_EVOLUTION.md)
- [status board protocol](docs/coordination/MULTI_AGENT_STATUS_BOARD.md)
- [wake kernel](docs/coordination/MULTI_AGENT_WAKE_KERNEL.md)
- [presence and return-to-team notices](docs/coordination/PRESENCE_AND_RETURN.md)
- [any online agent can request a bounded handoff](docs/coordination/ANY_AGENT_CAN_DISPATCH.md)
- [human-style desktop fallback](docs/coordination/HUMAN_UI_FALLBACK.md)
- [Windows desktop MCP provider discovery and fallback planning](docs/coordination/DESKTOP_MCP_PROVIDERS.md)
- [shared Windows command memory](docs/coordination/COMMAND_MEMORY.md)
- [Windows build case index](docs/coordination/WINDOWS_BUILD_KNOWLEDGE.md)
- [browser capability routing](docs/coordination/BROWSER_CAPABILITY_ROUTING.md)
- [optional OCTO real-time integration](docs/coordination/OCTO_INTEGRATION.md)
- [REA evidence capability for the agent team](docs/coordination/REA_CAPABILITY.md)
- [Per-Agent MCP bundles and session-aware dispatch](docs/AGENT_MCP_BUNDLES.zh-CN.md)
- [Desktop-primary / headless-augmentation production architecture](docs/DESKTOP_PRIMARY_HEADLESS_AUGMENTATION.zh-CN.md)
- [Desktop-primary / headless-augmentation acceptance checklist](docs/acceptance/DESKTOP_PRIMARY_HEADLESS_ACCEPTANCE_20261029.md)
- [session affinity](docs/coordination/MULTI_AGENT_SESSION_AFFINITY.md)
- [anti-collapse protocol](docs/coordination/MULTI_AGENT_ANTI_COLLAPSE_PROTOCOL.md)
- [role contracts, independent Catfish reviews and evidence-based incentives](docs/coordination/EXPERT_EVOLUTION_REVIEW_20261015.md)
- [红皇后体系下的协同推理工作流：16 条方法的审议与完善](docs/COLLABORATIVE_REASONING_FRAMEWORK.zh-CN.md)
- [Red Queen / Catfish / Creative Destruction retrospective](docs/coordination/RED_QUEEN_CATFISH_CREATIVE_DESTRUCTION_RETROSPECTIVE_20260928.md)

Reference code lives under `src/agent_evolution_protocol/coordination/`. It is
designed for local, event-driven wakeups: ordinary messages do not start models;
explicit wake requests are audited and routed through adapters.

## Unified local coordination CLI

Install locally and initialize a coordination root:

```powershell
pwsh.exe -File .\scripts\bootstrap.ps1 -CoordRoot coordination
```

The `aep` command is an alias for `agent-evolution` and exposes both planes:

- governance plane: `init`, `commit`, `reveal`, `challenge`, `threat`, `decide`, `audit`;
- coordination plane: `doctor`, `workspace-init`, `inspect`, `route`, `join`, `coord-status`, `coord-digest`, `coord-send`, `coord-wake`, `coord-wake-status`, `coord-archive-stale`, `coord-session-set`, `coord-claim`, `coord-release`, `coord-task-state`, `coord-onboarding`, `coord-guided-retry`;
- recovery kernel: `kernel-status`, `kernel-lease-acquire`, `kernel-lease-release`, `kernel-op-reserve`, `kernel-op-transition`, `kernel-session-bind`, `kernel-continuation-plan`, `kernel-checkpoint`.

The coordination CLI is local-file based and does not call models. It is meant
to make onboarding deterministic: a new agent reads the current digest and
sticky-session policy before doing work, and missing session IDs become a
visible handoff problem instead of silently spawning new chats.

See the Chinese [coordination CLI quick guide](docs/COORDINATION_CLI.zh-CN.md), [agent onboarding contract](docs/AGENT_ONBOARDING_CONTRACT.zh-CN.md), [guided retry policy](docs/GUIDED_RETRY_POLICY.zh-CN.md), [`aep inspect` runbook](docs/RUNBOOK_INSPECT.zh-CN.md), [joining any task](docs/JOINING_ANY_TASK.zh-CN.md), [natural-language join](docs/NATURAL_LANGUAGE_JOIN.zh-CN.md), and [`aep route` requirements and development plan](docs/ROUTE_DEVELOPMENT_PLAN.zh-CN.md).

See [the beginner introduction](docs/INTRODUCTION.zh-CN.md), [the protocol](docs/PROTOCOL.md), [the evolution history](docs/EVOLUTION.md), and [the Chinese README](README.zh-CN.md).

## Join a workspace in plain language

Initialize the shared project once:

```powershell
aep workspace-init --workspace 'K:\PythonProjects5\MyProject' --agents-md
```

Then tell a new agent: “Join this workspace and work on T125.” An agent that reads
`AGENTS.md` follows `AEP_JOIN.md`; otherwise point it to that file. A command-capable
agent can translate the original request into a read-only plan:

```powershell
aep route --agent hermes --workspace 'K:\PythonProjects5\MyProject' --task T125 --intent 'Join this task' --markdown
```

The router covers orientation, task joining, takeover, review, independent
exploration, shared-resource operations, recovery, wake requests, reporting and
file-only participation. Task IDs also support general work such as `BUG-42` and
`DOCS-INTRO`. Optional `.aep/routing.json` keywords adapt it to project vocabulary;
decision traces explain matches and ambiguity. Routing does not acquire ownership,
execute suggested commands or wake another model.

Existing sessions are preferred, with digest-first and checkpoint-based handoff.
An adapter must support actual session resumption or wake delivery; accepting a
new agent name does not provide that adapter. Agents without a CLI can read the
workspace entry and available status files, then request command-capable help.
See the [scenario router guide](docs/SCENARIO_ROUTER.zh-CN.md) for examples and limits.

## Status

Experimental shadow tooling. Keep irreversible operations and production automation under human approval until your own pilots pass.

## License

Apache-2.0.

接力部署与真实验收：[docs/AGENT_HANDOFF_AND_ACCEPTANCE.zh-CN.md](docs/AGENT_HANDOFF_AND_ACCEPTANCE.zh-CN.md) · 健壮性调研：[docs/ROBUSTNESS_RESEARCH_20261028.zh-CN.md](docs/ROBUSTNESS_RESEARCH_20261028.zh-CN.md)

[协作机制审计与目标](docs/COOPERATION_AUDIT_20261028.zh-CN.md)

投递证据审计：`aep --coord-root coordination coord-delivery-audit --summary-only`。它只读显示每个 Agent 下一项需要补齐的证据，不发送消息、不唤醒模型、不自动重试。
# Agent 接入与操作规范

新加入 Agent 请先阅读 [`skills/aep-agent-onboarding/SKILL.md`](skills/aep-agent-onboarding/SKILL.md)，并使用 [`templates/AGENT_JOIN_PROMPT.md`](templates/AGENT_JOIN_PROMPT.md) 完成接入。桌面投递、会话切换、失败重试和证据判据见 [`docs/AGENT_OPERATION_GUIDE.zh-CN.md`](docs/AGENT_OPERATION_GUIDE.zh-CN.md)。

