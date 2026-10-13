# 多 Agent 作战升级综合决策（2026-10-14）

本文件逐条回应指挥层的分析诉求，并给出可执行的升级路线。所有结论均基于本仓库 `src/agent_evolution_protocol/coordination/` 下**已提交代码**的事实，不靠脑补。

> 一句话定调：**机制层面你要的能力几乎都写完了，卡住的是客户端通道实机验收，不是协议缺位。** 下面把"已具备 / 已验收 / 待锚定 / 待开发"四档拆开，避免再用"全员已就绪"这种总括措辞。

---

## 0. TL;DR（最该先看）

| 你的诉求 | 现状 | 下一步 |
|---|---|---|
| 唤醒任意 agent / 代替我双击打开并发送指令 | `commander.dispatch` + `ui_fallback.run` 已实现"定位→发令→等 ACK"全流程 | 给每家建 `ui_profiles/*.json` 并锚定 PID |
| 双向沟通 / 读回 ACK / 不冒称已接单 | `ui_fallback.run` 用 challenge-response nonce（校验串倒序）防"自己 ACK 自己" | Codex 外尚未实机闭环 |
| 跨 agent 感知彼此会话 | `commander.perceive` + `transcript_bus` 已把各窗口全文进共享总线 | 补 Electron 三家的可读采集 |
| 无人值守自动推进 | `commander.watch` / `tick` 已是常驻编排器（感知→查 ACK→重投未完成订单→停火） | 加"换人 / 多波次"编排 |
| 会话同步日志（本地不脱敏） | 本地明文已存 `coordination/` 下，`.gitignore` 已隔离，不会误提交 | 开工前强制 `transcript_bus digest` |
| windows-mcp（mcp-windows） | 本机已有等效且已验收的 UIA/键鼠/OCR 层 | **不进主通道**，仅作 POC 对照 |
| 积分 / 竞赛 / 奖惩 / 自动进化 | `legion_ledger.py` + `LEGION_COMBAT_PACT` 已落地 | 强化"自动进化、拒绝躺平"条款 |
| 军团作战复盘（28 次额度失败过半） | 有方法论框架，缺数据回填 | 见第 7 节 |

---

## 1. 引入 GitHub `windows-mcp`（mcp-windows）的可行性

**结论：不建议作为主通道引入，可作 POC 对照。**

- 该项目的本质是用 Windows UI Automation 按控件名操作窗口，并提供 MCP/CLI 入口。本仓库 `wake_adapters/uia_session.ps1` 已经是**同样能力的本地实现**（直接用 .NET `System.Windows.Automation`），且已在本机 Codex 桌面端实机验证"定位唯一窗口→定位输入框（ValuePattern）→写入送出→独立读回事件 ID→`new_session=false`"。
- 引入 mcp-windows 作为主通道的收益很小（重复能力），风险是：多一个第三方进程、需要高权限、许可证需审、本机兼容性未测。这与 `DESKTOP_WAKE_TRANSPORT_DECISION_20261012.md` 已定的"原生 API 优先、UIA 兜底、PAD 作执行器"完全一致。
- 若某家客户端的 UIA 控件缺失、OCR 又读不准，可把 mcp-windows 当成**替代探测手段**做一次对照实验，验证它能不能比现有 OCR 更可靠地定位输入框；通过后再决定。在此之前不写进高权限主通道。

> 同样的逻辑适用于 Microsoft UFO / AutoGenesis pywinauto MCP / Robocorp rpaframework：**它们的架构值得借鉴，但本机已有更可控的 UIA 层，不必替换现有协议。** 现有 `pad_flow.ps1` 已是 PAD 执行器，PAD 仍可保留为"手"。

## 2. 仿人操作 vs 底层唤醒的取舍

**结论：底层受支持的会话接口优先，仿人 UI 兜底；绝不拿 UI 模拟去绕过安全机制。**

- "底层"指客户端官方 Hook / API / CLI / 可归属的 CDP。优点：精确定位原会话、去重、可靠读回 ACK。缺点：各家不一、未必公开。
- "仿人"指 UIA（Codex 已闭环）/ 键盘+剪贴板+OCR（Electron 系兜底）。优点：覆盖无接口客户端。缺点：受窗口、焦点、界面变化影响，OCR 错字会误判消息方向。
- 本仓库已经把两者统一在 `ui_fallback.py` 的 `Driver` 抽象下：`PowerShellUiaDriver` 同时支持 `uia` 与 `ocr` 两种 transport，并在回执判定上**有意降级**——通用 OCR 不能可靠标注消息方向，所以即使屏幕上看到 `AEP-ACK` 也不升级为 `AGENT_ACK`（避免"自己发的请求被当成对方回执"）。这是安全设计，不是缺陷。

## 3. Power Automate for Desktop（PAD）

