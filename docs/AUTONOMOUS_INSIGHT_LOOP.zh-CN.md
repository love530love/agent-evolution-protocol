# 自动发现问题与自动审计闭环：边界说明

> 记录日期：2026-11-05 · 对应能力：`skills/aep-insight-loop/SKILL.md`
> 本文是**边界文档**，不是设计宣传稿。凡本文与代码冲突，以代码与回执为准，并同步修本文。

## 0. 一句话结论

项目现在可以**在没有人事先提出具体问题的情况下，自动从已落盘事实里发现可复算的问题线索、自动完成证据复算与哈希冻结、自动把通过复算的线索写入带证据链的改进候选池**；但**不会**自动改源码、自动评审、自动晋升、自动提交或自动部署。项目不会自动改源码、不会自动晋升、不会自动部署。本地模型**不是这条链路的前提**：默认纯规则，模型只是可选的措辞建议者，且随用随走。

## 1. 为什么需要这一层（改造前的真实断口）

改造前项目不缺「记录」，缺的是「消费者」：

| 事实流 | 现场盘点量 | 是否有人自动读 |
|---|---|---|
| `coordination/battle_events/*` | 7 个 | 有部分调度读取 |
| `overlay_audit.jsonl` | 1 行 | 无 |
| `coordination/command_memory/observations/*.json` | 7 条 | 只在 `digest()` 被人工/显式调用时读 |
| `coordination/trajectories/*.json` | 1 份 | 只在 `verify/replay_plan` 显式调用时读 |
| `coordination/improvement_events/` | **0** | — |
| `coordination/improvements/` | **0** | — |

也就是说：约 16 处事实已 append-only 落盘，但 `improvement.ingest()` 从未被这些事实流自动喂过。
已存在的自动监督（`commander.watch/tick`、`federation/supervisor.py`、`campaign_cycle.reconcile`、
`hold_alerts.emit`）关注的是「任务有没有卡住」，不是「这段时间暴露了什么可以改进的模式」。
**本次新增的是一个离线消费者，不是第二套监督系统，也不是第二套审计 schema。**

## 2. 流水线

```
已落盘事实（只读）
   │
   ├─ scan()       4 条确定性规则，逐条输出 count / scanned / triggered / min_count / min_ratio
   │
   ├─ verify()     重读证据文件，重算 sha256 + count + scanned + key + rule
   │               任一漂移 → rejected[].reason，不进入候选
   │
   ├─ [可选] advisory  本地小模型只写 title / hypothesis / checks，通过严格 allowlist
   │                   模型缺席、弃答、失败、越界 → 丢弃文字，保留规则结果
   │
   ├─ [--apply] _snapshot()  把 append-only 日志复制为不可变快照
   │             build_event() → improvement.ingest()  → 状态恒为 PROPOSED
   │
   └─ 人工独立 reviewer（claim token / reviewer≠author / 回滚点）→ 才谈晋升
```

`scan()` **永远**报告 `scanned`（按数据源）和 `measurements`（按规则）。这条是硬要求：
「扫描器坏了」「机器很干净」「阈值没到」三种情况必须长得不一样，否则零发现就是不可信的。

## 3. 自动边界（最重要的表）

| 动作 | 自动？ | 说明 |
|---|---|---|
| 读取已落盘事实、统计命中 | ✅ | 只读，不修改任何源文件 |
| 重算证据 sha256 与计数、拒绝漂移 | ✅ | `verify()`，纯函数式复算 |
| 生成不可变证据快照、写哈希链日志 | ✅ | 仅在显式 `--apply` 后 |
| 生成 `PROPOSED` 改进候选 | ✅ | 通过既有 `improvement.ingest()`，`deployment_performed` 恒为 `false` |
| 跑验收测试 | 人工/外部触发 | 本层不自动启动测试进程 |
| 判定严重级别、根因、是否立项 | ❌ | `severity` 恒为 `lead`；升级即被 `verify()` 拒绝 |
| 修改源码 / 配置 / profile / 运行时状态 | ❌ | `scope: advisory-only; no source changes` |
| `advance` / `validate` / 晋升 `ADOPTED` | ❌ | 需独立 reviewer、claim token、回滚点 |
| commit / push / tag / release / 部署 | ❌ | 本次未做，也不在能力范围内 |
| 启动常驻 watcher / 模型 server | ❌ | 见第 5 节，明确禁止 |

