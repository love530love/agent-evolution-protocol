# 三个外部项目的能力融入（2026-11-02）

调研对象：

- **GitHub Copilot** — <https://github.com/features/copilot>
- **CLI-Anything** — <https://github.com/HKUDS/CLI-Anything>
- **Computer use** — 视觉/UI 代理的通用形态（看 → 定位 → 操作 → 再看 → 断言）

本文不是读书笔记。每一项能力都写成三段：**它是什么 → AEP 里已经有什么 → 差距是什么**，
然后给一个决定。决定只有两种：**融入**（有代码、有测试、有命令）或 **不融入**（写明理由）。
"看起来很强，我们也加上"不算理由；一个协议类项目里，第二套并存的标准比没有标准更有害。

---

## 0. 结论先行

### 融入了四项

| # | 能力来源 | 落地物 | 命令 | 测试 |
| --- | --- | --- | --- | --- |
| 1 | CLI-Anything「技能即接口」 | `coordination/skill_manifest.py` + `docs/coordination/SKILL.md` + `skill-manifest.json` | `coord-skill-manifest {show,write,check}` | `tests/test_skill_manifest.py`（22 条） |
| 2 | Copilot「跨表面延续同一会话」 | `session_fingerprint` 的语义/易变字段拆分 + `continuity()` | `coord-client continuity` | `tests/test_session_continuity.py`（20 条） |
| 3 | Computer use「看-做-再看-断言」 | `coordination/action_verification.py` + `input_routing.delivery_verdict(verification=…)` | `coord-route --send-report/--verification` | `tests/test_action_verification.py`（32 条） |
| 4 | CLI-Anything「`trajectory.json` 命令↔结果绑定」 | `coordination/trajectory.py` | `coord-trajectory {start,record,show,verify,replay}` | `tests/test_trajectory.py`（28 条） |

### 明确不融入五项

| 能力 | 为什么不做 |
| --- | --- |
| Copilot 的「cloud agent 从 issue 开始、以 PR 返回」 | AEP 已有 `handoff-packet-v1` + `task-envelope-v2` + `mission_graph`，语义已经覆盖"派出去、交回来"。再引入一套 issue→PR 的表述等于造第二套任务标准，而这正是本项目踩过的坑（见 `docs/coordination/` 里那批 `federation-*` 私有 schema 被全量回归抓住的教训）。 |
| Copilot 的模型选择 / BYOK / Ollama / Foundry Local | `execution_provider_catalog` 已经更强：`promote` 必须带证据才能推进 `probed → session-accepted → task-tested`，`retire` 留历史。BYOK 只是"多一个 provider 条目"，现有结构已能表达，不需要新机制。 |
| CLI-Anything 的 undo / redo | 对桌面投递没有意义。"发了就发了"——撤销一个已经送达对方的消息在协议上不存在。真正有用的是**回放只读步骤**，这个做了（第 4 项）。 |
| CLI-Anything 的 CLI-Hub 注册中心 | AEP 的 coordination root 本身就是注册中心（`clients/`、`ui_profiles/`、`claims/`、`acks/`）。再建一个中心会让"权威在哪"变成两个答案。 |
| Copilot 的「并行运行多个任务」 | `volley_plan`（波次 + 配额 + 保留）与 `battle_orders`（主攻/副攻/复核）已经在做，且带配额算术。Copilot 的并行是产品形态，不是可移植的机制。 |

---

## 1. CLI-Anything：技能即接口

### 它是什么

CLI-Anything 的核心主张是：**CLI 就是 agent 的接口，而接口文档必须从代码生成**。
它从 Click 装饰器自动导出 `SKILL.md`，把 SOP 写成代码（`HARNESS.md` 单一真理源），
用 `--json` 给结构化输出、`--help` 做自描述，并要求真实后端硬验证（依赖缺失时失败而不是跳过）。

### AEP 已经有什么

- 89 个子命令（`build_parser()`），其中 25 个在处理函数上有 docstring。
- `route_guidance.build_next_action()` 已经会返回**一条**可执行 argv，且明确"never execute"。
- 有防漂移测试的先例：`tests/test_route_guidance_doc.py`。

### 差距（这是真差距，而且已经造成过事故）

`docs/ROBUSTNESS_RESEARCH_20261028.zh-CN.md` 曾把 `schema` 写成 `aep-next-action-v1`、
把 `kind` 写成 `observe`，而代码实际是 `agent-evolution-next-action-v1` 和
`kind ∈ {command, needs_input, file_only}`。**整整两天没人发现**，因为散文不会报错。

当时的处置是给那一段散文补一个手写测试。这是对的，但只解决了一篇文档。

### 决定：融入

`skill_manifest.py` 把整个命令面从解析器生成出来，手写的只剩一张风险分级表。

三条硬规则：

1. **解析器是唯一真理源**。参数、默认值、choices 一个都不在这里复述。
2. **风险默认 `mutating`，未登记即按会写处理**。把会写的命令标成 `read-only` 是这里
   唯一有代价的错误——调用方会以为干跑过了。所以默认值朝安全方向倒，测试只守这个方向。