- 已接入：`wake_adapters/pad_flow.ps1` 是 PAD 执行器，`workbuddy.ps1` 在设置了 `AEP_PAD_WORKBUDDY_FLOW_ID` 时会转交 PAD 跑流程。
- 限制（已在 `DESKTOP_WAKE_TRANSPORT_DECISION_20261012.md` 记录）：外部触发默认弹确认、有 Premium/pay-as-you-go 前提、本地 attended 同时只跑一个流程。**不能为了"无人值守"擅自关闭确认或修改安全设置。**
- 定位：PAD 是"手"，负责把消息投递到已锚定的原窗口；事件 ID、优先级、停火、租约、ACK 仍由 AEP 协议负责。不要把 PAD 当成新的指挥大脑。

## 4. 已具备的能力（按四档诚实标注）

### 4.1 已实现且本机实机验收
- **Codex 原会话投递闭环**：`uia_session.ps1` 实测 PID 唯一窗口、`input_verified=true`、`uia-valuepattern` 写出、`outgoing_chars=520`、投递后重抓会话读回事件 ID、`new_session=false`。状态 `MESSAGE_VISIBLE`。**唯一缺口是对方 ACK**——2026-10-13 因 Codex 额度耗尽物理上无法回复，非机制问题。
- **会话全文总线**：`transcript_bus.py` + `transcript_patrol.py` 已实测采集 Codex 109–176 行进共享总线。

### 4.2 已实现代码、待实机验收
- **代替双击打开 + 发指令 + 等 ACK 全流程**：`commander.dispatch` 调用 `_profiled_deliver` → `ui_fallback.run`（challenge-response 防自 ACK → 轮询读回 `AEP-ACK:<nonce倒序>` → `AGENT_REPLY_VERIFIED`）。只要目标 `ui_profiles/<agent>.json` 存在且窗口已锚定，即自动走完。
- **多传输自动择优**：`commander._pick_transport` 只在 CDP 被显式钉到该客户端锚定会话时才用 CDP，否则回落 UIA/OCR。CDP 端口归属不可查时 fail-closed。
- **订单状态机**：`DEPOSITED→WINDOW_FOUND→MESSAGE_VISIBLE→AGENT_ACK`，以及 `CEASED`（投递未证实）/ `FAILED`（停火/过期）。不跳级推断。
- **无人值守编排**：`commander.watch`/`tick` 常驻循环 = 感知所有窗口→查 ACK→重投未完成订单→超阈值停火（`CEASE_AFTER_FAILURES`）。`wake_daemon.py` 是独立事件驱动队列，带去重、冷却、审计。
- **停火与幂等**：`transcript_bus` / `ui_fallback` 收据幂等；同根因连续失败自动冻结。
- **积分 / 竞赛 / 账本**：`legion_ledger.py` + `LEGION_COMBAT_PACT` 第 2 节。
- **投递审计**：`delivery_audit.py` 只读聚合 `DEPOSITED/MESSAGE_VISIBLE/AGENT_ACK/任务认领/产物验证`。

### 4.3 待锚定（机制在，客户端未就绪）
- **WorkBuddy / Qoder / CodeBuddy**：Electron 系 UIA 无控件，`perceive` 返回 `NO_WINDOW` / `NOTHING_READABLE` / `WINDOW_NOT_UNIQUE`。需：① 建 `ui_profiles/*.json`；② 用 `coord-client anchor` 钉唯一 PID；③ 走 OCR/键鼠投递。WorkBuddy 当前有两个候选窗口未消歧，**未消歧前禁止发令**（已在适配器里 fail-closed）。

### 4.4 待开发（机制缺口）
- **"换人 / 多波次"编排（已实现代码，2026-10-14 待实机验收）**：`submit_order` 支持 `candidates`，`commander.reinforce` 在订单 `CEASED` 或久未 ACK 时自动对候选 agent 生成差异化补位订单（火枪手轮次：一播攻完另一波补上），对应单测见 `tests/test_commander_reinforce.py`。
- **跨 agent 自动吵架 / 仲裁器**：`check_acks` 只查 ACK，没有把"两个 agent 同一题结论冲突"自动升级成会诊/仲裁。
- **竞赛"候选包→可投包"升级器**：`delivery_audit` 是审计骨架，但"依据回表错误码 + 上传锁 + 评分回归"自动把候选包升级为可投包，需要你提供回表/锁数据才能落地（机制已备，缺输入）。
- **短板自动发现与自我修正**：`LEGION_COMBAT_PACT` 写了"自动进化"，但代码层还没有"识别协作堵点 → 提议改进 → 自我改代码"的闭环。

## 5. 聊天同步机制（外挂日志 / 本地不脱敏）

- **已经存在且本地不脱敏**：`transcript_bus` 把每个客户端的窗口全文采集到本地 `coordination/` 下的总线文件（明文，不脱敏——符合你"本地不用脱敏"的要求）。`.gitignore` 已把 `messages/`、`private_conversations/`、`ui_profiles/`、`ui_receipts/`、`wake_*`、`captures/`、`receipts/` 全部隔离，**不会误提交到远程**。
- **其他 agent 看不到彼此对话，是因为没人采集，不是因为没有机制**。"看不见"的纪律：某 agent 行数为 0 表示"采集不到"，不是"它躺平"——不许反向当成躺平证据（`LEGION_COMBAT_PACT` 第 4 节已写明）。
- **强制动作**：任何 agent 开工前跑一次 `transcript_bus digest` + `awareness`，把"看不见彼此"从机制问题降级为操作问题。下一步可考虑在 `commander.tick` 的 `perceive` 阶段自动发布摘要给所有在线 agent。

