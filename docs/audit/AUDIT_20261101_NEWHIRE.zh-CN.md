# 新人审计：文档说的 vs 实际发生的

> **审计对象**：`K:\PythonProjects5\agent-evolution-protocol` @ `627f7c5`（`git describe` = `v0.6.15`）
> **审计人**：新入职第 3 天，无内部 wiki、无导师可问、之前未参与本项目
> **方法**：逐字照文档做，不做经验性补全。文档说敲什么就敲什么，报错原样记。
> **边界**：不重复红队/lead/SRE 的发现。本文只做一件事——**文档与代码/实测对不上**。
> **未跑的实验一律标「未覆盖」，不推测成结论。**

---

## 0. 结论先行

**第一处卡点**：`docs/INTRODUCTION.zh-CN.md:178` 的 `python -m agent_evolution_protocol init …`。
在 Git Bash 里得到 `No module named agent_evolution_protocol`；换到 PowerShell 立刻成功。87 份文档没有一处说明"用哪个 python"。新人会因此去 `pip install -e .`，把机器搞成两套安装。

**最严重的矛盾**：`README.md:64` 把 `docs/SYSTEM_OVERVIEW.zh-CN.md` 标成「总体系与能力状态」，那份文档写的是 **v0.3.0**，仓库是 **0.6.15**。同一份 README 里 `README.md:186` 又把 `README.zh-CN.md` 当同一份文档，那份停在 **v0.5.0**（`coord-improvement` 出现 0 次，英文版 13 次）。

**最该修的一处**：`.aep/workspace.json` 入库且写死 `K:\PythonProjects5\...` 绝对路径。新克隆里 `aep coord-status` 返回的是**原作者机器上的**协作目录。这是"文档承诺"与"实现"之间最贵的一条，见 D-01。

**请裁决的三点**见第 8 节。

**统计**：26 条发现 → `说假话` 11 / `静默失效` 5 / `崩溃` 6 / `只是难看` 4。置信度：纯实测 23 条、纯读码推断 1 条（D-06）、实测+推断 2 条（D-04、D-16）。

---

## 1. 新人第一处会卡住的地方

### 他看到的原文

`README.md:82`：

> **New here?** Start with the plain-language [Chinese introduction](docs/INTRODUCTION.zh-CN.md), which explains the failure mode, design rationale, roles, one complete round, and a beginner walkthrough.

他点进去，读到 `docs/INTRODUCTION.zh-CN.md:167`「初学者第一次试用」，照抄第 178 行：

```powershell
python -m agent_evolution_protocol init `
  --round-id demo --task example --leader lead --cells alpha beta catfish --budget 10
```

### 他实际得到的输出

```text
C:\Users\love\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe:
No module named agent_evolution_protocol
```

### 他需要猜什么

1. **这是"没装"还是"装错地方了"？** 两种解释都成立，且给出的行动完全不同（`pip install -e .` vs 换解释器）。
2. **这台机器上到底有没有装过？** 他去查：
   ```text
   $ python -m pip show agent-evolution-protocol
   Name: agent-evolution-protocol
   Version: 0.2.0
   Location: D:\A\Lib\site-packages
   ```
   `pyproject.toml` 写的是 `0.6.15`。于是他得到第三个假设："装了个 0.2.0 的老版本，要重装。"
3. **到底该信哪个版本号？** 他去试 `aep --version`：
   ```text
   $ aep --version
   exit=2
   aep: error: the following arguments are required: command
   ```
   报错完全没提 `--version`，只吐了两屏 usage。第四个假设："这个包装坏了。"

### 真相（我后来查出来的，文档没写）

| 事实 | 值 |
|---|---|
| `aep` 可执行 | `D:\A\Scripts\aep.exe`，用 `D:\A\python`（3.13.9） |
| editable 安装位置 | `D:\A\Lib\site-packages\__editable__.agent_evolution_protocol-0.2.0.pth` → `K:\…\src` |
| Git Bash 的 `python` | `C:\Users\love\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe` |
| PowerShell 的 `python` | `D:\A\python.exe` ← 所以 PowerShell 里同样的命令能跑 |
| `pip show` 的 0.2.0 | dist-info 元数据从 v0.2.0 起没再更新过；代码是 0.6.15 |

**结论**：同一个新人、同一台机器、同一个仓库，**换一个终端窗口，结果完全不同**。而文档通篇用 `powershell` 代码块，却从没说过"必须在 pwsh 里跑"或"python 必须指向哪个"。

### 为什么这不是"我需要学"

"项目用哪个解释器"是仓库级约定，不是领域知识。它无法从代码里推断（代码里没有 `.python-version`、没有 `[tool.pytest]`、`pyproject.toml` 只写 `requires-python = ">=3.10"`），只能由文档告知。**这一条属于"文档没写"，不属于"我该自己查"。**

---

## 2. 文档互相矛盾：逐对判定

判定依据只有两种：**实测输出** 或 **代码**。不用"两者都有道理"糊过去。

### C-01 命令清单：33 还是 87

| | |
|---|---|
| A 说 | `README.md:171-175` 列了三组命令（治理 7 + 协作 18 + 内核 8 = 33 个），标题是「The `aep` command … exposes both planes」，读起来是全集 |
| B 说 | `aep --help` 的 argparse choices 有 **87** 个 |
| **判定** | **B 对，A 不完整** |
| 依据 | `parser._actions` 里 `_SubParsersAction.choices` 实测 87 项；且 A 的清单里没有 `coord-improvement`（24 个子命令）、`coord-dispatch`、`coord-ack` 等 |
| 补充 | 43 个命令在两份 README 里**一次都没出现**（脚本 `check_doc_commands.py`）。`coord-transcript` 在 `docs/` 里 0 命中 |

### C-02 中文 README 与英文 README 是不是同一份

| | |
|---|---|
| A 说 | `README.md:186` — "See … [the Chinese README](README.zh-CN.md)"，并列在"beginner introduction / protocol / evolution history"之后，语义是同份文档 |
| B 说 | 两份内容不同 |
| **判定** | **英文版对，中文版陈旧** |
| 依据 | `coord-improvement` 出现次数：英文 13 / 中文 **0**。行数 234 / 86。`git log -- README.zh-CN.md` 最后一条是 `fb6497f release: v0.5.0`；`README.md` 最后一条是 `627f7c5`（v0.6.15）。代码里 `coord-improvement` 有 24 个子命令（`aep coord-improvement --help` 实测），中文版对此完全无记录 |

### C-03 「能力状态」到底停在哪个版本

| | |
|---|---|
| A 说 | `README.md:64` — `[总体系与能力状态](docs/SYSTEM_OVERVIEW.zh-CN.md)` |
| B 说 | 该文档 `:17` 标题 `## v0.3.0 能力与边界`；`## 版本关系` 链接 `releases/v0.3.0.md` 并称"本次交付" |
| **判定** | **A 的标签错（说假话），B 是过期文档** |
| 依据 | `pyproject.toml:7` = 0.6.15；`__init__.py:3` = 0.6.15；`git describe` = v0.6.15。`EVOLUTION.md:25` `## Current maturity` 同样停在 "v0.3.0 keeps …" |
| 补充 | 87 份顶层文档里只有 2 份提到 `0.6.15`，且都不是指南（一份 ACK 记录、一份状态快照） |