3. **摘要覆盖整份清单**，两次生成可以直接比摘要，不必 diff 89 行。

顺手暴露了一个真问题：**64 / 89 个命令在定义处没有任何说明**。第一版渲染把它们显示成
`—`，看起来像排版问题；现在它们被显式标成 `未记录`，并且 `tests/test_skill_manifest.py`
把未记录数量钉成**棘轮**——只许降，涨了就得有人解释。

写这个模块时，测试立刻抓到自己的一个洞：`check()` 原本信任文件里存的摘要，
所以**手改清单但不改摘要可以通过**。已改成先从文件自身内容重算摘要（`DIGEST_NOT_SELF_CONSISTENT`）。

---

## 2. Copilot：跨表面延续同一会话

### 它是什么

Copilot 的多表面（Desktop App / CLI / VS Code / JetBrains / Xcode / Vim / Web / 移动 /
Slack / Teams）共享**同一段会话**：在 CLI 里开的头，能在 IDE 里接着做。

### AEP 已经有什么

`session_fingerprint` 用 5 个字段算摘要：`agent`、`exe`、`title`、`session_marker`、`model_marker`，
并刻意排除 pid（重启不是新会话）。`compare()` 回答"这是不是上次那个窗口"。

### 差距

摘要**盖住了表面**。同一段会话换一个客户端打开，`agent`/`exe`/`title` 全变，
摘要必然不同，于是 `compare()` 说"不是同一段会话"——而这恰恰是它要支持的场景。
换句话说：现有指纹能回答"是不是同一个窗口"，**无法回答"是不是同一段会话"**。

### 决定：融入

把 5 个字段拆成两层：

| 层 | 字段 | 换表面时 |
| --- | --- | --- |
| 会话身份 | `session_marker` | 不变 |
| 模型 | `model_marker` | 可变（换模型不新开会话） |
| 表面 | `agent`、`exe`、`title` | 变 |

`continuity(recorded, observed)` 给四种答案，第三种是关键：

- `True` — 标记一致。`same_surface` 说表面有没有变，`model_changed` 是提示不是拒绝。
- `False` — 两边都报了会话名，但报的不是同一段。
- `None` — **没记录过，或者标记太短不足以当身份**。`None` 不是 `True`：
  同一个客户端里两段没有标记的会话是分不开的，硬说"是"就是消息进错线程的那条路。
- 标记短于 4 字符一律 `None`（`新会话`、`Hi` 这种会撞车）。

`group_by_conversation()` 把多份观测按标记聚成"这段会话现在显示在哪些表面上"。
没有标记的行**各自成组**，不合并——合并出来的组会声称自己是"所有无标记会话"，读起来像个有意义的数字。

有一处必须说明：`group_by_conversation` 与 `continuity` 都只做**离线**判定，
不要求目标客户端正在运行。这样同一条规则在有窗口和没窗口时答案一致，测试也不需要桌面。

---

## 3. Computer use：看-做-再看-断言

### 它是什么

一个 computer-use 代理的循环是：截图看 → 定位控件 → 操作 → 再看 → 断言成功了。
断言这一步是全部价值所在：没有它，"点了"和"点到了"没有区别。

### AEP 已经有什么

比想象中多：

- `policy_executor` 已经把策略关在笼子里：动作词表是 AEP 的（`desktop_providers.OPERATIONS`），
  shell 一律拒绝，0-999 坐标先范围检查再换算，**一个未验证的步骤就结束整轮**。
- `vision_grounding` 有 `Proposal` / `admissible()` / `agreement()` / `disagreement_rate()`，
  并且模型给出的定位只是"建议"，永远不是"已送达"的证据。
- `input_routing.resolve_target()` 有三条腿（`uia` / `vision` / `geometry`），
  `NEEDS_VERIFICATION` 明确标出哪条腿需要验证，`delivery_verdict()` 默认 `defer`。

### 差距

`delivery_verdict()` 会说 `send-after-verification`，但**没有任何地方定义"验证"是什么**。
验证逻辑当时散落在 `uia_session.ps1` 的一堆布尔里，Python 侧看不见，也无法测试。

### 决定：融入

`action_verification.py` 把验证变成契约，只放**今天真的会做**的三个检查：

| 检查 | 证明什么 |
| --- | --- |
| `text_present` / `text_absent` | 这段文字在不在画面上 |
| `placeholder_gone` | 输入框回到占位符了（文字真的离开了） |
| `text_moved` | 文字**离开**输入框带、**出现在**记录带 |

`verdict()` 拒绝四舍五入：失败优先于缺失（反驳是信息，缺失不是），
没有声明 `required` 就什么都不结论（"一堆通过"不能证明任何事，调用方没说清什么才算数）。

**`text_moved` 是这一节的要点**：文字"存在"不等于"发出去了"。草稿还在框里、消息已经滚进
记录带，两种情况下同一个字符串都在屏幕上，只有位置能分开。所以量的是带边界，不是"在不在"。