## 6. 升级路线（可执行）

### 6.1 把"叫起来+指挥+沟通"从 Codex 扩到全员（最高优先级）
逐客户端执行，每家只做"发一条带事件 ID 的无害消息→独立读回→对方回 ACK→不新建窗口"四步验收：
```powershell
$env:PYTHONPATH = 'K:\PythonProjects5\agent-evolution-protocol\src'
# 1) 建档案（以 workbuddy 为例，先消歧两个窗口）
python -m agent_evolution_protocol.coordination.client_registry anchor --root coordination --agent workbuddy --pid <唯一pid>
# 2) 无害投递试验
python -m agent_evolution_protocol.coordination.commander submit_order --root coordination --target workbuddy --task PROBE --text '无害测试 EVT-PROBE-01' --sender commander
python -m agent_evolution_protocol.coordination.commander watch --root coordination --interval 20
```
任一客户端未通过"独立读回 + 对方 ACK"前，状态保持"投递未确认"，不冒称已唤醒或接单。

### 6.2 加"换人 / 多波次"编排（实现火枪手轮次）
在 `commander.submit_order` 支持 `candidates` 列表；`tick` 在目标 `CEASED` 或超时未 `AGENT_ACK` 时，对下一个候选 agent 生成"补位"订单（不取消原订单，符合"同题可并发但差异化"）。避免"只重投同一 target → 闭眼开枪"。

### 6.3 竞赛"候选包→可投包"升级器
你提供：各赛题的官方回表（错误码/失败原因）、上传锁状态、评分基线。我把 `delivery_audit` 扩成"回表停火 + 修复证据 + 回归 + SHA + 额度 + 上传锁复核"五道闸，只有全过才允许该包进入可投队列。在没有你的回表数据前，先搭框架、空跑、输出缺口清单。

### 6.4 跨平台编译协作
`capability_roster.py` / `CAPABILITY_MATRIX_20261013.md` 已经在登记"谁擅长跨平台编译 / 谁擅长某算子"。下一步：把"主攻 / 跨平台诊断 / 鲶鱼反证 / 独立审阅"四类角色在订单里显式声明，让某 agent 的编译短板能自动路由给有该能力的队友补位，而不是各自散打。

## 7. 作战指挥结果分析：28 次额度、失败过半

这是方法论，数据需从回表回填，但归因顺序先固定（不许跳结论）：

1. **先分桶**：编译失败 / 评测报错 / 结果不达标 / 重复提交 / 环境错误。
2. **再找共性**：同一错误码或同一算子家族反复出现 → 是知识缺口，不是运气。
3. **定停火线**：同一根因连续 2 次失败，该题进入停火，必须换打法或换人，禁止第 3 次盲投。
4. **留弹药**：任何一题上传前必须有书面评估（谁评估、依据哪份回表、预期改善多少）。没有评估的包是哑弹。
5. **火枪手轮次**：第一波投完立刻记录回表，第二波在**看过回表之后**再上；退下的那波去填弹（准备下一批包）。不看回表就补投 = 闭眼开枪。

把昨晚 28 次的每条失败按上面分桶后回填到 `delivery_audit`，就能得到"哪些是同一坑被烧了两次"的可视化，这正是军团级事故的高发点。

## 8. 激励机制：严厉拒绝躺平、谦让，自动进化

`LEGION_COMBAT_PACT` 已写反躺平条款与积分；本轮进一步强化的方向（已并入该契约第 1、2 节增补）：
- **"谁行谁上，不可退避"** 从姿态升级为记分约束：认领必须带"我认领 X，预计 Y 分钟内给 Z 证据"，仅表态"我先看看"记零分且视为观望。
- **鲶鱼必须带预算**：质疑须同时给替代假设、预期签名、最便宜证伪实验、预算、停止条件；只喊"我觉得不对"记噪音。
- **红皇后只对外部变化加速**：对手回表更新 / 官方榜单变动 / 新错误码才触发加速，禁止内部制造紧急感。
- **自动进化条款**：每轮 `tick` 后若某任务连续 N 次 `CEASED` 且无非 ACK 证据，自动生成"短板报告"并广播给有互补能力的队友，触发换人或会诊，而不是原地空转。
- **积分不替代证据**：`legion_ledger` 的分永不兑换成"已通过评测"，防止刷分冒险上传。

## 9. 结语

你要的"唤醒 + 指挥 + 沟通 + 无人值守 + 军团激励"在**协议层已经齐备**，真正要啃的是**客户端通道实机验收**和**换人多波次编排**这两块硬骨头。本仓库坚持的 fail-closed 原则（不确定就停在"投递未确认"，绝不冒称已接单）是这套机制能"攻无不克"而非"假战绩"的底座，升级时务必保留。

下一步按 6.1 → 6.2 → 6.3 顺序推进，每突破一个客户端就回填能力矩阵，不用"全员已就绪"的总括措辞。