### C-04 「auto-discovers from this folder」到底从哪发现

| | |
|---|---|
| A 说 | `AEP_JOIN.md:22` — "The `aep` CLI auto-discovers `.aep/workspace.json` **from this folder**." |
| B 说 | 机制正确（确实读 `.aep/workspace.json`），但读到的是文件里的**绝对路径**，不是"这个文件夹" |
| **判定** | **代码为准；A 的措辞误导** |
| 依据 | `cli.py:190` `configured_root()`：`path = Path(config.get(key, default)); return path if path.is_absolute() else workspace / path`。入库的 `.aep/workspace.json` 是 `{"coord_root":"K:\\PythonProjects5\\agent-evolution-protocol\\coordination", …}` |
| 实测 | 克隆到 `K:\_aep_audit_tmp\clone1` 后 `aep coord-status` → `"coord_root": "K:\\PythonProjects5\\agent-evolution-protocol\\coordination"` |

### C-05 第一步到底是 `doctor` 还是 `coord-onboarding`

| | |
|---|---|
| A 说 | `docs/COORDINATION_CLI.zh-CN.md:16` 第一条：`aep --coord-root coordination doctor` |
| B 说 | `docs/AGENT_ONBOARDING_CONTRACT.zh-CN.md:10` 第一条：`aep --coord-root coordination coord-onboarding --agent <agent>` |
| **判定** | **两者都对，但 A 的顺序错**——在空协作根上 `doctor` 直接失败 |
| 实测 | 空 root：`protocol error: coordination doctor found missing required directories`，rc=2。同一 root 上 `coord-onboarding` rc=0 正常返回 |
| 依据 | `scripts/bootstrap.ps1` 的正确顺序是 `coord-bootstrap` → `doctor`。`coord-bootstrap` 在 87 份文档里 **0 命中** |

### C-06 CI 覆盖哪些 Python 版本

| | |
|---|---|
| A 说 | `README.md:62` — "CI covers Ubuntu/Windows and Python 3.10/3.12." |
| B 说 | `.github/workflows/test.yml` 有三个 job；第三个 `offline-desktop-sdk` 用 `python-version: '3.13'` |
| **判定** | **B 为准；A 不完整（漏了 3.13 那条主线）** |
| 依据 | 该 job 是唯一跑 SDK 对账、wheel 比对、dependency-gate 的 job，不是边角 |

### C-07 `coord-inbox` 存不存在

| | |
|---|---|
| A 说 | `docs/releases/v0.6.14.md:62` — "仓库里已经有这套标准：`coord-send` 写这个路径，`coord-inbox`/`coord-digest` 读它" |
| B 说 | 87 个 choice 里没有 `coord-inbox` |
| **判定** | **A 错（说假话）** |
| 实测 | `aep coord-inbox --help` → `invalid choice`，退出 2 |
| 依据 | `grep -rn "coord-inbox" src/` 只有 2 处，都是 docstring/注释：`coordination/hold_alerts.py:12` 也写着这个名字。**说明是改名成 `coord-digest` 后没清理引用** |

### C-08 版本历史以谁为准

| | |
|---|---|
| A 说 | `CHANGELOG.md` 21 条，`## 0.1.0` 之后才是 `## 0.4.0`（顺序错），缺 0.3.0 / 0.3.1 / 0.4.1 / 0.4.2 |
| B 说 | `docs/releases/` 23 份 |
| **判定** | **B 为准** |
| 依据 | `compare_versions.py` 输出：`in CHANGELOG only: ['0.1.0','0.2.0']`，`release note only: ['0.3.0','0.3.1','0.4.1','0.4.2']`，`changelog order correct: False`。另：`v0.3.1` 有发布说明但 `git tag` 里没有（22 个 tag / 23 份说明） |

### C-09 最小验收命令在新克隆能不能跑