**「自动」的准确含义**：发现与审计无需人类先给出问题，且无需人类做判断；**但触发本身仍由人或外部调度发起**。
本项目刻意不引入常驻进程——既因为操作员要求「不要让电脑卡顿」，也因为本机环境下
由 agent 建立的常驻进程会随工具调用结束被作业对象回收（见项目记忆）。想要周期性运行，
应由外部调度器或自动化调用同一条只读命令，而不是在本仓库里再养一个 daemon。

## 4. 规则与复算准入

四条规则定义在 `insight_miner.RULES`，全部是对文件的确定性统计，不含任何模型判断：

| 规则 | 数据源 | 触发条件 | 含义 |
|---|---|---|---|
| `unclassified_failure` | `coordination/command_memory/observations/*.json` | `count>=3` 且 `count/scanned>=0.5` | 失败未被规则库归类，关键词表可能缺本机错误模式 |
| `repeated_failure_signature` | 同上 | `count>=2` | 同一错误签名重复出现 |
| `unverified_overlay` | `overlay_audit.jsonl` | `count>=1` | 存在 `verified != true` 的弹窗处置 |
| `failed_trajectory_step` | `coordination/trajectories/*.json` | `count>=1` | 只读轨迹里有非零 `exit_code` |

`verify()` 的拒绝原因（全部可在回执里查到）：
`unknown-rule`、`severity-escalation-rejected`、`finding-marked-as-model-generated`、
`evidence-list-malformed`、`missing-evidence`、`evidence-changed`、`count-mismatch`、
`key-mismatch`、`no-longer-triggered`；`--apply` 阶段另有 `evidence-drift-since-snapshot`
与 `snapshot-<reason>`。

**为什么要快照**：`overlay_audit.jsonl` 会持续追加。若直接把 live 文件冻结进改进证据，
后续任何 `improvement.advance()` 都会因 `frozen evidence changed` 永久失败。
`_snapshot()` 把证据复制到 `coordination/insights/evidence/<finding_id>/0000-*`，
再对快照做一次 `verify()`。快照已存在而源数据变了 → 报 `evidence-drift-since-snapshot`，
**不静默重定义已有提案**，由人类决定是否重新立项。

发布用 `_publish_raw()`（同卷硬链接 + `xb` 临时文件），并发赢家不被覆盖，事件文件重放时内容不变。

## 5. 本地模型：默认不存在，用时随用随走

操作员约束：习惯 LM Studio、模型要小、不要常驻、不要让机器卡顿、接入 Agent 能力参差不齐。
对应设计：

- **规则层 socket-free**：`insight_miner.py` 不 import `socket / urllib / requests / httpx / insight_proposer`，
  由 `tests/test_insight_miner.py` 的 AST 守线（且守线自身有非空断言，防空转）。
  模型相关模块只在 `--propose` 时用 `importlib.import_module` 动态加载。
- **模型只做措辞**：`insight_proposer.describe()` 只发 `rule / count / scanned / summary`，
  **不发证据路径、哈希或文件内容**。`validate_advisory()` 只接受恰好 `title / hypothesis / checks`
  三个字段，拒绝未知字段、绝对路径、命令、控制字符；长度上限 200 / 300 / 5×120。
  模型文本只能追加到 `change` 的 `[model_generated advisory; not evidence, not a decision]` 段落，
  **永远进不了** `event_id`、`kind`、`evidence`、`severity`、`count`。
  弃答（`{"unknown": true}`）是合法答案。
- **小模型**：`.models/Qwen3-0.6B/model.safetensors` 约 1.5GB，`.gitignore` 已忽略 `.models/`；
  LM Studio 默认 model key `qwen3-0.6b`，Ollama 默认 `qwen3:4b`。权重不进主包、不在运行时被硬加载。
- **随用随走**（`local_model_session.py`）：

  ```
  lms load qwen3-0.6b --ttl 60 --gpu off -c 2048 -y --identifier aep-insight
  lms unload aep-insight
  ```

  `--ttl 60` 是崩溃兜底（即使调用方死了，LM Studio 也会自己卸载）；`--gpu off` 默认不抢显卡；
  `-c 2048` 限制上下文；`Plan` 拒绝 `ttl` 不在 10..3600、`context_length` 不在 512..8192 的配置；
  卸载按**精确 identifier**，绝不 `unload --all`（不动别人加载的模型）；
  `__exit__` 在 `finally` 里卸载，卸载异常不掩盖原异常。
  Ollama 没有 load 动作，只在退出时 `ollama stop <model>`。
  **代码里没有 `lms server start`，没有轮询，没有后台线程。**
