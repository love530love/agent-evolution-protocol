# Mage-VL-Cowork 引入评估（2026-10-31）

范围：是否把 `microsoft/Mage` 的 `Mage-VL-Cowork` 引入 AEP，以及以什么形态引入。
提出方：codex。本文件是对该提案的复核与更正，并登记到执行提供者目录。

**结论**：方向采纳——它是**执行节点**，不是调度内核的替代。但提案里有两处前提和一处数字
必须改，另有一处架构选择我不同意，理由见第 3 节。

## 0. 一句话

`Mage-VL-Cowork` 是我们缺的**视觉感知**能力的一个候选来源；它不是我们缺的**送达证明**，
也不是我们缺的**跨 Agent 调度**。这三件事在 AEP 里分别由 CUA/UI 适配器、证据阶梯、
`federation` 承担，不能合并。

## 1. 它是什么（事实层）

| 项 | 值 |
|---|---|
| 仓库 | `https://github.com/microsoft/Mage`（`mage_vl_cowork/`），权重 `microsoft/Mage-VL-Cowork` |
| 发布 | 权重、模型卡与 OSWorld 评测参考于 **2026-10-30** 放出；`Mage-VL` 基座 2026-08 |
| 规模 | 4B，bf16 **9.48 GB / 2 分片**；上下文 32,768（架构上限 262,144） |
| 架构 | 自定义 `llava_onevision2`；Mage-ViT（16×16 patch，3D RoPE）+ Qwen3-4B-Instruct-2507 |
| 动作 | `computer_use` 11 个原语（坐标 `[0,999]`）+ `bash`（持久 shell，`/tmp_workspace`） |
| 工作记忆 | 最近 **3 张截图** + **4 条完整回复**，更早折叠为单行 `Action:` 摘要 |
| 许可 | Apache-2.0；**加载需 `trust_remote_code=True`** |
| 主指标 | OSWorld-Verified **66.04%**（361 题、排除 Google Drive、50 步预算、两次均值 65.33/66.75） |

官方明确定位：训练与评测都在**隔离容器/快照 VM**，可以执行任意 shell 与 GUI 动作。

## 2. 必须更正的三处

### 2.1 「66.0%」不是我们要部署的那个组件的分数

提案引用 66.0% 作为引入价值，同时又要求「bash 必须经 AEP 命令代理」——这两件事互斥。
同一份报告的分阶段数字：

| 阶段 | OSWorld-Verified |
|---|---|
| Stage 2 纯 GUI SFT | **45.56%** |
| Stage 4 混合 SFT（GUI+bash） | 55.84% |
| Stage 5 在线混合 RL（即 66.04%） | **66.04%** |

66.04% 是**带自由 bash** 拿到的。把 bash 收进白名单代理之后，合理的预期区间是 **45–56%**，
而且没有任何测量支撑它落在哪一端。演示里那个「15.3× 快」也来自 bash 直接改文件——
正是要被管住的那条腿。

⇒ 引用 66% 时，必须同时引用 45.56%。否则是在用一个我们不打算部署的配置的分数，为部署决策背书。

### 2.2 视觉兜底解决不了 Qoder 的真正阻断

提案的主用法是「无 UIA 控件时的视觉兜底」。两条实测都不支持这个前提：

1. **阻断不是感知，是输入。** 2026-10-31 实测：Qoder 提权时，普通权限执行器
   `drivable: false`、`blocked_by: TARGET_NEEDS_ELEVATION`。UIPI 拒绝的是**注入输入**，
   不是**读取屏幕**。一个 4B 模型「看见」输入框，仍然无法从低完整性级别往里打字。
   我们已为此加了闸门：`EXECUTOR_TOO_LOW`，在写操作前就拒绝（`ui_fallback.run`）。
2. **对最关心的两个客户端，「没有 UIA 控件」已经不成立。** 开 `SPI_SETSCREENREADER` 之后，
   Qoder 给出 `input_candidate_count: 2`、`write_method: uia-valuepattern`、`input_verified: true`；
   调度路径已用 `transport: uia` 真送达并拿回 `AEP-ACK`（`docs/releases/v0.6.5.md`）。

所以 `MageWorkerAdapter` **不解除提权要求**——它只是多了一个也必须提权的 GPU 进程。
一个能跑 shell 的模型以管理员身份常驻，是安全降级，换来的是视觉能力**解决不了**的那个问题。

真正剩余的视觉场景是窄的：Canvas / 自绘控件、非 Electron 的原生程序、模态与 Toast 的识别。
这些值不值一个提权 GPU 进程，要单独算。

### 2.3 显存判断：12 GB 不行，我上一轮的 40 GB 也太保守

权重 9.48 GB，SGLang 默认 `--mem-fraction-static 0.82`（静态区占显存 82%）：

| 卡 | 静态预算 | 减去权重 | 余给 KV |
|---|---|---|---|
| 12 GB | 9.84 GB | **0.36 GB** | 基本为零，按官方参数跑不起来 |
| 16 GB | 13.12 GB | 3.64 GB | 可用，但要降 context 或 mem-fraction |
| 24 GB | 19.68 GB | 10.2 GB | 舒适 |

⇒ codex 的「16 更现实、24 以上稳定」是对的；「12 GB 可能勉强」不对。
另外认账：我在上一轮口头说的「≥40 GB 较稳妥」过于保守，官方没有给下限，这里是按参数推算的。

### 2.4 补充：AX 那组消融别读成「a11y 没用」