| | |
|---|---|
| A 说 | `docs/PROJECT_STANDARDS.zh-CN.md:76`「最小验收命令」四条，是"工程基线" |
| B 说 | 后两条依赖 `.tools/agent-runtime/Scripts/python.exe`，`.tools/` 被 `.gitignore:9` 忽略 |
| **判定** | **A 只在本机成立，对新克隆是空承诺** |
| 依据 | `git ls-files .tools` → **0 个文件**。该文档没写"这四条仅限已有本机运行时" |

### C-10 「被拒 exit 2」是不是普遍成立

| | |
|---|---|
| A 说 | `docs/HANDS_AND_FEET_STATUS_20261031.zh-CN.md:460-461` — "`coord-dispatch --order-id <id>` 发送前闸门，被拒 exit 2"；"`coord-ack --order-id <id>` ACK 判定，未接受 exit 2"。`docs/DISPATCH_GATE_20261101.zh-CN.md:142` — "任一张单被拒 → exit 2"。`docs/ACK_AND_SESSION_IDENTITY_20261101.zh-CN.md:87` — "未接受时 exit 2" |
| B 说 | `coord-claim` 作用域冲突实测 **exit 3** |
| **判定** | **A 只对了一部分；exit 3 没有任何文档** |
| 实测 | `coord-dispatch --order-id NOSUCHORDER` → exit 2 ✓；`coord-ack --order-id NOSUCHORDER` → exit 2 ✓；`coord-claim` 冲突 → **exit 3** |
| 依据 | `cli.py:3336-3342`：`if result.get("schema") == "agent-evolution-claim-result-v1" and not result.get("claimed"): return 3`。**整个 CLI 只有这一个 schema id 被特判成 3**。`grep "exit 3" docs/` → 0 命中 |

### C-11 ACK 是一个概念还是三个

| | |
|---|---|
| A 说 | `docs/AGENT_OPERATION_GUIDE.zh-CN.md:29` — "`AGENT_ACK` 才是接单确认"（订单级语义） |
| B 说 | CLI 有 `coord-ack --order-id`、`coord-ack-topic --agent --topic`、`coord-ack-message --agent --id` 三个命令 |
| **判定** | **不矛盾，是三个不同粒度共用一个词，且没有一处说明它们是三种东西** |
| 依据 | 三个 `--help` 的必填参数完全不同：订单 id / 主题 / 消息 id |

### C-12 v0.6.15 的测试数字

| | |
|---|---|
| A 说 | `docs/releases/v0.6.15.md:218` — "Windows `.tools/agent-runtime` 3.13.9 \| **1058 passed, 3 skipped**" |
| B 说 | 我用 `D:\A\python` 3.13.9 跑：**1059 passed, 2 skipped** |
| **判定** | **判不了，标未覆盖** |
| 依据 | 两个 skip 都是 `tests/test_a11y.py:27/69: this assertion is about the non-Windows path`。差异可能来自 `.tools/agent-runtime` 与 `D:\A` 的环境差，但我没有 `.tools/agent-runtime` 之外的对照，无法判定谁对 |

---

## 3. 文档承诺 vs 代码实现：清单

| # | 承诺 | 出处 | 实际 | 兑现 |
|---|---|---|---|---|
| P-01 | `coord-inbox` 读消息路径 | `releases/v0.6.14.md:62` | 命令不存在 | **0** |
| P-02 | 被拒统一 `exit 2` | `HANDS_AND_FEET:460-461` 等 4 处 | `coord-claim` 冲突是 `exit 3`，无文档 | **部分** |
| P-03 | README 命令清单 = 全部命令 | `README.md:171-175` | 33 / 87 | **38%** |
| P-04 | `AGENT_OPERATION_GUIDE` 讲投递/会话切换/失败重试/证据判据 | `README.md:232` | 34 行，只讲证据判据，0 条命令 | **~40%** |
| P-05 | 用 `AGENT_JOIN_PROMPT.md`「完成接入」 | `README.md:232` | 该模板 15 行，0 条命令；它让你再读另一个 0 命令文件 | **0** |
| P-06 | `./aep.ps1` 是统一入口 | `README.md:19` | 新克隆里 `Runtime receipt missing; install the portable core first` | **0（缺前置）** |
| P-07 | `PROJECT_STANDARDS` §7 四条验收命令 | `PROJECT_STANDARDS:76` | 后两条依赖被 gitignore 的 `.tools/` | **2/4** |
| P-08 | `SYSTEM_OVERVIEW` = 能力状态 | `README.md:64` | 停在 v0.3.0 | **0（差 12 版）** |
| P-09 | `schemas/` 是 schema 标准 | `PROJECT_STANDARDS:38` | 20 个输出 schema id 只覆盖 1 个 | **5%** |
| P-10 | `CHANGELOG` 是版本历史 | `PROJECT_STANDARDS:65` 发布流程 | 缺 4 版、顺序错、v0.3.1 无 tag | **部分** |

> 证据分布：P-01/P-02/P-09 下面给了完整输出；其余的证据在对应 D 条目里——P-03→D-09、P-04→D-10、P-05→D-11、P-06→D-14、P-07→D-16、P-08→D-08、P-10→D-19。

### P-01 证据

```text
$ aep coord-inbox --help
usage: aep [-h] [--root ROOT] … {init,commit,…,federate} ...
aep: error: argument command: invalid choice: 'coord-inbox'
        (choose from init, commit, reveal, …, federate)      ← 87 个 choice 全列一遍
```

代码里只有两处提到它，都是注释：`coordination/hold_alerts.py:12`。

### P-02 证据

```text
$ aep --coord-root ./coord coord-claim --agent alpha --task T-A --paths src/x.py
exit=0
$ aep --coord-root ./coord coord-claim --agent beta  --task T-B --paths src/x.py
exit=3
{"schema": "agent-evolution-claim-result-v1", "claimed": false, "reason": "scope_conflict", …}
```

