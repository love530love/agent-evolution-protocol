# `aep route` 需求调研与开发计划

本文把“新 agent 找到项目/任务之后，如何自动接入协作”的需求整理成可开发、可测试的计划。调研采用专家团和用户代表视角建模；不假设所有 agent 都支持 CLI，也不假设所有协作都是继续写代码。

## 参与视角

用户代表是模拟角色，依据用户在本项目中的真实反馈推演，并非已进行外部用户访谈。列出的专家领域是审议视角，不代表每个领域均召集了独立参与者。

专家团：

- 协作协议架构师：关注场景边界、claim/lease/hand-off 的一致性。
- 安全与副作用审计专家：关注浏览器、上传、发布、删除、支付等不可轻易重放的动作。
- 多 agent 产品设计师：关注人类只说一句自然语言时，新 agent 能否自己判断下一步。
- 失败恢复工程师：关注卡住、失联、UNKNOWN 操作、checkpoint 和 takeover。
- 治理机制专家：关注 Red Queen、Catfish、Creative Destruction 不被协作便利性稀释。

用户代表：

- 项目 owner：想快速知道“现在什么情况”，不想解释协议术语。
- 新加入 agent：只知道当前文件夹和一句人话，需要最小可执行路径。
- 接手 agent：需要安全接管别人未完成的任务，不能误重放外部写动作。
- 审查 agent：只想 review 或挑战假设，不想抢实现任务。
- 低能力 agent：不能运行命令，只能读文件，也要知道自己能做什么、不能做什么。

## 主要痛点

- 自然语言入口太少：`join` 能加入，但不能区分“旁观、接手、审查、唤醒、并行探索、外部提交”等场景。
- 任务发现之后容易误 claim：新 agent 看到任务 ID 后可能直接抢写。
- 外部副作用风险高：浏览器、上传、发布、删除、支付等动作必须先 lease 和 reserve operation。
- 卡住/失联不等于失败：沉默可能是等待人类、等待外部系统、或正在执行高风险动作。
- 审查和实现混在一起：review/catfish 应默认只读，不能默认 claim 主任务。
- commit-reveal 没有自然语言入口：人类说“你们各自想方案”时，需要自动进入隔离探索。
- CLI 能力不一致：有些 agent 只能读文档，不能执行 `aep` 命令。

## 场景分类

`aep route` 输出 `scenario`，当前第一版覆盖：

- `project-orientation`：加入项目但没有具体任务。只读态势，推荐可接入任务。
- `task-join`：接入具体任务。先 inspect/digest/join，再 claim-if-free。
- `takeover`：明确接手他人任务。先查 owner、checkpoint、stale 证据，不抢活跃 claim。
- `review-only`：审查、评审、鲶鱼挑战。只读主任务，可提出一个低成本证伪。
- `isolated-exploration`：并行候选方案。进入 commit-reveal，不提前读取竞争假设。
- `shared-resource-lock`：浏览器、上传、提交、发布、删除、支付等共享或外部写动作。先 lease，再 reserve operation。
- `stalled-recovery`：诊断卡住/失联。先确认 task-state/checkpoint/UNKNOWN 操作，再恢复。
- `wake-agent`：通知或唤醒其他 agent。普通消息不启动模型，使用显式 wake。
- `status-report`：只汇报当前情况。不得 claim 或改变 ownership。
- `cli-less-fallback`：agent 不支持 CLI。读文件、报告限制、请求命令型 agent 帮助 claim/lease。

## 路由输出契约

JSON 输出使用 `agent-evolution-route-v1`。以下是结构示例，字段值和可执行建议以当前 CLI 输出及协作状态为准：