官方消融（去掉 Accessibility dump 后，4B 学生 25.1% → 37.6%，+12.5pp）说的是
**把 AX 文本转储喂给 4B 模型**有害。AEP 这边是**程序化使用** UIA（`ValuePattern` 写、
`TextPattern` 读），实测 3 元素 → 400 元素。两条不冲突。
风险在于「精简工具」被误读成「把 a11y 关掉」——那会直接毁掉本项目的送达路径。

## 3. 架构分歧：不要买 policy，先买 perception

`Mage-VL-Cowork` 是**一个自己拥有循环的 policy**：它决定何时点、何时写代码。
而 AEP 的价值在于**循环和证据归 AEP 所有**——`INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK`、
`input_verified`、`composer_cleared`、`send_retry`。把 policy 塞进来，等于把
「谁决定下一步」和「谁证明送达」合并成一个不可审计的黑盒。

而且这一周真正的进展不是更聪明的决策，是**确定性的控制**：`ValuePattern` 写入、
composer 轮询、粘贴重发、`EXECUTOR_TOO_LOW` 提前拒绝。都是「笨但可证」的东西。

更小的接入面是只取感知：

```
现状：composer 位置靠 -ComposerXRatio/YRatio 几何猜 + OCR marker 匹配
替换：一次 grounding 调用 → 返回区域 / 「发送按钮是否可用」/「哪个窗口在线」
证据链不动：仍由 input_verified / composer_cleared / transcript 读回判定
```

代价是放弃 bash 带来的那个 15.3×（本来也要被管住）。
**买 policy 还是买 perception，应当是一个显式决定，不该被「视觉大脑」这个比喻带过去。**

## 4. 落地：登记到既有目录，并把晋升变成闸门

不新造注册表。`schemas/execution-provider-catalog-v1.schema.json` 已经是标准，
本次按它登记：

```
id: mage_vl_cowork   layer: desktop_visual   integration: vision-service   status: candidate
```

同时修掉目录自身的两处不一致（这是「第二套标准」的典型现场）：

1. `examples/execution-provider-catalog.json` 用了 `integration: vision-service`、
   `cua_driver` 带 `license_note`，而 schema 的枚举与 `additionalProperties: false` 都不允许；
   没有任何测试读过 schema，所以一直没人发现。现已把两个值补进 schema，
   并让 `validate_catalog` 也校验 layer / integration 枚举与未声明字段，
   另加一条 `jsonschema` 直校验（`test_the_shipped_catalog_matches_its_published_schema`）。
2. **`status` 之前谁都能改。** 14 个 provider 全是 `candidate`，`probed` 一个都没有——
   这个注册表从来没被推进过一格，加第 15 个只是装饰。
   现在 `promote()` 是闸门，三条拒绝：
   - **不能跳步**：candidate 只能到 probed；
   - **不能无证**：`probed` 必须有 `probe_command` / `probe_at` / `probe_result`，
     `session-accepted` 必须有 `session_event_id` / `session_receipt` / `session_at`，
     `task-tested` 必须有 `task_id` / `task_receipt` / `task_outcome`；
   - **失败不是晋升**：`probe_result` / `task_outcome` 必须是 `pass`，否则保持 candidate 或 `retire`。
   每次晋升写一行 append-only 日志 `provider_promotions.jsonl`，`coord-providers history` 可查。

命令：

```bash
aep coord-providers list --layer desktop_visual
aep coord-providers promote --id mage_vl_cowork --to probed \
  --evidence probe_command="..." probe_at="..." probe_result=pass
aep coord-providers retire --id mage_vl_cowork --reason "..."
```

## 5. 预注册探针（在看见任何数字之前写死）

四步，每步都能失败。第 2、3 步是真正的判决点，其余是入场条件。

| # | 动作 | 通过条件 | 失败即停 |
|---|---|---|---|
| 1 | WSL2 里按官方 pin 起服务（SGLang 0.5.2 / torch 2.8.0+cu128 / flashinfer 0.3.1），记录 revision hash | `/health` 200 | 是 |
| 2 | 用**我们自己的 Windows Qoder 截图**问 grounding：输入框在哪、发送按钮是否 enabled | 返回可解析的区域/布尔 | 是 |
| 3 | 同一时刻用 `coord-uia observe` 的答案作参照，算**不一致率** | 不一致率低于现有 OCR+几何兜底 | 是（低于才继续） |
| 4 | 全程不给 `bash`、不给裸 `pyautogui`；动作只作为建议区域交给 CUA driver 执行 | 一次事件走完 `input_verified` + `composer_cleared` + 同事件 ACK | 是 |

判决标准：第 3 步的**不一致率**才是它的真实价值，而不是 OSWorld 66%。
如果它并不比现有 OCR+几何更准，这一轮就到此为止——省下一个提权 GPU 进程和一条供应链。

## 6. 什么会改变我的判断

- 第 3 步不一致率显著低于现状，**且**在 Canvas / 自绘控件这类现有通路真的读不到的场景上有效；
- 官方给出 **GUI-only 配置**（关掉 bash）的 OSWorld 分数，且高于 56%；
- 出现 Windows 原生（非 WSL 跳板）的低延迟部署路径，使每步截图往返不再是瓶颈；
- 我们自己的 `deliver` 延迟被证明不是瓶颈（目前一次尝试约 75 秒，插入 VLM 只会更慢）。

## 7. 参考

- 仓库与训练配方：<https://github.com/microsoft/Mage/blob/main/mage_vl_cowork/README.md>
- 项目页：<https://microsoft.github.io/Mage>
- 权重与模型卡：<https://huggingface.co/microsoft/Mage-VL-Cowork>
- 本项目实测：`docs/releases/v0.6.5.md`、`docs/HANDS_AND_FEET_STATUS_20261031.zh-CN.md`