`cli.py:3339` 是全仓库唯一产生 exit 3 的地方，且只认这一个 schema id。
（**这条同时验证了 `COORDINATION_CLI:66` 的承诺是对的**：冲突时确实没留下空 claim 目录——`ls coord/claims` 只有 `T-A`。文档在这点上是准确的。）

### P-09 证据

```text
schema files: 7
distinct schema ids emitted by src: 20
covered by a schema file: 1 -> ['aep-agent-capability-bundle-v1']
NOT covered: 19
```

19 个裸奔的包括 `agent-evolution-claim-result-v1`（就在 P-02 的输出里）、`agent-evolution-route-v1`、`agent-evolution-workspace-v1`（就是 `.aep/workspace.json` 里那个）、`aep-reasoning-contract-v1`、`aep-readback-v1`、`aep-handoff-analysis-v1`、`aep-federate-{status,serve,revive}-v1` 等。两套前缀 `aep-*` / `agent-evolution-*` 无任何文档解释。

---

## 4. 术语不一致对照表

同一概念在不同文档/代码里叫什么，以及它们的实际差别。

| 概念 | 文档里出现的名字 | 代码/CLI 里的名字 | 差异 |
|---|---|---|---|
| 一次派工 | 订单（9 份文档）、派工单（2 份）、dispatch（13 份）、order（8 份）、`AEP 派工单 / ASSIGNMENT`（`DISPATCH_GATE:114` 的渲染标题） | `order_id`、`coord-dispatch`、`dispatch-envelope-v1` | **5 个名字指同一件事**；`assignment` 只出现在渲染输出里，schema 里没有 |
| 工作单元 | 任务、task、`T125` / `BUG-42` | `--task`、`coord-task-state`、`task-envelope-v2` | 与「订单」的关系**没有任何文档说明**。`coord-dispatch --order-id` 和 `coord-claim --task` 交替出现 |
| 占用 | 认领、claim、租约、lease | `coord-claim --ttl-hours 48` | 单位是**小时** |
| 资源锁 | 租约、lease、`ACK lease`（`NOVICE_LONGRUN:23`） | `kernel-lease-acquire --ttl-seconds 300` | 单位是**秒**；另一套系统、另一个根目录（`.aep-kernel/`）、另一个命令族。**和上面那条同名不同物** |
| 接单确认 | ACK、`AGENT_ACK`、`MESSAGE_VISIBLE` | `coord-ack --order-id` / `coord-ack-topic --topic` / `coord-ack-message --id` | **三个粒度共用一个词**，无文档说明层级关系 |
| 回执 | 回执、receipt | `pad_receipts/`、`ui_receipts/`、completion `receipt` | 三种回执、三个目录，`PROJECT_STANDARDS` 只把 completion receipt 当完成证据 |
| 交接 | 接力、交接、handoff | `handoff-packet-v1`（数据包）、`inspect-handoff`（命令）、`BLOCKED_HANDOFF_REQUIRED`（状态）、`digest-first handoff`（策略） | **一个词四种词性** |
| 阶段 | 阶段、phase、stage、状态 | `--phase`（9 个小写值）/ `--state`（7 个大写值） | 见下 |
| 简报 | `brief`（`README.md:5`） | `coord-improvement brief`（命令） | 文档里当名词用（"接替者的 brief"），代码里是动词/命令 |

### 阶段的三套词表（互不通用）

```python
# cli.py:88  —— coord-task-state --phase
("todo","claimed","working","waiting","blocked","needs-review","ready","done","abandoned")

# coordination/improvement.py:12  —— coord-improvement advance --state
{"PROPOSED":{"VALIDATED","REJECTED"}, "VALIDATED":{"REVIEWED","REJECTED"},
 "REVIEWED":{"TRIAL","REJECTED"}, "TRIAL":{"ADOPTED","ROLLED_BACK"}, …}
```

```text
# docs/NOVICE_LONGRUN_WORKFLOW.zh-CN.md:20 —— 第三种，中文
研究、实现、验证、审查、交付
```

`README.md:5` 说"接替者的 `brief` 自动带上**同阶段**失败证据"——**三套词表，读者无法确定是哪一套。** 这是我在整份文档里卡最久的一句话。

### 术语覆盖度（顶层 87 份文档）

| 词 | 出现文档数 | 有统一定义吗 |
|---|---|---|
| `ACK` | 74 | 无 |
| `lease` / 租约 | 27 | 无 |
| `handoff` / 接力 | 21 | 无 |
| `claim` / 认领 | — | 无 |

`grep -rn "术语表\|词汇表\|Glossary" docs/*.md` → **0 命中**。

---

## 5. 我的一天（压缩）