同时把 OCR 那条血泪教训从 ps1 搬进 Python 并变成可测规则：**识别器会换字**
（实测 `键`→`鍵`、`0`→`O`）。`fragment_match()` 从全长往后退到 4 字取最长存活片段——
**靠放弃字符来容错，而不是放宽比较**。放宽比较会同时接受"从没打进去的字"。

真实数据验证（订单 `20261102T145319592152Z-c0272241` 的 send_report）：

```
probe_in_composer_before_send = false
probe_in_transcript           = false
probe_in_composer_after       = false
placeholder_gone              = true
```

`from_send_report()` 判 `verified = false`，理由指名 `text_arrived` 失败。
**这是对的**：那次投递是被 agent 的 ACK（reversed-nonce）证明的，粘贴本身从未被证明。
两件事互相独立，把它们合成一个布尔就会掩盖"探针根本没触发"这个事实。
这条已固化成 `test_delivery_and_verification_are_different_facts`。

---

## 4. CLI-Anything：trajectory.json

### 它是什么

每一步同时绑定"命令"和"结果"，使轨迹事后无法被改写成另一个故事；并支持回放。

### AEP 已经有什么

`commander` 有 `attempts`，`send_report` 有几十个字段，receipts 按订单存。
但**没有一条有序的"这个 argv 产生了这个结果"的链**，回执是散的。

### 决定：融入（但回放不许重发）

`trajectory.py`：

- 每步存 `argv` **和**它的摘要，`result_ref` **和**它的摘要。
- `verify()` **重算**，不信任存下来的值。三种失败分开报，因为处置不同：
  `ARGV_TAMPERED`（文件被改）、`RESULT_MISSING`（证据没了）、`RESULT_CHANGED`（两半不再描述同一次运行）。
- 摘要按**值**算而不是按字节算：同一份 JSON 换行写不该被判成"结果变了"。
- `replay_plan()` **只回放 `read-only` 步骤**，会写的按名字拒绝并带上风险等级。
  轨迹是审计物；让它驱动一次发送，就把"复现这次运行"变成了"重放副作用"。
- `replay` 命令**只打印 argv 计划，不执行**——和 `route_guidance` 同一个立场：
  一个负责打印记录的工具有不该同时是执行它的那个东西。

顺带修掉一个自己写的 bug：`..` 作为 id 会变成 `...json`，是合法文件名但是个不可用的 id，
现在明确拒绝。

---

## 5. 剩余差距（诚实清单）

这些**没做**，不是忘了：

1. **64 个命令没有说明**。棘轮只保证不增长。真正的修法是逐个补 docstring，
   但那是 64 次判断，应该按使用频率排序做，不是一次性刷完。
2. **`continuity()` 还没有接进投递路径**。它现在是一个可查的判定（`coord-client continuity`），
   但 `commander.tick` 遇到"目标表面没了"时还不会去找同标记的另一个表面。
   接线需要先想清楚一件事：**换表面投递要不要重新征得同意**。这不该由实现顺手决定。
3. **`trajectory` 还没有自动记录**。现在要靠 `coord-trajectory record` 手动写。
   自动化的正确位置是 `commander.tick` 的 dispatch 出口，但那需要先把"一步"的定义
   定下来（一次 dispatch 算一步，还是 perceive→gate→send→ack 各算一步）。
4. **`action_verification` 只覆盖了文字类证据**。`click_geometry` / `composer_band_growth`
   还没有对应的检查；它们目前只是回执里的数字，没有被任何判定消费。
5. **Copilot 的"仓库指令 + skills 自定义"** 对应 AEP 的 `agent-capability-bundle-v1.schema.json`
   里的 `skills[]`，但那个字段目前**没有消费者**。补消费者比补 schema 难，也更值得做。

---

## 6. 落地清单

新增文件：

```
src/agent_evolution_protocol/coordination/skill_manifest.py     （约 300 行）
src/agent_evolution_protocol/coordination/action_verification.py（约 250 行）
src/agent_evolution_protocol/coordination/trajectory.py          （约 300 行）
docs/coordination/SKILL.md                                       （生成物，勿手改）
docs/coordination/skill-manifest.json                            （生成物，勿手改）
tests/test_skill_manifest.py      22 条
tests/test_session_continuity.py  20 条
tests/test_action_verification.py 32 条
tests/test_trajectory.py          28 条
```

修改文件：

```
src/agent_evolution_protocol/coordination/session_fingerprint.py  （拆字段 + continuity + grouping）
src/agent_evolution_protocol/coordination/input_routing.py        （delivery_verdict 接 verification）
src/agent_evolution_protocol/cli.py                               （3 个新命令 + 2 个新参数）
```

新增命令：

```
aep coord-skill-manifest {show,write,check} [--directory D] [--markdown]
aep coord-trajectory {start,record,show,verify,replay} --id X [...]
aep coord-client continuity --agent A [--target-agent B]
aep coord-route ... [--send-report FILE | --verification FILE]
```

验证方式：

```
aep coord-skill-manifest check          # 退出码 0 表示文档与 CLI 一致
aep coord-client continuity --agent workbuddy3
aep coord-trajectory verify --id <run>
```