- **缺席即降级**：server 关闭、`probe` 为假、load 失败、HTTP 异常 →
  `model_status` 为 `unavailable:<异常名>`，`model_note` 说明「未启动服务、无常驻模型」，
  **规则结果原样保留**。实测当日 `lms status` 为 `Server: OFF`、Ollama `11434` 连接被拒，
  纯规则路径照常给出 `unverified_overlay` 候选。

⚠ **诚实边界**：已用测试覆盖 LM Studio `/v1/chat/completions`（`response_format: json_object`）
与 Ollama `/api/chat`（`format: json`、`think: false`）的线格式、会话加载/卸载行为与降级分支；
已确认 `lms load/unload --help` 的参数存在、模型目录与大小、server 状态。
**尚未做 LM Studio 端到端真实推理实测**（`lms ls` 曾挂起并被终止）。因此不声称模型输出质量经过验证。
本项目也不需要它：能力不依赖模型。

## 6. 弱 Agent 如何完成同样的能力

「接入的任意 Agent 能力大小不同」是设计输入，不是事后补丁。规范见 `skills/README.md`：

- **卡片负责判断，Agent 只负责执行与抄回执字段**。卡片六个固定小节，禁止「综合判断 / 酌情处理」，
  结论一律查「回执判定表」。
- `model_required: false` 是所有卡片的必填项；`min_agent_capability` 只能是
  `file-only | cli | desktop-ui`。
- 不新增 schema、不新增命令面：命令名复用 `docs/coordination/skill-manifest.json`
  （由 `build_parser()` 生成，不可手改），派工契约复用 `schemas/dispatch-envelope-v1.schema.json`，
  回执结论复用 `docs/DELIVERY_TRUTH_MATRIX.zh-CN.md`，能力包复用
  `schemas/agent-capability-bundle-v1.schema.json`。
- 当前卡片：`aep-agent-onboarding`（cli，较早的散文规范）、
  `aep-start-dispatch`（**file-only**：新 Agent 从零接入 → 认领 → 派工 → 回执判定）、
  `aep-insight-loop`（cli）、
  `aep-desktop-wake`（**file-only**：只写 `coordination/delegation/inbox/<id>.json` 也能发起派工）。
  新 Agent 要开始调度任务时从 `aep-start-dispatch` 起步。
- `tests/test_capability_cards.py` 守：卡片 frontmatter 与目录名一致、六节顺序、
  所有 `coord-*` 命令真实存在、所有引用的 `docs|scripts|schemas` 路径真实存在、
  Qoder 0.5.3 插话序列不回退到旧结论。且每条守线都带扫描量断言，防止「守线空转」。

顺带修正了一处会误导弱 Agent 的文档漂移：
`docs/DESKTOP_SEND_AND_URGENT_INTERJECT.zh-CN.md` 旧版仍写「先看到插话控件再发送」，
与 Qoder 0.5.3 实测（发送后控件才出现）冲突，已改为两半判定与
`WAIT_INTERJECT_CONTROL` 轮询序列。

## 7. 回执字段（弱 Agent 只需抄这些）

| 字段 | 含义 |
|---|---|
| `scanned` | 按数据源的实读条数；**全为 0 只能写「没读到数据」，不能写「没问题」** |
| `rules_evaluated` | 规则数，当前 4 |
| `measurements` | 逐规则 `count / scanned / triggered / min_count / min_ratio` |
| `candidates` / `verified` / `rejected` | 候选数、复算通过、被拒原因 |
| `advisory_used` | 实际附上的模型建议数（只是措辞） |
| `model_status` | `disabled` / `ok` / `abstained` / `failed:*` / `rejected-advisory` / `unavailable:*` |
| `model_session.unloaded` | 模型是否已释放 |
| `applied` | 是否真的写了文件（默认 `false`，零写入） |
| `ingested[].state` | 只能是 `PROPOSED` |
| `deployment_performed` | **恒为 `false`**；若为 `true` 属设计违规，立即停止并报告 |

## 8. 与既有安全层的关系