| 时刻 | 我做了什么 | 结果 |
|---|---|---|
| T+0 | 读 `README.md`（234 行），校验全部相对链接 | 68/68 存在，0 缺失 ✓ |
| T+4 | 找"怎么开始" | 找到 6 处"先读 X"，互不引用 |
| T+8 | 读 `INTRODUCTION.zh-CN.md`，照 `:178` 敲 `python -m …` | `No module named agent_evolution_protocol` ✗ |
| T+10 | `pip show` | `Version: 0.2.0`（pyproject 是 0.6.15） |
| T+11 | `aep --version` | exit 2，`the following arguments are required: command` |
| T+12 | 换 `/d/A/python` 重跑 init/commit/reveal/status/audit | 全通 ✓ |
| T+18 | `bootstrap.ps1 -CoordRoot <tmp>`（读源码发现未文档化的 `-SkipInstall`） | 通 ✓ |
| T+20 | 空 root 上 `doctor` | rc=2 `missing required directories` ✗ |
| T+22 | 同一空 root 上 `coord-status` | rc=0，返回默认值，**什么都不创建、什么都不报** |
| T+26 | 按 `AGENT_ONBOARDING_CONTRACT` 四条命令 | onboarding 有用 ✓ / digest 空 ✗ / status 一大坨 / continuation-plan `BLOCKED_HANDOFF_REQUIRED` |
| T+30 | 换 `--agent codex` 试 digest | `unread=8` —— 原来 digest 是按 agent 名的信箱 |
| T+36 | 沿 `README.md:232` 走 SKILL → JOIN_PROMPT → OPERATOR_CONTRACT | 3 跳、130 行、**0 条命令** |
| T+45 | `git clone` 到临时目录，跑 `aep coord-status` | 指向**原仓库**的 coordination ✗ |
| T+48 | 新克隆里 `./aep.ps1` / `./aep-desktop.ps1` | 两个都缺 runtime ✗ |
| T+52 | `python -m pytest` | `No module named pytest` ✗（又是解释器） |
| T+55 | `/d/A/python -m pytest -q` | `1059 passed, 2 skipped in 293s` ✓ |
| T+60 | 再跑一遍 | `1 failed, 1058 passed` ✗ ← 什么都没改 |
| T+66 | 给 `coord-claim` 加 `--dry-run` | 改 2 处成功，无文档指导 |
| T+70 | schema 对账 | 20 个 id / 1 个文件 |
| T+72 | 找术语表 | 不存在 |
| T+75 | 放弃，写报告 | |

---

## 6. 其余发现

> 严重度只用四个词：`说假话`（文档/输出断言了不成立的事）/ `静默失效`（不报错但结果不对）/ `崩溃`（直接报错或退出）/ `只是难看`

### D-01 新克隆里所有不带 `--coord-root` 的命令都指向原作者机器上的协作目录

- **复现**：`git clone <repo> clone1 && cd clone1 && aep coord-status`
- **实测**：
  ```json
  {"coord_root": "K:\\PythonProjects5\\agent-evolution-protocol\\coordination", …}
  ```
  `.aep/workspace.json` 入库且内容为绝对路径；`coordination/`、`.aep-kernel/`、`.tools/` 均在 `.gitignore`（`git ls-files .tools` = 0）
- **应该是**：`coord_root` 落在克隆自己的目录下；或读到跨仓库绝对路径时**报错拒绝**，而不是静默采用
- **严重度**：`静默失效`（读写都落到别人的工作区，`coord-claim` / `coord-send` / `coord-wake` 走同一个 `configured_root()`，`cli.py:190`）
- **置信度**：`实测`

### D-02 新 agent 的 digest 是空的

- **复现**：`aep --coord-root <copy> coord-digest --agent newhire`
- **实测**：`{"agent":"newhire","unread_count":0,"topics":[],…}`；同一 root 换 `codex` → `unread=8`，`hermes_desktop` → `unread=4`
- **应该是**：文档说明"新人先用什么名字读"，或提供一个项目级的历史入口
- **严重度**：`静默失效`（契约说"读取上下文"，实际什么都读不到，且不报错）
- **置信度**：`实测`

### D-03 `doctor` 的检查相对当前工作目录，不相对协作根

- **复现**：同一 `--coord-root`，分别在仓库根和临时目录跑 `doctor`
- **实测**：临时目录 → `{"name":"local_model","ok":false,"detail":"K:\\_aep_audit_tmp\\fresh\\.models\\Qwen3-0.6B"}`；仓库根 → `ok:true`
- **应该是**：`--coord-root` 决定所有检查的相对基准
- **严重度**：`静默失效`（假失败，把人引到无关的模型目录）
- **置信度**：`实测`

### D-04 全量测试偶发失败

- **复现**：`CODEBUDDY_SAFE_DELETE_ENABLED=0 /d/A/python -m pytest -q`，连跑 5 次，不改任何代码
- **实测**：
  ```text
  第1次 1059 passed, 2 skipped in 293.06s
  第2次 1 failed, 1058 passed, 2 skipped in 257.52s   ← 同一命令同一工作区
  第3次 1059 passed
  第4次 1059 passed
  第5次 1059 passed, 2 skipped in 493.72s            ← 耗时翻倍
  ```
  4 个 watcher 测试文件批量跑 9 次复现 1 次：`FAILED tests/test_campaign_watcher_integration.py::CampaignWatcherIntegrationTests::test_file_event_reconciles_expired_assignment`；单跑该用例 6 次全过
- **应该是**：测试要么稳定，要么明确标 skip
- **严重度**：`崩溃`（新人第一反应是"我刚才改坏了什么"，我为此浪费约 5 分钟查 `git status`）
- **置信度**：`实测`（复现）+ `读码推断`（原因：`tests/test_campaign_watcher_integration.py:54` `deadline = time.monotonic() + 20`，起 `pwsh` 子进程等墙钟）

### D-05 7 份 schema 只覆盖 1 个真正输出的 schema id

- **复现**：`python docs/audit/repro/check_schema_coverage.py`
- **实测**：`schema files: 7` / `distinct schema ids emitted by src: 20` / `covered: 1` / `NOT covered: 19`
- **应该是**：`schemas/` 要么覆盖全部，要么在 README 写明"仅联邦平面"
- **严重度**：`说假话`（放在仓库根、命名规整的 7 份文件，读者会当成全部标准）
- **置信度**：`实测`

### D-06 加第 8 份 schema 会被写死文件名的测试挡住