```json
{
  "schema": "agent-evolution-route-v1",
  "agent": "codex",
  "intent": "请加入这个工作区协作，接入 T125",
  "scenario": "task-join",
  "task": "T125",
  "target_agent": "",
  "workspace": "...",
  "coord_root": "...",
  "kernel_root": "...",
  "mode": "digest-first",
  "description": "Join a concrete task and claim only when the task is not already owned.",
  "claim_guidance": {
    "claim_available": true,
    "command": "aep --coord-root ... coord-claim --agent codex --task T125"
  },
  "hazards": {
    "active_leases": 0,
    "unknown_operations": 0,
    "inflight_operations": 0,
    "latest_checkpoints": []
  },
  "blockers": [],
  "allowed_actions": ["read", "inspect", "digest", "claim-if-free"],
  "forbidden_actions": ["create a new session by default"],
  "first_commands": ["aep ..."],
  "runbook": "docs/SCENARIO_ROUTER.zh-CN.md",
  "operator_notes": ["This route is advisory and has no side effects."]
}
```

Markdown 输出用于只能读自然语言的 agent。

## 治理衔接

- Red Queen：`route` 只响应显式人类意图、任务状态、claim、checkpoint、UNKNOWN 操作等可见事实，不制造紧急状态。
- Catfish：`review-only` 是受保护的低成本证伪入口；默认不 claim 实现任务，不做外部写动作。
- Creative Destruction：`isolated-exploration` 把“各自想一个方案”映射到 commit-reveal，先独立形成候选，再用证据淘汰。

## 开发计划

第一阶段：

- 增加 `aep route --agent <agent> --intent "<自然语言>"`。
- 支持 JSON 和 Markdown 输出。
- 自动抽取任务 ID 和目标 agent。
- 输出允许动作、禁止动作、第一组命令、claim 指引、session/lease/UNKNOWN/checkpoint hazard 摘要、治理说明。

第二阶段：

- 让 `workspace-init` 生成的 `AEP_JOIN.md` 推荐 `aep route` 作为自然语言入口。
- 为场景词表增加可配置覆盖项。

第三阶段：

- 增加 takeover 的 stale 判断和 checkpoint 摘要。
- 增加 lease 类型自动选择，如 upload/browser/payment。
- 增加 route 决策 trace，便于复盘误路由。

## 本轮实现与审议记录（2026-10-07）

第一阶段及工作区接入文档已实现。独立复审发现并修复组合意图误路由：显式不执行优先、上传状态汇报保持只读、并行上传仍要求资源租约。移除并行开发造成的多套重复路由实现，保留统一入口。

本轮没有开展外部访谈；需求来自已有用户反馈与模拟角色审议。此次先交付第一版场景入口，将可配置词表、精细 stale 判定、资源类型自动选择和决策 trace 列入下一轮。唤醒结果依赖接收方适配器，route 本身仅生成指引。

## 完整开发与验收范围（2026-10-08）

用户要求一次完成已列开发事项。本轮按独立文件分工并行实现：分类专家负责词表、否定/冲突和决策解释；恢复专家负责持有者时效、检查点和共享资源建议；用户代表视角负责接入说明、需求覆盖和边界审议；集成方负责 CLI、通用任务标识、工作区发现及完整回归。用户代表仍为基于真实反馈的模拟审议，没有开展外部真人访谈。

| 需求 | 真实痛点或场景 | 本轮交付与验收口径 |
| --- | --- | --- |
| R01 一句话接入 | 新 agent 只打开同一主目录或辅助目录，不了解协议 | `workspace-init` 生成短入口；`route --workspace` 能定位目标工作区配置 |
| R02 任意任务 | 任务可能是赛题、缺陷、报告或一般项目工作 | 支持安全通用任务 ID，不限 `T125`；显式 `--task` 和工作区默认任务可接入 |
| R03 少开窗口 | WorkBuddy 收到消息就建新会话，长程上下文断裂 | 输出会话亲和与续接建议；缺失会话绑定可见，不把新增窗口当默认恢复手段 |
| R04 按需上下文 | 全量聊天历史消耗上下文，短提示又遗漏细节 | 先 digest/当前状态，再读取任务相关检查点与证据；不复制全部聊天 |
| R05 意图边界 | “只审查上传”“汇报提交情况”被误当执行 | 固定只读/否定规则优先；返回候选、歧义和决策 trace；含糊请求先只读 |
| R06 项目词汇 | 不同团队对接手、认领、审查有不同叫法 | `.aep/routing.json` 可追加或替换场景文本词表；无效配置明确报错 |
| R07 接手恢复 | 卡住、等待和失联混为一谈，重复提交造成损失 | 结合持有者状态、更新时间、checkpoint、UNKNOWN 操作给恢复建议；过期只是复核依据 |
| R08 并发资源 | 多 agent 同时争用浏览器或上传入口 | 识别资源类别、提示租约和操作登记；真实争用仍由内核获取租约时处理 |
| R09 能力差异 | 有的 agent 无 CLI、无多目录、无 resume 能力 | file-only 指引、显式根目录、能力事实回执；不伪造认领或唤醒成功 |
| R10 保留多样性 | 信息共享后方案趋同，集体重复失败 | 审查、独立探索与执行分流；保留有界鲶鱼挑战和 commit–reveal 指引 |