`routing.classify_intent`、`federation/diagnosis.classify`、`recovery_policy.decide` 均为
fail-closed（歧义/未识别 → 降级、升级或 BLOCK）。概率模型没有「不知道」这个输出，
**不能替换这些裁判**。本层沿用项目已有的 `routing_proposer.py` 范式：
「模型提议 + 规则引擎保留否决权」。本次**没有改动**任何安全层模块，
也没有放宽任何既有验收测试来让自己通过。

`improvement.py` 的 `validate_tests()` 明确标注 `security_sandbox: false`，
`PROMOTION_BLOCKED` 注释写明「test success is not promotion」；
项目当前没有真正的隔离沙箱，因此「自动改源码」被排除在能力范围之外，而非留作 TODO。

## 9. 验收与复算

```bash
# 只读预览（零写入）
python -m agent_evolution_protocol.coordination.insight_miner --workspace .

# 可选：随用随走的模型建议
python -m agent_evolution_protocol.coordination.insight_miner --workspace . --propose --backend lmstudio --ttl 60 --gpu off

# 验收门
python -m pytest -q tests/test_insight_miner.py tests/test_local_model_session.py tests/test_capability_cards.py
```

实测结果（2026-11-05）：

- 上述三个测试文件（含边界文档守线、验收门引用守线、全局参数守线）：`51 passed, 83 subtests passed in 1.23s`。
- 全量回归（最终，含新增守线）：`1379 passed, 3 skipped, 503 subtests passed in 163.08s (0:02:43)`；
  补文档与守线之前的一次全量也全绿（`1377 passed, 3 skipped, 493 subtests`）。
- 真仓库 dry-run：`scanned = {command_memory: 7, overlay_audit: 1, trajectories: 6}`，
  `rules_evaluated = 4`，`candidates = 1`（`unverified_overlay`，finding_id `04c66729cb53e3bebae90cf2`），
  `model_status = disabled`，`applied = false`，`deployment_performed = false`。
- 不带 `--apply` 时，`coordination/insights/`、`coordination/insight_events/`、
  `coordination/improvements/` 均不应被创建；三者被 `.gitignore` 的 `/coordination/` 覆盖，不污染版本控制。

## 10. 本次未做（不要在别处写成已完成）

- 没有自改代码、没有自动晋升；**这套能力本身不触发 commit / push / tag / release**。
  本版交付由操作员按「每次更新务必 push 及发版」的约定人工发布为 v0.6.17。
- 没有常驻 watcher、没有定时任务、没有自动启动 LM Studio / Ollama 服务。
- 没有 LM Studio 端到端真实推理实测（见第 5 节）。
- 没有新增 schema、没有新增 `coord-*` 命令、没有修改 `skill-manifest.json`。
- 没有把鲶鱼主动质疑、统一 artifact 验收器、多日预算调度、supervisor 重启自恢复等
  历史待开发项冒充为已完成。

## 11. 改动文件清单

| 文件 | 作用 |
|---|---|
| `src/agent_evolution_protocol/coordination/insight_miner.py` | 纯规则离线消费者：`scan / verify / validate_advisory / build_event / _snapshot / mine / main` |
| `src/agent_evolution_protocol/coordination/insight_proposer.py` | 可选双后端（LM Studio / Ollama）措辞提议者 |
| `src/agent_evolution_protocol/coordination/local_model_session.py` | 随用随走会话：TTL、CPU、精确卸载、缺席降级 |
| `tests/test_insight_miner.py` | 证据复算、模型隔离、快照、幂等、socket-free 守线 |
| `tests/test_local_model_session.py` | 不常驻、不启动 server、异常仍卸载、线格式守线 |
| `tests/test_capability_cards.py` | 能力卡防漂移守线 |
| `skills/README.md` | 能力卡统一规范（不新增标准） |
| `skills/aep-insight-loop/SKILL.md` | 弱 Agent 执行「自动发现 + 自动审计」的卡片 |
| `skills/aep-desktop-wake/SKILL.md` | file-only 级唤醒/派工/插话卡片 |
| `docs/DESKTOP_SEND_AND_URGENT_INTERJECT.zh-CN.md` | 修正 Qoder 0.5.3 插话旧结论的文档漂移 |
| `docs/releases/v0.6.17.md` / `CHANGELOG.md` / 版本号三处 | v0.6.17 发版元数据与版本一致性绊线 |