- **复现**：读 `tests/test_federation_schemas.py:14-22`
- **实测**：`assert {p.name for p in paths} == {7 个字面量文件名}`
- **应该是**：白名单要么有文档说明，要么改成按前缀 glob
- **严重度**：`崩溃`（加文件即红，报错只说集合不等）
- **置信度**：`读码推断`（未实际加文件）

### D-07 `README.zh-CN.md` 停在 v0.5.0

- **复现**：`grep -c coord-improvement README.md README.zh-CN.md` → `13` / `0`；`git log -1 -- README.zh-CN.md` → `fb6497f release: v0.5.0`
- **实测**：234 行 vs 86 行
- **应该是**：两份 README 同版本，或中文版顶部写明"本文对应 v0.5.0，最新见 README.md"
- **严重度**：`说假话`
- **置信度**：`实测`

### D-08 README 把两份 v0.3.0 的文档标成「总体系与能力状态」

- **复现**：`README.md:64`；`SYSTEM_OVERVIEW.zh-CN.md:17`；`EVOLUTION.md:25-27`
- **实测**：两份文档最后一段都写 v0.3.0，仓库是 0.6.15
- **应该是**：要么更新，要么在标题里写"（截至 v0.3.0）"
- **严重度**：`说假话`
- **置信度**：`实测`

### D-09 43 个命令零文档，也没有命令参考

- **复现**：`python docs/audit/repro/check_doc_commands.py`；`grep -rn "命令参考" docs/*.md` → 0
- **实测**：87 个 choice，README 提及 44，未提及 43；`coord-transcript` 在 `docs/` 0 命中，`coord-vision` 只在 `releases/v0.6.7.md` 出现一次
- **应该是**：一份 `COMMANDS.zh-CN.md`（可由 `--help` 半自动生成）
- **严重度**：`说假话`（`README.md:171-175` 的清单读起来是全量）
- **置信度**：`实测`

### D-10 `AGENT_OPERATION_GUIDE` 承诺四件事只讲一件半

- **复现**：`README.md:232` → 该文档 34 行
- **实测**：只有「发送判据」「错误处理」两节，0 条命令；没有"会话切换"做法，没有"失败重试"流程
- **应该是**：README 的措辞改成"发送证据判据见…"，或补全另外三件
- **严重度**：`说假话`
- **置信度**：`实测`

### D-11 4 跳接入路径全是散文，0 条命令

- **复现**：`README.md:232` → `skills/aep-agent-onboarding/SKILL.md`（39 行/0 命令）→ `templates/AGENT_JOIN_PROMPT.md`（15 行/0 命令）→ `templates/AGENT_OPERATOR_CONTRACT.zh-CN.md`（76 行/0 命令）
- **实测**：`grep -c "aep \|python -m"` 三个文件均为 0
- **应该是**：第一跳就给一条能跑的命令，再讲证据判据
- **严重度**：`说假话`（"使用 X 完成接入"，X 不含任何可执行步骤）
- **置信度**：`实测`

### D-12 已安装包元数据 0.2.0，且没有 `--version`

- **复现**：`pip show agent-evolution-protocol`；`aep --version`
- **实测**：`Version: 0.2.0`；`--version` → exit 2 `the following arguments are required: command`
- **应该是**：`__version__` 已经存在于 `__init__.py:3`，`add_argument("--version", action="version", …)` 一行即可
- **严重度**：`说假话`（`pip show` 报了一个不成立的版本号）
- **置信度**：`实测`

### D-13 空协作根上 `doctor` 崩溃，`coord-bootstrap` 零文档

- **复现**：`aep --coord-root <empty> doctor`
- **实测**：`protocol error: coordination doctor found missing required directories`，rc=2
- **应该是**：`doctor` 自己调用 bootstrap，或在报错里直接给出 `aep coord-bootstrap`
- **严重度**：`崩溃`
- **置信度**：`实测`

### D-14 新克隆里两个入口脚本都不可用

- **复现**：`pwsh -File .\aep.ps1 --help`；`pwsh -File .\aep-desktop.ps1 --help`
- **实测**：`Runtime receipt missing; install the portable core first` / `Desktop SDK missing; run scripts/bootstrap-desktop.ps1 explicitly`
- **应该是**：README 第 19/21 行补一句"先跑 `install-portable-python.ps1 -Install`"
- **严重度**：`崩溃`（**但报错信息本身写得很好**，是全仓库最好的一处）
- **置信度**：`实测`

### D-15 文档里的 `python` 是哪个，无任何说明

- **复现**：Git Bash 与 PowerShell 各跑 `python -m agent_evolution_protocol --help`
- **实测**：Git Bash → `No module named agent_evolution_protocol`；PowerShell → 正常（`D:\A\python` 3.13.9）
- **应该是**：README 顶部钉死解释器与验证命令
- **严重度**：`崩溃`
- **置信度**：`实测`

### D-16 `PROJECT_STANDARDS` §7 两条命令在新克隆不可能通过

- **复现**：`git ls-files .tools` → 0；文档 `:76` 第 3、4 条依赖 `.tools/agent-runtime/Scripts/python.exe`
- **应该是**：标注"仅在本机已有运行时的情况下"
- **严重度**：`崩溃`
- **置信度**：`实测`（`git ls-files`）+ `读码推断`（未在新克隆实跑这两条）

### D-17 `coord-claim` 两种模式返回结构不同

- **复现**：`coord-claim --agent x --task T --paths a.py` vs 不带 `--paths`
- **实测**：
  ```json
  {"schema":"agent-evolution-claim-result-v1","claimed":true,…,"gate":{"status":"ALLOWED","ignored_expired":[]},"claim_created":true}
  {"claimed":true,…,"paths":[],"expires_at":"…+00:00","gate":{"status":"TASK_ONLY","allowed":true}}
  ```
  差别：一个有 `schema` 一个没有；一个有 `claim_created` 一个没有；`gate` 子字段不同；时间戳一个 `+00:00` 一个 `Z`