代码验证覆盖默认词表和自定义词表、组合意图、通用 ID、从项目外选择工作区、持有者时效、检查点以及资源建议。

### 交付验收记录（2026-10-08）

三阶段开发事项均已实现。完整回归 `python -m pytest -q`：**50 passed**；`git diff --check` 通过。测试保留在 `tests/test_routing.py`、`tests/test_route_state.py`、`tests/test_route_cli.py`，并扩展既有协作测试以验证自定义 agent 能在状态板中被发现。

真实本地 `aep` CLI 临时工作区演练通过：路径同时包含空格和单引号；初始化普通任务 DOCS-INTRO；新 agent 接入并认领；逐条执行生成的 join/inspect/digest 命令；绑定演练会话后返回 RESUME_EXISTING；另一 agent 接手时识别 active owner；只审查上传方案保持 review-only。该演练没有启动外部模型、打开新聊天或提交外部业务操作。

本轮修复还包括：上轮清理误删的 ProtocolError 异常类、重复路由实现、读取路由时意外创建内核目录、缺失 owner 文件被误判可认领、工作区外配置定位、PowerShell 路径引用、多个任务 ID 的消歧，以及 file-only 回执夸大执行状态的问题。

协作记录：文档与用户视角 subagent 完成文档和最终接口审议；分类与恢复 subagent 遭遇账户用量限制后，由主 agent 接回开发并完成集成。未把未完成的 subagent 工作计为已交付，也未开展外部真人访谈。

### 配置和兼容契约

工作区配置仍为 `.aep/workspace.json`；可选路由词表为 `.aep/routing.json`，格式见[使用说明](SCENARIO_ROUTER.zh-CN.md)。已有工作区无需迁移历史任务。新增任务 ID 使用 ASCII 字母、数字、下划线或连字符，首字符为字母或数字，最长 64 字符，规范为大写，拒绝 Windows 设备名。自然语言常见编号可自动抽取，其他任务名用 `--task` 明确指定。

新 agent 的标识同样采用安全 slug，并规范为小写。接受名字仅表示能在文件协作层识别参与者；实际执行、原会话续接和唤醒仍取决于该 agent 的适配器能力。

路由继续保持只读、无模型调用、无后台轮询。输出命令是建议，调用者需依据当前状态及用户意图执行。场景分类、`confidence` 或 stale 结论都不能扩大任务授权。

### 本地验收与外部验收

本地验收包括 CLI 输出、决策解释、配置发现与拒绝、工作区入口、状态恢复建议及既有协议回归。外部验收另指 WorkBuddy/Qoder/Hermes 等真实应用的适配器触达、旧会话续接和执行回执。前者通过不能证明后者通过；本轮不宣称已对这些桌面应用逐一完成真实唤醒。

## 验收用例

- 新 agent 听到“请加入这个工作区协作”时，得到 `project-orientation`，不 claim。
- 听到“接入 T125”时，得到 `task-join` 和 claim-if-free 指引。
- 听到“审查 qoder 的方案”时，得到 `review-only`，禁止 claim 实现任务。
- 听到“接手 qoder 卡住的 T125”时，先 takeover/stalled 恢复，不重放未知外部动作。
- 听到“通知 qoder 看一下 T125”时，输出 `coord-wake`，不默认新建会话。
- 听到“浏览器上传提交”时，输出 lease 和 operation reserve 指引。
- 听到“不支持 CLI”时，输出 file-only 兼容模式。