- **应该是**：同一命令同一 schema，字段只增不减
- **严重度**：`静默失效`（解析器按一种写，另一种静默缺字段）
- **置信度**：`实测`

### D-18 大部分 CLI 输出没有 `schema` 字段

- **复现**：`python docs/audit/repro/probe_schema_fields.py . ./coord`
- **实测**：`coord-status` / `coord-digest` / `coord-onboarding` / `coord-provider-catalog` / `coord-delivery-audit` / `kernel-status` 全部 `schema=None`；`route` / `join` / `inspect` 输出 markdown
- **应该是**：要么统一带 `schema`，要么文档写明"哪些命令输出受 schema 约束"
- **严重度**：`静默失效`（`PROJECT_STANDARDS:38` 要求 schema 先行，实际无约束）
- **置信度**：`实测`

### D-19 CHANGELOG 缺 4 版、顺序错、v0.3.1 无 tag

- **复现**：`python docs/audit/repro/compare_versions.py`
- **实测**：`in CHANGELOG only: ['0.1.0','0.2.0']`；`release note only: ['0.3.0','0.3.1','0.4.1','0.4.2']`；`order correct: False`（`0.4.0` 排在 `0.1.0` 之后）；22 tag / 23 说明
- **应该是**：CHANGELOG 与 releases 对齐；`PROJECT_STANDARDS:65` 的发布流程含"annotated tag"
- **严重度**：`说假话`（CHANGELOG 是"历史"，但它不是完整历史）
- **置信度**：`实测`

### D-20 v0.6.14 发布说明写了不存在的 `coord-inbox`

- **复现**：`grep -n coord-inbox docs/releases/v0.6.14.md`；`aep coord-inbox --help`
- **实测**：`invalid choice: 'coord-inbox'`；`src/` 里 2 处提到，均为注释（含 `hold_alerts.py:12`）
- **应该是**：改名成 `coord-digest` 时同步清理发布说明与 docstring
- **严重度**：`说假话`
- **置信度**：`实测`

### D-21 没有术语表，8 个核心词无定义

- **复现**：`grep -rn "术语表\|词汇表\|Glossary" docs/*.md` → 0
- **实测**：`ACK` 74/87、`lease` 27/87、`handoff` 21/87 份文档出现，均无统一定义
- **应该是**：一份 `GLOSSARY.zh-CN.md`
- **严重度**：`说假话`（每份文档都假设读者知道这些词，读者不知道）
- **置信度**：`实测`

### D-22 `phase` 三套词表

- **复现**：`cli.py:88`；`improvement.py:12`；`NOVICE_LONGRUN_WORKFLOW:20`
- **实测**：9 个小写 / 7 个大写 / 5 个中文
- **应该是**：要么统一，要么在 README 说明"`--phase` 与 `--state` 是两套"
- **严重度**：`说假话`（`README.md:5` 的"同阶段"无法解析）
- **置信度**：`实测`

### D-23 加 `--dry-run` 无任何文档指导

- **复现**：`grep -rn "add_parser\|新增.*子命令" docs/*.md` → 0
- **实测**：需改 `cli.py:3204`（解析器，整行分号串写）与 `cli.py:1272`（处理函数），相隔 1900 行；`coord_claim` 自己 `print` 又 `return`（`:1289-1290`），不知道该在哪输出预览；137 个测试文件里找不到哪个是 `coord-claim` 的（最后靠名字猜到 `test_claim_conflicts.py`）
- **应该是**：一份"加一个子命令"的 10 行说明
- **严重度**：`只是难看`
- **置信度**：`实测`

### D-24 测试失败只给断言，不给源码位置

- **复现**：把 `claim_conflicts.py:135` 的 `<=` 临时改成 `>=`
- **实测**：`AssertionError: 'ALLOWED' != 'BLOCKED'` at `tests\test_claim_conflicts.py:252`，17 failed；**没有一条提到 `claim_conflicts.py`**
- **应该是**：失败信息里带被测函数名，或至少带模块路径
- **严重度**：`只是难看`（有 17 个失败做信号，方向不难猜）
- **置信度**：`实测`

### D-25 README 有第二个 H1

- **复现**：`grep -n "^# " README.md` → `1:` 与 `230:# Agent 接入与操作规范`
- **实测**：第 230-233 行像粘贴残留，与 `templates/AGENT_JOIN_PROMPT.md` 内容重复
- **应该是**：删掉，或改成 `##`
- **严重度**：`只是难看`（但它同时是 D-11 那条错误指路的来源）
- **置信度**：`实测`

### D-26 `-SkipInstall` 未文档化

- **复现**：`grep -rn SkipInstall docs/ README.md` → 0
- **实测**：`scripts/bootstrap.ps1:4` 有 `[switch]$SkipInstall`
- **应该是**：写进 `COORDINATION_CLI` 的初始化小节
- **严重度**：`只是难看`
- **置信度**：`实测`

---

## 7. 未覆盖 / 我判断不了

诚实列出，不装懂：

1. **`coord-dispatch` 的闸门拒绝路径**：我只测了"订单不存在"（exit 2 ✓）。真实闸门拒绝（12 项检查）需要构造一张合法订单，未做。`DISPATCH_GATE:142` 的"任一张单被拒 → exit 2"**未验证**。
2. **`coord-improvement validate` 失败返回 2**（`EVOLUTION_EXECUTION_PACT:150`）：需要可信工作区 + 提案，未做。
3. **`HANDOFF_OWNERSHIP:58` 的 BLOCKED → exit 2**：需要构造损坏记录，未做。
4. **v0.6.15 的 1058/3 与我的 1059/2**：环境差异，判不了（C-12）。
5. **`.tools/agent-runtime` 与 `D:\A\python` 的差异**：没有对照跑，不推测。
6. **`docs/coordination/` 下 43 份文档**：我只读了 4 份，其余未读。
7. **红队/SRE 已覆盖的运行时缺陷**：按 lead 要求未重复，也未验证他们的结论。

---

## 8. 请裁决的三个冲突点

### 裁决点 1：`.aep/workspace.json` 入库，是不是有意设计？

**冲突**：`.gitignore` 里明确忽略 `coordination/`、`.aep-kernel/`、`.tools/`（作者认定这些是本机状态，不该入库），但 `.aep/workspace.json`——**指向这些本机状态的指针**——入库了，且内容是绝对路径。

**两个可能的意图**：
- (a) 有意：让克隆者"接上"作者的现有工作区（协作场景下说得通）
- (b) 意外：`workspace-init` 直接 `atomic_write_json(workspace / WORKSPACE_CONFIG, config)`（`cli.py:2134`），没考虑入库

**为什么必须裁决**：如果是 (a)，那 D-01 不是 bug，但必须写进 README（"克隆后你的命令会写进作者的工作区，这是故意的"）；如果是 (b)，`coordination/` 被 ignore 而指针被 track，就是自相矛盾，必须移除。

**我的判断**：倾向 (b)。理由是 `.gitignore` 把 `coordination/` 当本机状态处理，而指针指向它——两者不可能同时成立。**但这是意图问题，我判不了，需要你定。**

### 裁决点 2：「被拒 exit 2」是约定还是巧合？

**冲突**：4 份文档承诺"被拒 exit 2"；`coord-claim` 作用域冲突实测 exit 3；`cli.py:3339` 只对 `agent-evolution-claim-result-v1` 这一个 schema id 特判成 3。

**两种解释**：
- (a) exit 3 是有意的"可重试/需人工"信号，只是没写文档
- (b) exit 3 是某次修补顺手加的，`main()` 里那个 `if result.get("schema") == …` 是**硬编码特例**，属于技术债

**为什么必须裁决**：如果 (a)，要补一份退出码表（并检查 `claim_conflicts.py:511-515` 那三处 `return 3` 与 `cli.py:3339` 是否一致）；如果 (b)，应该把 exit 3 收敛回 2，或改成通用的 `result["exit_code"]`。

**我的判断**：倾向 (b)。一个"按 schema 字符串匹配决定退出码"的 `if`，不像设计，像补丁。**但退出码是对外契约，改它会影响调用方，需要你定。**

### 裁决点 3：`README.md` 和 `README.zh-CN.md` 谁是主文档？

**冲突**：英文版 234 行到 v0.6.15，中文版 86 行停在 v0.5.0；但 `README.md` 里 6 段 0.6.x 功能摘要**全是中文写的**，`pyproject.toml` 的 `readme = "README.md"`，仓库作者名是中文。

**两种解释**：
- (a) 英文版是主文档，中文版是遗留的翻译，应该删掉或标"过期"
- (b) 中文才是目标读者，英文版是"为了开源门面"而膨胀出来的，中文版应该重建

**为什么必须裁决**：这决定了该修哪一份。如果是 (a)，正确动作是把 `README.zh-CN.md` 改成一行"本文已停止维护，见 README.md"；如果是 (b)，要花时间把 234 行的英文内容翻回去，而 D-07 会持续误导每一个中文新人。

**我的判断**：倾向 (a)——因为 `README.md` 正文本身就以中文为主，中文版的存在反而制造了"有两份权威文档"的错觉。**但这是产品定位问题，我判不了。**

---

## 附：改动与合规

**只改过两处源码，均已还原**（`git status --porcelain src/ tests/ schemas/` → 0 行）：

| 文件:行 | 改动 | 目的 | 还原 |
|---|---|---|---|
| `cli.py:3204` + `cli.py:1279` | 加 `--dry-run`（1 行参数 + 6 行预览分支） | D-23 | 反向 `Edit`，`git diff --stat` 空 |
| `claim_conflicts.py:135` | `expiry <= moment` → `>=` | D-24 | 改回，`tests/test_claim_conflicts.py` 35 passed |

**未触碰**：
- `coordination/`：`find coordination -type f -newermt "-90 minutes"` → **0 个文件**
- `.aep-kernel/`、`workspaces/`：未触碰
- 所有实验在 `K:\_aep_audit_tmp\` 下的副本 / 空目录
- 全量测试加 `CODEBUDDY_SAFE_DELETE_ENABLED=0`；基线 `1059 passed, 2 skipped`

**复现脚本**（`docs/audit/repro/`，均为只读，不写仓库状态）：

| 脚本 | 用途 | 对应 |
|---|---|---|
| `check_links.py` | md 相对链接校验 | 全文（68/68 通过） |
| `compare_versions.py` | CHANGELOG / releases / pyproject 三方对账 | C-08、D-19 |
| `check_schema_coverage.py` | `src/` 输出的 schema id vs `schemas/` | D-05、P-09 |
| `check_doc_commands.py` | 文档提到的 `coord-*` / `kernel-*` vs argparse | C-01、D-09、D-20 |
| `probe_schema_fields.py` | 10 个只读命令是否输出顶层 `schema` | D-18 |

> 注：`docs/audit/repro/` 下另有 `r01`–`r15`、`sre` 等脚本，非我所写，我未改动。
