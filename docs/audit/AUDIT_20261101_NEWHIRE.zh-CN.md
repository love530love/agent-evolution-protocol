# 新人审计：第 3 天，我照文档走了一遍

> 审计对象：`K:\PythonProjects5\agent-evolution-protocol` @ `627f7c5`（`git describe` = `v0.6.15`）
> 审计人：新入职第 3 天，之前没有参与过这个项目，没有内部 wiki，没有导师可问
> 方式：**逐字照文档做**，不做"经验性补全"。文档说敲什么就敲什么，报错原样记。
> 时间：约 75 分钟（从打开 README 到放弃并开始写这份报告）
> 复现脚本：`docs/audit/repro/`（5 个只读脚本，见文末）

---

## 我的一天

### T+0 打开 README

`README.md` 234 行。第一屏是 6 段 0.6.x 功能摘要，每段一个链接。我先做了唯一一件不需要任何背景的事：**验证链接是否存在**。

```text
file=README.md relative_links=68 missing=0
file=README.zh-CN.md relative_links=32 missing=0
```

68 个相对链接全部存在。**这是这个仓库做得最好的一件事，也是唯一一件我一次就做成的事。** 从这里开始往下全是坎。

### T+4 找"怎么开始"

README 里有 6 处"先读 X"：

| 位置 | 说什么 |
|---|---|
| `README.md:82` | **New here?** Start with the plain-language [Chinese introduction](docs/INTRODUCTION.zh-CN.md) |
| `README.md:186` | See [the beginner introduction], [the protocol], [the evolution history], and [the Chinese README] |
| `README.md:232` | 新加入 Agent 请先阅读 `skills/aep-agent-onboarding/SKILL.md`，并使用 `templates/AGENT_JOIN_PROMPT.md` 完成接入 |
| `AGENTS.md:7` | 先按 `docs/SYSTEM_OVERVIEW.zh-CN.md` 理解工作流与能力状态 |
| `AEP_JOIN.md:9` | What to do: 1. Treat this folder as the shared workspace … 3. run `aep route …` |
| `docs/AGENT_ONBOARDING_CONTRACT.zh-CN.md:7` | 读取项目 README、协议介绍、最新 status / digest，然后运行 4 条命令 |

我选了 `README.md:82` 指向的那份（因为它是"New here?"，语气最像给我看的）。

### T+8 读 `docs/INTRODUCTION.zh-CN.md`，照它做

229 行，写得清楚，是这 87 份文档里我唯一读得下去的一份。第 167 行「初学者第一次试用」给了完整步骤，第一步：

```powershell
python -m pip install -e .
```

**我没有执行这一条**，因为它会改我这台机器的环境。我改成检查当前状态：

```text
$ python -m pip show agent-evolution-protocol
Name: agent-evolution-protocol
Version: 0.2.0
Location: D:\A\Lib\site-packages
```

`pyproject.toml` 写的是 `version = "0.6.15"`。我以为是装了个旧版本，准备重装——先别急，接着走第二步。

### T+12 第二步失败

```powershell
python -m agent_evolution_protocol init --round-id demo --task example --leader lead --cells alpha beta catfish --budget 10
```

```text
C:\Users\love\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe: No module named agent_evolution_protocol
```

我当时的第一反应是：**"装错了，要重装"**。真实原因是 `python` 在这个 shell 里指向 `3.13.12`（workbuddy 自带），而 editable 安装装在 `D:\A\python`（3.13.9）。换成 `/d/A/python` 立刻成功：

```json
{
  "schema_version": 1,
  "round_id": "demo",
  "mode": "SHADOW",
  "created_at": "2026-11-01T15:59:41.746594Z"
}
```

后面 `commit / reveal / status / audit` 全部按文档跑通了：

```text
4378fa1ad6e0bb6e77a88a051cd193d2361abc8202e29eb967aaabc199b3a3e0
{"round_id":"demo","event_count":5,"valid":true,"errors":[],"head_sha256":"98fdf56a…"}
```

**结论：这份文档的代码是对的，但"用哪个 python"这件事从头到尾没有一个字。** 我在 Git Bash 里踩，在 PowerShell 里就没事（PowerShell 里 `python` 恰好是 `D:\A\python`）。这意味着：同一个新人，同一台机器，换个终端窗口，结果完全不同。

### T+18 顺手试了 `--version`

```text
$ aep --version
exit=2
aep: error: the following arguments are required: command
```

报错里完全没提 `--version`，只吐了两屏 usage。我第一秒的念头是"这个包装坏了"，而不是"没有这个参数"。

### T+22 读 `docs/COORDINATION_CLI.zh-CN.md`，照它做

第一条：

```powershell
pwsh.exe -File .\scripts\bootstrap.ps1 -CoordRoot coordination
```

它会 `pip install -e .`。我读了脚本，发现有未文档化的 `-SkipInstall`，用它跑，并且把 `-CoordRoot` 指到临时目录（不动仓库里的 `coordination/`）：

```text
{"coord_root": "K:\\_aep_audit_tmp\\fresh2\\coordination", "created": true}
{"coord_root": "…", "ok": true, "checks": [{"name": "python", "ok": true, "detail": "3.13.9"}, …]}
```

跑通了。然后文档「常用命令」的第一条是 `doctor`。我在一个**空**协作根上试：

```text
$ aep --coord-root ./coord doctor
protocol error: coordination doctor found missing required directories
rc=2
```

而同一个空协作根上，`coord-status` 却"成功"了——它返回一堆默认值，什么都不创建，什么都不报：

```json
{"coord_root": "K:\\_aep_audit_tmp\\fresh\\coord", "sessions": {"autoclaw": {…}, "codebuddy": {…}}}
```

正确做法是 `coord-bootstrap`，它存在、能用，**但 87 份文档里只有 `COORDINATION_CLI` 提到 `bootstrap.ps1`，没有一份文档提到 `coord-bootstrap` 这个命令**。

顺带发现 `doctor` 的检查是相对**当前工作目录**的，不是相对协作根：同一个协作根，在仓库根跑 `local_model: ok=true`，在临时目录跑 `local_model: ok=false, detail: "K:\_aep_audit_tmp\fresh\.models\Qwen3-0.6B"`。

### T+30 读 `docs/AGENT_ONBOARDING_CONTRACT.zh-CN.md`，四条命令

这份文档最像"给新人的"，它给的四条我全跑了：

```text
aep --coord-root coordination coord-onboarding --agent <agent>
aep --coord-root coordination coord-digest --agent <agent>
aep --coord-root coordination coord-status
aep --kernel-root .aep-kernel kernel-continuation-plan --agent <agent> --workspace <workspace>
```

`coord-onboarding` 的输出是**这一整天最有用的东西**——它直接把下一步要敲的命令拼好了：

```json
"unread_digest_command": "agent-evolution --coord-root … coord-digest --agent newhire",
"claim_command": "agent-evolution --coord-root … coord-claim --agent newhire --task <TASK>",
"rules": ["Read README / introduction / latest status board before acting.", …]
```

然后第二条就断了。`coord-digest --agent newhire`：

```json
{"agent": "newhire", "unread_count": 0, "topics": [], "session": {…}}
```

**空的。** 我不死心，换成已有的 agent 名试：

```text
--- workbuddy        unread= 1 topics= 1
--- codex            unread= 8 topics= 8
--- hermes_desktop   unread= 4 topics= 3
```

原来 digest 是**按 agent 名的定向信箱**，不是项目历史。所以契约里那句「读取上下文」对一个新人根本不成立：我不知道该用哪个 agent 名，用新名字就是空的，而文档从没说"你可以借用某个已有名字"或"项目历史在哪"。

第四条 `kernel-continuation-plan` 返回：

```json
{"action": "BLOCKED_HANDOFF_REQUIRED", "provider": "newhire", "session_id": null,
 "reason": "no resumable target session; automatic new session is forbidden"}
```

这个是对的（设计如此），文档也说了"缺少 session id 时写 digest 停止等待"。但配合上一条（digest 是空的），新人的实际处境是：**两个入口都告诉我"去读上下文"，而两条路都是空的，且没有任何一条告诉我上下文在哪。**

### T+38 换一条路：`skills/aep-agent-onboarding/SKILL.md`

README 说新 agent 要先读这个。39 行，**一条命令都没有**。它讲 UIA、PID 锚定、输入框清空、ACK 证据链——这些是我第 30 天才会用到的东西。它让我先读 `templates/AGENT_JOIN_PROMPT.md`，那个文件又让我先读 `templates/AGENT_OPERATOR_CONTRACT.zh-CN.md`，那个 76 行的文件里有 **0 条命令**。

README 指的路是 4 跳，4 跳全是散文。

### T+45 决定自己验证"新人到底能不能起步"：克隆一份

```text
$ git clone -q --depth 1 file:///K/PythonProjects5/agent-evolution-protocol clone1
$ cd clone1 && aep coord-status
{
  "coord_root": "K:\\PythonProjects5\\agent-evolution-protocol\\coordination",
```

**新克隆读到的是原作者机器上的协作目录。**

我盯着这一行看了很久。`.aep/workspace.json` 是入库的，里面是硬编码的绝对路径：

```json
{"coord_root":"K:\\PythonProjects5\\agent-evolution-protocol\\coordination",
 "kernel_root":"K:\\PythonProjects5\\agent-evolution-protocol\\.aep-kernel",
 "workspace":"K:\\PythonProjects5\\agent-evolution-protocol"}
```

而 `coordination/`、`.aep-kernel/`、`.tools/` 全在 `.gitignore` 里，克隆里根本没有这些目录。所以：**任何人克隆这个仓库、跑任何一条不带 `--coord-root` 的命令，读写的都是别人机器上的目录。** 读还好，`coord-claim` / `coord-send` / `coord-wake` 是写。

我又试了两个"统一入口"：

```text
$ pwsh -File .\aep.ps1 --help
Exception: K:\_aep_audit_tmp\clone1\scripts\runtime-common.ps1:16
Runtime receipt missing; install the portable core first
EXIT=1

$ pwsh -File .\aep-desktop.ps1 --help
Exception: K:\_aep_audit_tmp\clone1\aep-desktop.ps1:5
Desktop SDK missing; run scripts/bootstrap-desktop.ps1 explicitly
EXIT=1
```

这两个报错本身写得很好（直接告诉你该跑什么）。但 README 第 19、21 行把它们当"统一入口"介绍，没说要先装运行时。

### T+52 想跑测试，README 里没有一句话讲怎么跑

```text
$ python -m pytest --version
C:\Users\love\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe: No module named pytest
```

又是解释器问题。换 `/d/A/python` 才行。测试命令是我在 `docs/PROJECT_STANDARDS.zh-CN.md` 第 76 行「最小验收命令」里翻到的，README 里没有任何链接指向"怎么跑测试"。

全量跑：

```text
1059 passed, 2 skipped in 293.06s (0:04:53)
```

### T+60 跑第二遍，结果不一样

```text
1 failed, 1058 passed, 2 skipped in 257.52s (0:04:17)
```

我什么都没改。第三、四、五遍又都是 `1059 passed`。五次里挂一次。**我第一反应是"我刚才是不是动了什么"**——我去 `git status` 确认了三次，工作区是干净的。

（后来定位到是 `tests/test_campaign_watcher_integration.py` 里那个起 `pwsh` 子进程、等 20 秒墙钟的用例，详见 N-11。）

### T+66 想加一个 `--dry-run`

`grep -c add_parser src/agent_evolution_protocol/cli.py` → 83，文件 3343 行，`docs/` 里搜 `add_parser` / `新增子命令` → **0 命中**。

没有文档。我读代码加成了（改 2 处，见附录），也验证了能用。但整个过程我需要自己回答三个问题，文档一个都没答：参数加在哪个文件、行为写在哪一层、**要不要补测试**（`PROJECT_STANDARDS` §3 第 1 条说"新能力先写 Schema/接口和验收门"，但一条命令没说要动哪个测试）。

### T+70 想查 schema

`schemas/` 7 份。我写脚本把 `src/` 里所有 `"schema": "…"` 字面量捞出来对了一遍：

```text
schema files: 7
distinct schema ids emitted by src: 20
covered by a schema file: 1 -> ['aep-agent-capability-bundle-v1']
NOT covered: 19
```

20 个里只有 1 个有对应文件。而且有两套命名：`aep-*` 和 `agent-evolution-*`，没有任何文档解释这两个前缀的区别。

### T+72 想查术语

`grep -rn "术语表\|词汇表\|Glossary" docs/*.md` → **0 命中**。没有术语表。

`ack` 在 87 份文档里的 74 份出现，`lease` 27 份，`handoff` 21 份。而 `ack` 对应 3 个不同的命令、3 种不同的意思。

### T+75 放弃，写这份报告

---

## 发现清单

> 严重度：`走不下去`（卡死，做不下去）/ `走错方向`（能做，但会做错或白做）/ `浪费时间`（能做完，但要绕）/ `只是难看`
> 置信度：`实测`（我跑了，贴的是真实输出）/ `读码推断`（我没跑出结果，是从代码推的）

---

### N-01 新克隆里所有命令都指向原作者机器上的协作目录

- **我照做了什么**：`git clone` 到 `K:\_aep_audit_tmp\clone1`，`cd clone1`，`aep coord-status`
- **文档说**：`AEP_JOIN.md:22` — "The `aep` CLI auto-discovers `.aep/workspace.json` from this folder."
- **实际**：
  ```json
  {"coord_root": "K:\\PythonProjects5\\agent-evolution-protocol\\coordination", …}
  ```
  `.aep/workspace.json` 是入库文件，内容是硬编码绝对路径；`coordination/`、`.aep-kernel/`、`.tools/` 都在 `.gitignore` 里，克隆里不存在。
- **我当时的想法**：我以为"auto-discovers from this folder"是"从我所在的这个文件夹找"，结果它是"从入库的配置文件里读一个绝对路径，然后无视我现在在哪"。我克隆了一份仓库，却在对别人的目录说话。
- **严重度**：`走不下去`
- **置信度**：`实测`

> 影响面比"读错地方"大：`coord-claim` / `coord-send` / `coord-wake` / `coord-task-state` 都是写命令，走的同一个 `configured_root()`（`src/agent_evolution_protocol/cli.py:190`），新人第一条写命令就会落到别人的工作区里。

---

### N-02 文档里的 `python` 是哪个 python，全靠运气

- **我照做了什么**：照 `docs/INTRODUCTION.zh-CN.md:178` 敲 `python -m agent_evolution_protocol init …`；照 `docs/PROJECT_STANDARDS.zh-CN.md:79` 敲 `python -m pytest -q`
- **文档说**：`python -m agent_evolution_protocol …`
- **实际**：
  ```text
  # Git Bash
  python -m agent_evolution_protocol init …
  → No module named agent_evolution_protocol
  python -m pytest --version
  → No module named pytest

  # PowerShell（同一台机器，同一个仓库）
  python → D:\A\python.exe (3.13.9)，两条都正常
  ```
  原因：`python` 在 Git Bash 里解析到 `C:\Users\love\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe`，editable 安装装在 `D:\A\Lib\site-packages`（`__editable__.agent_evolution_protocol-0.2.0.pth` → `K:\…\src`）。
- **我当时的想法**：我以为是"没装依赖"，准备 `pip install -e .`。如果我真装了，会装进 `3.13.12`，机器上就有**两套安装记录**（`3.13.12` 一套新的、`D:\A` 一套 `0.2.0` 的旧元数据），而 `aep`（`D:\A\Scripts\aep.exe`）永远只用 `D:\A` 那套。我后来才知道两边指向同一个 `src/`，但当时我无从判断。
- **严重度**：`走不下去`（对 Git Bash 用户）；`浪费时间`（对 PowerShell 用户，只是疑惑）
- **置信度**：`实测`

---

### N-03 新 agent 的 digest 是空的，"先读 digest 拿上下文"对新人不成立

- **我照做了什么**：`aep --coord-root <copy> coord-digest --agent newhire`
- **文档说**：`docs/AGENT_ONBOARDING_CONTRACT.zh-CN.md:7` — "读取项目 README、协议介绍、最新 status / digest"；`docs/COORDINATION_CLI.zh-CN.md:60` — "新 agent 先读 digest / status / onboarding，再行动"
- **实际**：
  ```json
  {"agent": "newhire", "unread_count": 0, "topics": [], "session": {…}}
  ```
  同一个协作根（6887 个文件的历史），换已有名字：`codex` → `unread=8 topics=8`，`hermes_desktop` → `unread=4 topics=3`。
- **我当时的想法**：我以为 digest 是"项目当前状态摘要"。它是**发给某个 agent 的未读消息**。新人没有名字，就没有消息，就没有上下文——而这个"名字"本身是入职前就该知道的东西，文档没给。
- **严重度**：`走不下去`
- **置信度**：`实测`

---

### N-04 中文 README 停在 v0.5.0，和英文 README 不是同一份文档

- **我照做了什么**：先读 `README.zh-CN.md`（我是中文使用者，这是最自然的入口）
- **文档说**：`README.md:186` — "See … [the Chinese README](README.zh-CN.md)"，语气是"同一份文档的中文版"
- **实际**：
  | 关键词 | `README.md` | `README.zh-CN.md` |
  |---|---|---|
  | `coord-improvement` | 13 次 | **0 次** |
  | `PORTABLE_PYTHON` | 1 次 | 0 次 |
  | `self-test` | 1 次 | 0 次 |
  | `inspect-handoff` | 1 次 | 0 次 |
  | 行数 | 234 | 86 |

  `git log -- README.zh-CN.md` 最后一条是 `fb6497f release: v0.5.0`；`README.md` 最后一条是 `627f7c5`（v0.6.15）。**中文版落后 10 个 minor 版本。**
- **我当时的想法**：我读了中文 README，得到的能力清单里没有 0.6.x 的任何东西。如果我只看它，我会以为这个项目没有 `coord-improvement`、没有便携运行时、没有改进生命周期——而这些是当前 README 前 40 行的全部内容。
- **严重度**：`走错方向`
- **置信度**：`实测`

---

### N-05 README 把两份停在 v0.3.0 的文档标成"总体系与能力状态"

- **我照做了什么**：`README.md:64` → `docs/SYSTEM_OVERVIEW.zh-CN.md`；`README.md:186` → `docs/EVOLUTION.md`
- **文档说**：`[总体系与能力状态](docs/SYSTEM_OVERVIEW.zh-CN.md)`；`[the evolution history](docs/EVOLUTION.md)`
- **实际**：
  - `SYSTEM_OVERVIEW.zh-CN.md:17` 标题是 `## v0.3.0 能力与边界`，`## 版本关系` 里链接的是 `releases/v0.3.0.md`，并称"本次交付"。
  - `EVOLUTION.md` 最后一节 `## Current maturity` 第一句是 "v0.3.0 keeps Red Queen / Catfish / Creative Destruction as the umbrella system."
  - 87 份顶层文档里只有 2 份提到 `0.6.15`，而且都不是指南：`ACK_AND_SESSION_IDENTITY_20261101.zh-CN.md`（一份记录）和 `HANDS_AND_FEET_STATUS_20261031.zh-CN.md`（一份状态快照）。剩下两处命中是发布说明自己（`v0.6.14.md`、`v0.6.15.md`）。
- **我当时的想法**：我看到 "v0.3.0 能力与边界" 时以为自己在读一份 12 个版本前的存档，但它的链接文案是"能力状态"，现在时。我花了几分钟确认这到底是不是过期文档——因为如果是过期文档，那我该看哪份？
- **严重度**：`走错方向`
- **置信度**：`实测`

---

### N-06 87 个子命令里 43 个在两份 README 里一次都没出现，也没有命令参考

- **我照做了什么**：`aep --help`（87 个 choice）；然后统计两份 README 提到了多少个
- **文档说**：`README.md:171-175` 给了三组命令清单，看起来像完整目录
- **实际**：
  ```text
  total subcommands: 87
  mentioned in README(+zh): 44
  not mentioned: 43
  ['coord-a11y', 'coord-ack', 'coord-ack-message', 'coord-ack-topic', 'coord-adversarial',
   'coord-agent-mcp', 'coord-assign', 'coord-battle-order', 'coord-bootstrap', 'coord-capabilities',
   'coord-capability-plan', 'coord-cdp', 'coord-chat-ack', 'coord-chat-digest', 'coord-chat-publish',
   'coord-chat-read', 'coord-clawchat', 'coord-client', 'coord-commander', 'coord-delivery-status',
   'coord-desktop', 'coord-desktop-ipc', 'coord-desktop-runner', 'coord-dispatch', 'coord-evolution',
   'coord-governance', 'coord-joint-case', 'coord-legion', 'coord-mechanism-audit', 'coord-policy',
   'coord-process-probe', 'coord-providers', 'coord-reasoning', 'coord-route', 'coord-transcript',
   'coord-uia', 'coord-vision', 'coord-volley-plan', 'coord-wake-cancel', 'coord-wake-dispatch',
   'federate', 'hold', 'record-result']
  ```
  其中 `coord-transcript` 在整个 `docs/` 里 **0 命中**；`coord-vision` 只在 `docs/releases/v0.6.7.md` 出现过一次，没有任何使用说明。没有"命令参考"文档（`grep "命令参考\|command reference"` → 0）。
- **我当时的想法**：README 那三组清单读起来像"这就是全部命令"。我照它建了心智模型，然后 `aep --help` 吐出来 87 个，其中 `coord-governance`、`coord-legion`、`coord-volley-plan` 我完全不知道是干什么的，也没有任何文档可以查。唯一的信息源是 3343 行的 `cli.py`。
- **严重度**：`走错方向`
- **置信度**：`实测`

---

### N-07 6 个"先读什么"，4 跳全是散文、0 条命令

- **我照做了什么**：沿着 `README.md:232` 的路径走：SKILL.md → AGENT_JOIN_PROMPT.md → AGENT_OPERATOR_CONTRACT.zh-CN.md
- **文档说**：`README.md:232` — "新加入 Agent 请先阅读 `skills/aep-agent-onboarding/SKILL.md`，并使用 `templates/AGENT_JOIN_PROMPT.md` 完成接入"
- **实际**：
  | 文件 | 行数 | 里面有几条可执行命令 |
  |---|---|---|
  | `skills/aep-agent-onboarding/SKILL.md` | 39 | 0 |
  | `templates/AGENT_JOIN_PROMPT.md` | 15 | 0 |
  | `templates/AGENT_OPERATOR_CONTRACT.zh-CN.md` | 76 | 0 |
  | `docs/AGENT_OPERATION_GUIDE.zh-CN.md` | 34 | 0 |

  这四份都是"证据判据/角色契约"，讲的是**做对了怎么证明**，不是**第一步敲什么**。
- **我当时的想法**：我一开始很认真地读 SKILL.md，读到"UIA 控件树""输入框清空""DEFERRED/CEASED"时才发现这不是给我的——这是给已经在跑投递的 agent 的。我被指错了三次，每次都读完才发现。
- **严重度**：`走错方向`
- **置信度**：`实测`

---

### N-08 README 说 `AGENT_OPERATION_GUIDE` 讲四件事，实际只讲了一件半

- **我照做了什么**：`README.md:232` → "桌面投递、会话切换、失败重试和证据判据见 `docs/AGENT_OPERATION_GUIDE.zh-CN.md`"
- **文档说**：讲**四件**：桌面投递、会话切换、失败重试、证据判据
- **实际**：34 行，只有「最重要的发送判据」和「错误处理」两节。没有"会话切换"的具体做法（只说"不得新建会话绕过上下文"），没有"失败重试"的流程（只说 `DEFERRED` 不是失败）。全文 0 条命令。
- **我当时的想法**：我以为这里会有"怎么切换会话"的命令。没有。它是一份判据清单，不是操作手册。
- **严重度**：`走错方向`
- **置信度**：`实测`

---

### N-09 7 份 schema 只覆盖 1 个代码真正输出的 schema id

- **我照做了什么**：把 `schemas/*.json` 的 `properties.schema.const` 与 `src/` 里所有 `"schema": "…"` 字面量对账
- **文档说**：`docs/PROJECT_STANDARDS.zh-CN.md:38` — "新能力先写 Schema/接口和验收门，再写适配器"；"Schema ID 一旦发布不可复用"
- **实际**：
  ```text
  schema files: 7
  distinct schema ids emitted by src: 20
  covered by a schema file: 1 -> ['aep-agent-capability-bundle-v1']
  NOT covered: 19
  ```
  19 个裸奔的（节选）：
  ```
  agent-evolution-claim-result-v1     claim_conflicts.py
  agent-evolution-claim-gate-v1       claim_conflicts.py
  agent-evolution-route-v1            cli.py
  agent-evolution-workspace-v1        cli.py        ← 就是 .aep/workspace.json 里的那个
  aep-reasoning-contract-v1           reasoning_contract.py
  aep-readback-v1                     provider_readback.py
  aep-handoff-analysis-v1             handoff_worker.py
  aep-federate-status-v1              cli.py
  …
  ```
  而且有两套前缀：`aep-*`（7 份文件全用这个）和 `agent-evolution-*`（13 个输出用这个，0 份文件）。
- **我当时的想法**：`schemas/` 放在仓库根目录、7 份、命名规整，我以为是"全部标准"。真实情况是：**它只覆盖联邦平面，协作平面 20 个 schema 里 19 个没有文件**。我在 `coord-claim` 的真实输出里看到 `"schema": "agent-evolution-claim-result-v1"`，去 `schemas/` 找，没有。
- **严重度**：`走错方向`
- **置信度**：`实测`

---

### N-10 加第 8 份 schema 会被一个写死文件名的测试挡住，没文档告诉你

- **我照做了什么**：读 `tests/test_federation_schemas.py`，想看 schema 是怎么被验证的
- **文档说**：无（没有任何文档讲"加一份 schema 要动哪些地方"）
- **实际**：`tests/test_federation_schemas.py:14-22` 把 7 个文件名写成字面量集合：
  ```python
  assert {p.name for p in paths} == {
      "event-envelope-v1.schema.json", "handoff-packet-v1.schema.json",
      "dispatch-envelope-v1.schema.json", "agent-capability-bundle-v1.schema.json",
      "execution-provider-catalog-v1.schema.json", "task-envelope-v2.schema.json",
      "workspace-snapshot-v1.schema.json",
  }
  ```
- **我当时的想法**：我按 `PROJECT_STANDARDS` §3 第 1 条"新能力先写 Schema"，加一份文件，然后测试红了一片，而报错只会说集合不相等。我会花很久才找到是白名单。
- **严重度**：`浪费时间`
- **置信度**：`读码推断`

---

### N-11 全量测试偶发失败，且失败时你不知道是不是自己弄坏的

- **我照做了什么**：`CODEBUDDY_SAFE_DELETE_ENABLED=0 python -m pytest -q`，连跑 5 次，一行代码没改
- **文档说**：`docs/PROJECT_STANDARDS.zh-CN.md:79` — `python -m pytest -q`（这就是验收命令）
- **实际**：
  ```text
  第 1 次: 1059 passed, 2 skipped in 293.06s
  第 2 次: 1 failed, 1058 passed, 2 skipped in 257.52s   ← 同一条命令，同一个工作区
  第 3 次: 1059 passed, 2 skipped in 246.23s
  第 4 次: 1059 passed, 2 skipped in 292.46s
  第 5 次: 1059 passed, 2 skipped in 493.72s              ← 耗时翻倍
  ```
  第 2 次的失败用例名我没抓到（`grep` 把它过滤了）。后来用 4 个 watcher 相关测试文件批量跑 9 次，复现 1 次：
  ```text
  FAILED tests/test_campaign_watcher_integration.py::CampaignWatcherIntegrationTests::test_file_event_reconciles_expired_assignment
  1 failed, 23 passed in 125.15s
  ```
  单跑这个用例 6 次全过（每次 6.5s）。
- **我当时的想法**：我第一次看到 `1 failed` 时立刻去 `git status`，确认了三遍工作区是干净的，然后开始怀疑是不是我刚才 `--dry-run` 的实验没还原干净。**我浪费了大约 5 分钟怀疑自己。** 一个新人遇到这个，第一反应永远是"我弄坏了什么"。
- **原因（读码推断）**：该用例 `subprocess.Popen` 起 `pwsh.exe` 跑 `scripts/campaign-watch.ps1`，然后 `time.monotonic() + 20` 的墙钟循环等 `status == "timed_out"`（`tests/test_campaign_watcher_integration.py:54-62`）。机器上有别的 pwsh 子进程时，20 秒不够。这解释了"批量跑挂、单跑不挂""总耗时从 246s 漂到 493s"。
- **严重度**：`走错方向`
- **置信度**：`实测`（复现）+ `读码推断`（原因）

---

### N-12 `.tools/` 全被 gitignore，`PROJECT_STANDARDS` 的两条验收命令在新克隆里不可能通过

- **我照做了什么**：`docs/PROJECT_STANDARDS.zh-CN.md:76` 「最小验收命令」四条，逐条试
- **文档说**：
  ```powershell
  python -m pytest -q
  pwsh.exe -File .\scripts\install-capability-runtime.ps1 -WhatIf
  & .\.tools\agent-runtime\Scripts\python.exe -m pip check
  & .\.tools\agent-runtime\Scripts\python.exe -m agent_evolution_protocol coord-provider-catalog
  ```
- **实际**：后两条依赖 `.tools/agent-runtime/Scripts/python.exe`。`.gitignore` 第 9 行是 `.tools/`，`git ls-files .tools` → **0 个文件**。新克隆里这个路径不存在。
- **我当时的想法**：我把这四条当成"新人自检清单"。第三条开始我就不知道 `.tools/agent-runtime` 是什么、该由谁创建。`install-capability-runtime.ps1` 大概能建，但文档没串起来。
- **严重度**：`走错方向`
- **置信度**：`实测`（`git ls-files` 为 0）+ `读码推断`（未在新克隆实跑这两条）

---

### N-13 已安装包的元数据版本是 0.2.0，仓库是 0.6.15，且没有 `--version` 可对照

- **我照做了什么**：`pip show agent-evolution-protocol`；`aep --version`
- **文档说**：`pyproject.toml:7` `version = "0.6.15"`；`__init__.py:3` `__version__ = "0.6.15"`
- **实际**：
  ```text
  $ pip show agent-evolution-protocol
  Version: 0.2.0
  Location: D:\A\Lib\site-packages

  $ aep --version
  exit=2
  aep: error: the following arguments are required: command
  ```
- **我当时的想法**：`pip show` 说 0.2.0，我判断"装了个老版本，要重装"。重装之后 `pip show` 才会变成 0.6.15——但**新人怎么知道该不该重装？** 代码里明明有 `__version__`，CLI 却没有把它暴露出来。这是唯一一个能一秒回答"我跑的是哪版"的东西，而它不存在。
- **严重度**：`走错方向`
- **置信度**：`实测`

---

### N-14 没有术语表；`ack` 有 3 个命令 3 种意思，`lease` 有 2 套系统 2 个单位

- **我照做了什么**：`grep -rn "术语表\|词汇表\|Glossary" docs/*.md`；然后逐个词去 `--help` 里找定义
- **文档说**：无。87 份文档里没有一份定义这些词。
- **实际**：

  | 词 | 出现文档数 | 我找到的"定义" | 冲突 |
  |---|---|---|---|
  | `ack` | 74 / 87 | `AGENT_ACK`（`AGENT_OPERATION_GUIDE:29`）= 接单确认 | 命令有 **3 个**：`coord-ack --order-id`（订单级）、`coord-ack-topic --agent --topic`（主题级）、`coord-ack-message --agent --id`（消息级）。三者互不覆盖，谁也没说是子集关系 |
  | `lease` | 27 / 87 | 两套：`coord-claim --ttl-hours`（默认 48，小时）与 `kernel-lease-acquire --ttl-seconds`（默认 300，秒） | 两套系统、两个根目录（`coordination/` vs `.aep-kernel/`）、两个单位、都叫"租约"。`NOVICE_LONGRUN_WORKFLOW:23` 还发明了第三个词 "ACK lease" |
  | `order` vs `task` | — | `order_id`（dispatch envelope，有 ACK）、`task`（有 phase / claim） | 没有文档说这两者的关系。`coord-dispatch --order-id`、`coord-claim --task`、`coord-ack --order-id` 交替出现 |
  | `receipt` | — | `pad_receipts`、`ui_receipts`、completion `receipt` | 三种回执，三种目录 |
  | `handoff` | 21 / 87 | `handoff-packet-v1` schema、`inspect-handoff` 命令、`BLOCKED_HANDOFF_REQUIRED` 状态、`digest-first handoff` 策略 | 一个词四种用法，schema 里那个是数据包，`BLOCKED_HANDOFF_REQUIRED` 是"没有可续接会话" |

- **我当时的想法**：我读到 `README.md:5` 说"接替者的 `brief` 自动带上同阶段失败证据"，完全不知道 `brief` 是什么、`同阶段` 是哪个阶段的词表。我去查，发现既没有术语表，也没有一处把 `claim/ack/order/task/receipt/lease/handoff/phase` 串起来的文档。
- **严重度**：`走错方向`
- **置信度**：`实测`

---

### N-15 `phase` / `stage` / `状态` 有三套互不通用的词表

- **我照做了什么**：读 `cli.py:88`、`coordination/improvement.py:12`、`docs/NOVICE_LONGRUN_WORKFLOW.zh-CN.md:20`
- **文档说**：无统一说明
- **实际**：
  ```python
  # cli.py:88  —— coord-task-state --phase
  TASK_PHASES = ("todo","claimed","working","waiting","blocked","needs-review","ready","done","abandoned")

  # coordination/improvement.py:12 —— coord-improvement advance --state
  TRANSITIONS = {"PROPOSED": {...}, "VALIDATED": {...}, "REVIEWED": {...},
                 "TRIAL": {...}, "ADOPTED": {...}, "REJECTED": ..., "ROLLED_BACK": ...}
  ```
  ```text
  # NOVICE_LONGRUN_WORKFLOW:20 —— 第三种，中文
  研究、实现、验证、审查、交付
  ```
- **我当时的想法**：README 说"同阶段失败证据"，我不知道"阶段"指哪套。三套词表，一套小写九值、一套大写七值、一套中文五值，`--phase` 和 `--state` 两个参数名，没有任何一处交叉引用。
- **严重度**：`浪费时间`
- **置信度**：`实测`

---

### N-16 空协作根上 `doctor` 失败，而 `coord-bootstrap` 在 87 份文档里一次都没出现

- **我照做了什么**：`docs/COORDINATION_CLI.zh-CN.md:16` 第一条 → `aep --coord-root <empty> doctor`
- **文档说**：`aep --coord-root coordination doctor`
- **实际**：
  ```text
  protocol error: coordination doctor found missing required directories
  rc=2
  ```
  同一空根上 `coord-status` 返回 `{"coord_root": …, "sessions": {…}}`，rc=0，什么都不创建、什么都不提示。正确的命令是 `aep coord-bootstrap`（存在、能用、输出 `{"created": true}`）。
- **我当时的想法**：我以为 `doctor` 会自己修，或者至少告诉我缺什么。它输出了一大段 checks（`coord_root_exists: false`）然后 rc=2 退出。我是读 `scripts/bootstrap.ps1` 的源码才看到 `coord-bootstrap` 的——**文档里搜不到这个词**。
- **严重度**：`浪费时间`
- **置信度**：`实测`

---

### N-17 `doctor` 的检查相对当前工作目录，不相对协作根

- **我照做了什么**：同一个协作根，分别在仓库根和 `/k/_aep_audit_tmp/fresh` 下跑 `doctor`
- **文档说**：无
- **实际**：
  ```text
  # 在临时目录
  {"name": "local_model", "ok": false, "detail": "K:\\_aep_audit_tmp\\fresh\\.models\\Qwen3-0.6B"}
  # 在仓库根
  {"name": "local_model", "ok": true}
  ```
- **我当时的想法**：`--coord-root` 让我以为整个检查是围绕协作根的。实际它还在看 CWD 下的 `.models/`。我会为了一个"和我这个协作根无关"的失败去查模型目录。
- **严重度**：`浪费时间`
- **置信度**：`实测`

---

### N-18 README 把 `./aep.ps1` / `./aep-desktop.ps1` 当统一入口，新克隆里两个都跑不了

- **我照做了什么**：新克隆里 `pwsh -File .\aep.ps1 --help`
- **文档说**：`README.md:19` — "支持固定哈希在线/离线安装及 `./aep.ps1` 统一入口"；`README.md:21` — "独立入口 `./aep-desktop.ps1`"
- **实际**：
  ```text
  Exception: …\scripts\runtime-common.ps1:16
  Runtime receipt missing; install the portable core first
  EXIT=1

  Exception: …\aep-desktop.ps1:5
  Desktop SDK missing; run scripts/bootstrap-desktop.ps1 explicitly
  EXIT=1
  ```
- **我当时的想法**：README 第 19 行把 `./aep.ps1` 写成"无需系统 Python"的卖点，我立刻去试了。它确实不需要系统 Python——它需要一个 `./scripts/install-portable-python.ps1 -Install`。这条前置写在 `docs/PORTABLE_PYTHON.zh-CN.md` 里，README 没提。
  （**公平地说**：这两个报错信息本身写得很好，直接告诉了我该跑什么。这是全仓库最好的错误信息。）
- **严重度**：`浪费时间`
- **置信度**：`实测`

---

### N-19 加一个 `--dry-run` 要改 2 处，没有任何文档说改哪、要不要补测试

- **我照做了什么**：给 `coord-claim` 加 `--dry-run`（改完已还原，见附录）
- **文档说**：无。`grep -rn "add_parser\|新增.*子命令\|添加.*子命令" docs/*.md` → 0 命中
- **实际**：需要改两处，位置相隔 1900 行：
  ```python
  # src/agent_evolution_protocol/cli.py:3204（解析器，整行用分号串起来）
  claim = commands.add_parser("coord-claim"); claim.add_argument("--agent", …); …
  # src/agent_evolution_protocol/cli.py:1272（处理函数）
  def coord_claim(args: argparse.Namespace) -> dict:
  ```
  而且 `coord_claim` **自己 `print(json.dumps(...))` 又 `return result`**（`cli.py:1289-1290`），所以我不知道该在哪里输出预览——如果 `main()` 也打印返回值，就会打两遍。我读了 `main()` 才确认不会。
  更麻烦的是要不要补测试：`PROJECT_STANDARDS` §3 第 1 条说"新能力先写 Schema/接口和验收门"，但没说对应哪个测试文件；137 个测试文件里没有一个的命名能让我确定 `coord-claim` 的测试在哪（最后找到 `tests/test_claim_conflicts.py`，靠的是它名字里带 claim）。
- **我当时的想法**：我改了 6 行，功能是对的，但我不确定我有没有违反项目规范。这种"改对了但不知道自己合不合规"的感觉，比报错更让人不敢提交。
- **严重度**：`浪费时间`
- **置信度**：`实测`

---

### N-20 `coord-claim` 两种模式的返回结构完全不同，且 task-only 分支硬编码 `"paths": []`

- **我照做了什么**：`coord-claim --agent x --task T --paths a.py` 和 `coord-claim --agent x --task T`
- **文档说**：`docs/COORDINATION_CLI.zh-CN.md:68` — "不带 `--paths` 的旧任务级 claim 仍兼容，但也会写入默认 48 小时租约，并标记 `gate.status=TASK_ONLY`"
- **实际**：
  ```json
  // 带 --paths
  {"schema": "agent-evolution-claim-result-v1", "claimed": true, …, "gate": {"status": "ALLOWED", "ignored_expired": []}, "claim_created": true}

  // 不带 --paths
  {"claimed": true, …, "paths": [], "expires_at": "2026-11-03T16:55:07.808330+00:00", "gate": {"status": "TASK_ONLY", "allowed": true}}
  ```
  差别：一个有 `schema` 一个没有；一个有 `claim_created` 一个没有；`gate` 里一个有 `allowed`+`ignored_expired` 一个只有 `allowed`；时间戳一个带 `+00:00` 一个带 `Z`。
- **我当时的想法**：我在写解析器时卡住了——同一个命令的两种模式返回不同字段，我不知道该按哪个写。文档只说了 `gate.status` 会变，没说整个结构会变。
- **严重度**：`浪费时间`
- **置信度**：`实测`

---

### N-21 测试失败信息只给断言，不给源码位置

- **我照做了什么**：把 `claim_conflicts.py:135` 的 `expiry <= moment` 临时改成 `expiry >= moment`，跑 `tests/test_claim_conflicts.py`
- **文档说**：无
- **实际**：
  ```text
  def test_live_claim_still_blocks(self):
      …
  >       self.assertEqual(result["status"], "BLOCKED")
  E       AssertionError: 'ALLOWED' != 'BLOCKED'
  E       - ALLOWED
  E       + BLOCKED
  tests\test_claim_conflicts.py:252: AssertionError

  17 failed, 18 passed in 2.46s
  ```
  17 个失败，全部只指向测试文件的断言行。没有一条提到 `claim_conflicts.py`。
- **我当时的想法**：我知道是我改的那行（因为我改的），但如果这是我第 3 天接手的一个 bug，我会从 `'ALLOWED' != 'BLOCKED'` 开始 `grep`——先搜测试、再搜 `"BLOCKED"`、再找到 `gate()`、再找到 `_is_expired()`。4 跳。项目没有 conftest 里的定位辅助，也没有失败时打印调用栈的选项。
- **严重度**：`只是难看`（有 17 个失败做信号，方向不难猜）
- **置信度**：`实测`

---

### N-22 CHANGELOG 缺 4 个版本、顺序错乱；v0.3.1 有发布说明但没有 tag

- **我照做了什么**：`CHANGELOG.md` 的 `## x.y.z` 标题 vs `docs/releases/v*.md` vs `git tag`
- **文档说**：`docs/PROJECT_STANDARDS.zh-CN.md:65` 发布流程 —— "CHANGELOG → 版本号一致 → Git commit → annotated tag"
- **实际**：
  ```text
  pyproject version      : 0.6.15
  changelog entries      : 21
  release note files     : 23
  in CHANGELOG only      : ['0.1.0', '0.2.0']
  release note only      : ['0.3.0', '0.3.1', '0.4.1', '0.4.2']
  changelog order correct: False
    actual   : [… 0.5.1, 0.5.0, 0.2.0, 0.1.0, 0.4.0]     ← 0.4.0 排在 0.1.0 后面
    expected : [… 0.5.1, 0.5.0, 0.4.0, 0.2.0, 0.1.0]

  tags: 22，release notes: 23
  release note without tag: ['0.3.1']
  ```
- **我当时的想法**：我想按版本顺序读历史，读到 0.5.0 之后突然跳回 0.2.0、0.1.0，然后再出现 0.4.0。我以为文件被谁弄坏了。而 0.3.0/0.3.1/0.4.1/0.4.2 四个版本在 CHANGELOG 里根本不存在——**两份清单必须取并集才完整，但没有一处这么说。**
- **严重度**：`浪费时间`
- **置信度**：`实测`

---

### N-23 发布说明 v0.6.14 提到 `coord-inbox`，这个命令不存在

- **我照做了什么**：读 `docs/releases/v0.6.14.md`，看到 `coord-inbox`，去 CLI 里找
- **文档说**：`docs/releases/v0.6.14.md:62` — "仓库里已经有这套标准：`coord-send` 写这个路径，`coord-inbox`/`coord-digest` 读它"
- **实际**：87 个子命令里没有 `coord-inbox`。`src/` 里 0 处实现，但 `coordination/hold_alerts.py:12` 的 docstring 里也写了这个名字。
- **我当时的想法**：我在 `--help` 里翻了很久，怀疑是不是拼错了或者有别名。真相是它被改名成 `coord-digest` 了，但发布说明和代码注释都还留着旧名。**发布说明是"这个版本发生了什么"的唯一记录，它里面有一个不存在的命令，我不知道该信哪一半。**
- **严重度**：`走错方向`
- **置信度**：`实测`

---

### N-24 大部分 CLI 输出根本没有 `schema` 字段

- **我照做了什么**：10 个只读命令逐个跑，检查输出顶层有没有 `schema`
- **文档说**：`PROJECT_STANDARDS` §3 强调 Schema 先行
- **实际**：
  ```text
  coord-status                        rc=0 kind=json schema=None
  coord-digest --agent newhire        rc=0 kind=json schema=None
  coord-onboarding --agent newhire    rc=0 kind=json schema=None
  coord-provider-catalog              rc=0 kind=json schema=None
  coord-delivery-audit --summary-only rc=0 kind=json schema=None
  route --markdown                    rc=0 kind=markdown
  join --markdown                     rc=0 kind=markdown
  inspect --markdown                  rc=0 kind=markdown
  kernel-status                       rc=0 kind=json schema=None
  doctor                              rc=2 kind=json schema=None
  ```
  只有少数命令（`coord-claim`、`federate` 等）带 `"schema"`。
- **我当时的想法**：我想按 `PROJECT_STANDARDS` 的做法"先看 schema 再写解析器"。结果是：有的命令有 schema、有的没有，没有规律可循，也没有文档说哪些有。
- **严重度**：`走错方向`
- **置信度**：`实测`

---

### N-25 发布说明的测试数字我复现不出来

- **我照做了什么**：`docs/releases/v0.6.15.md:218` 说 "Windows `.tools/agent-runtime` 3.13.9 | **1058 passed, 3 skipped**"；我用 `D:\A\python` 3.13.9 跑
- **文档说**：1058 passed, 3 skipped
- **实际**：1059 passed, 2 skipped（干净时）；被跳过的两个都是 `tests/test_a11y.py:27/69: this assertion is about the non-Windows path`
- **我当时的想法**：差 1 个 passed、1 个 skipped。可能是 `.tools/agent-runtime` 和 `D:\A` 的环境差异，但我查不出是哪一条。**在一个"证据驱动"的项目里，发布说明的核心数字我复现不出来，而且没法自查原因。**
- **严重度**：`只是难看`
- **置信度**：`实测`

---

### N-26 README 有第二个 H1，最后 4 行是粘贴残留

- **我照做了什么**：`grep -n "^# " README.md`
- **文档说**：无
- **实际**：
  ```text
  1:# Agent Evolution Protocol
  230:# Agent 接入与操作规范     ← 第二个 H1，后面只有 3 行
  ```
  第 230-233 行像是从别处粘过来的一段，和 `templates/AGENT_JOIN_PROMPT.md` 的内容重复。
- **我当时的想法**：我读到第 230 行时以为 README 结束了，下面是另一个文件。它打断了我的阅读，且它是"新加入 Agent 请先阅读 SKILL.md"这个错误指路的来源（见 N-07）。
- **严重度**：`只是难看`
- **置信度**：`实测`

---

## 文档重复与矛盾汇总

### 同一件事有几份文档在讲

| 主题 | 文档数 | 它们一致吗 |
|---|---|---|
| "新 agent 第一步做什么" | 8 份直接给出「第一步」，另有 6 份给出阅读顺序/前置条件 | 不一致，见下表 |
| `ack` | 74 / 87 | 三个命令、三种粒度，无交叉说明 |
| `lease` / 租约 | 27 / 87 | 两套系统、两个单位（hours / seconds）、两个根目录 |
| `handoff` | 21 / 87 | 一个词四种用法（数据包 / 命令 / 阻塞状态 / 策略名） |
| "总体系是什么" | 5（README、SYSTEM_OVERVIEW、EVOLUTION、INTRODUCTION、COLLABORATIVE_REASONING） | 两份停在 v0.3.0，三份在 0.6.x |

### 8 份文档给了 8 个不同的「第一步」

| 文档 | 它说的第一步 |
|---|---|
| `README.md:82` | 读 `docs/INTRODUCTION.zh-CN.md` |
| `README.md:232` | 读 `skills/aep-agent-onboarding/SKILL.md` |
| `AGENTS.md:7` | 读 `docs/SYSTEM_OVERVIEW.zh-CN.md` |
| `AEP_JOIN.md:9` | 跑 `aep route …` |
| `docs/COORDINATION_CLI.zh-CN.md:8` | 跑 `pwsh -File .\scripts\bootstrap.ps1 -CoordRoot coordination` |
| `docs/AGENT_ONBOARDING_CONTRACT.zh-CN.md:10` | 跑 `aep --coord-root coordination coord-onboarding --agent <agent>` |
| `docs/NOVICE_LONGRUN_WORKFLOW.zh-CN.md:26` | 读自己的定向信箱 |
| `docs/EXECUTION_PLANE_HANDOFF_20261030.zh-CN.md:7` | 先读本文件 |

`README.md:82`、`README.md:232`、`AGENTS.md:7` 这三条**在同一个仓库的同一批入口文件里**，给出三个不同的"先读什么"，谁也没引用谁。

### 明确的矛盾

1. **能力状态**：`README.md:64` 称 `SYSTEM_OVERVIEW` 是"总体系与能力状态"，该文档写的是 v0.3.0；README 自身描述的是 0.6.15。
2. **命令清单**：`README.md:171-175` 列出 33 个命令（治理 7 + 协作 18 + 内核 8），`aep --help` 有 87 个。清单没有"以下为部分"的说明。
3. **中文/英文 README**：`README.md:186` 把 `README.zh-CN.md` 当同一份文档，实际落后 10 个版本。
4. **`coord-inbox`**：`docs/releases/v0.6.14.md:62` 说它存在，CLI 里没有。
5. **CI 覆盖**：`README.md:62` 说 "CI covers Ubuntu/Windows and Python 3.10/3.12"，`.github/workflows/test.yml` 还有第三个 job 跑 Python 3.13（`offline-desktop-sdk`）。不是错，是不全。
6. **哪份更新**：没有元信息。87 份文档里只有 15 份文件名带日期（`*_20261031` 之类），其余 72 份没有版本、没有日期、没有"本文对应 vX.Y"。**判断新旧只能靠 `git log`。**

---

## 术语表现状（我实际拼出来的，不是文档给的）

| 词 | 我最终认定的意思 | 依据 | 有没有文档统一解释 |
|---|---|---|---|
| `claim` | 任务级或文件级的占用声明，带 `expires_at` | `coord-claim` 输出 | 没有 |
| `lease` | **两套**：(a) `coord-claim` 内部那个带 `expires_at` 的占用；(b) `kernel-lease-acquire` 的资源锁，单位是秒 | 两个命令的 `--help` | 没有 |
| `ack` | **三套**：订单级（`coord-ack --order-id`）、主题级（`coord-ack-topic`）、消息级（`coord-ack-message`） | 三个 `--help` | 没有 |
| `order` | 一次派工，有 `order_id`，是 ACK 的对象 | `dispatch-envelope-v1` schema | 没有 |
| `task` | 工作单元，有 `phase`、可被 claim，ID 形如 `T125` / `BUG-42` | `coord-task-state --task` | 没有 |
| `receipt` | **三种**：`pad_receipts`、`ui_receipts`、completion receipt | `.gitignore` 里的目录名 | 没有 |
| `handoff` | **四种**：`handoff-packet`（数据包）、`inspect-handoff`（命令）、`BLOCKED_HANDOFF_REQUIRED`（无会话可续）、`digest-first handoff`（策略） | 各文档 | 没有 |
| `phase` | **三套词表**：9 个小写（task）、7 个大写（improvement）、5 个中文（novice 文档） | 代码 + 文档 | 没有 |

---

## 版本考古

```text
pyproject.toml        0.6.15
__init__.py           0.6.15   （但 CLI 没有 --version，看不到）
pip show              0.2.0    ← 元数据陈旧，从未随版本号重装
git describe          v0.6.15
docs/releases/        23 份，最新 v0.6.15（与 pyproject 一致）
git tag               22 个，v0.3.1 有发布说明但无 tag
CHANGELOG.md          21 条，缺 0.3.0 / 0.3.1 / 0.4.1 / 0.4.2，0.4.0 排在 0.1.0 之后
README.zh-CN.md       停在 v0.5.0
SYSTEM_OVERVIEW       停在 v0.3.0
EVOLUTION.md          停在 v0.3.0
```

**发布说明里写了、代码里找不到的**：`coord-inbox`（v0.6.14）。

**我抽查并确认对得上的**：v0.6.15 说的 `step(..., ack_policy=)` 双值策略、`_ack_blocks` 返回 `{verdict, reason}`、默认 `"record"` —— `src/agent_evolution_protocol/federation/supervisor.py:80-135` 全部一致。**这份最新发布说明是可信的**，这让我对 N-23 更困惑：同一批文档，一份准到行号，一份写着不存在的命令。

---

## 附：我改了哪几行、怎么改回的

按任务要求做了两个实验，都**立刻还原**，并用 `git diff` 确认：

**实验 1（N-19，加 `--dry-run`）**
- `src/agent_evolution_protocol/cli.py:3204`：在 `coord-claim` 的 `add_parser` 那一行追加 `claim.add_argument("--dry-run", action="store_true");`
- `src/agent_evolution_protocol/cli.py:1279`（`paths = list(...)` 之后）：插入 6 行，`dry_run` 为真时打印预览并 return，不写盘。
- 验证：`coord-claim --dry-run` 输出预览且 `coord/claims` 目录未被创建；不带 `--dry-run` 时正常写入。`tests/test_claim_conflicts.py` 35 passed。
- 还原：两处 `Edit` 反向改回。`git diff --stat` → 空。

**实验 2（N-21，注入 bug）**
- `src/agent_evolution_protocol/coordination/claim_conflicts.py:135`：`expiry <= moment` → `expiry >= moment`
- 结果：17 failed, 18 passed
- 还原：改回 `<=`，重跑 35 passed。`git status --porcelain src/ tests/` → 空。

**没碰的东西**（已核验）：
- `coordination/`：`find coordination -type f -newermt "-90 minutes"` → 0 个文件
- `.aep-kernel/`、`workspaces/`：未触碰
- 所有实验都在 `K:\_aep_audit_tmp\` 下的副本 / 空目录里跑；仓库内 `coordination/` 只被只读命令读过
- 全量测试加 `CODEBUDDY_SAFE_DELETE_ENABLED=0`

**复现脚本**（`docs/audit/repro/`，全部只读，不写仓库状态）：
| 脚本 | 用途 | 对应发现 |
|---|---|---|
| `check_links.py` | 校验 md 相对链接 | 全文（68/68 通过） |
| `compare_versions.py` | CHANGELOG / releases / pyproject 三方对账 | N-22 |
| `check_schema_coverage.py` | `src/` 输出的 schema id vs `schemas/` | N-09 |
| `check_doc_commands.py` | 文档提到的 `coord-*` / `kernel-*` vs argparse | N-06、N-23 |
| `probe_schema_fields.py` | 10 个只读命令是否输出顶层 `schema` | N-24 |

---

## 如果我是导师，我会先修哪三处文档

### 第一处：在 README 最顶上放一个 15 行的「60 秒起步」，把解释器钉死

**为什么**：我一天里反复卡在同一个根因上——**不知道该用哪个 python**（N-02 的两条命令失败、N-13 的 `pip show` 版本对不上、外加 `pytest` 找不到）。这不是三个 bug，是一个缺失的段落。它是唯一一个"修 15 行、消掉 2 条发现"的改动。

具体要写死的：
```text
本仓库的开发解释器是 D:\A\python（3.13.9），aep 在 D:\A\Scripts\aep.exe。
Git Bash 里的 python 是 3.13.12，没有装本包。命令请在 pwsh 7 里跑。
验证：python -c "import agent_evolution_protocol as m; print(m.__file__)" 必须指向 src/
```

**不改的代价**：每个新人第一次照抄文档就失败，然后花 20 分钟怀疑是不是自己装错了——而这 20 分钟里他会去 `pip install -e .`，把环境搞成两份安装。

---

### 第二处：让 `.aep/workspace.json` 不要入库，或者改成相对路径 + 首次运行拒绝跨仓库

**为什么**：N-01 不是"难看"，是**数据安全事故**。任何人克隆这个仓库、跑任何一条不带 `--coord-root` 的写命令（`coord-claim` / `coord-send` / `coord-wake`），都会写进原作者的工作区。而 `.gitignore` 已经证明 `coordination/` 不该入库——那为什么指向它的指针入库了？

这一条比第一处严重得多，但它只需要一个人改一次；第一处是每个新人都会踩。所以我把它放第二。

最小修法（两选一）：
- `.aep/workspace.json` 从版本库移除，改由 `aep workspace-init` 生成；或
- `configured_root()` 在读到绝对路径且该路径不在当前仓库树下时，**报错并拒绝执行**，而不是静默采用。

---

### 第三处：写一份 `docs/GLOSSARY.zh-CN.md`，把 8 个词钉死，并给 87 个命令加一页索引

**为什么**：N-14、N-15、N-06 是同一个病：**这个词在这个仓库里指什么，取决于你在读哪份文档。** `ack` 三个命令、`lease` 两套系统两个单位、`phase` 三套词表、43 个命令零文档。我花在"这个词到底指什么"上的时间，比花在"这个功能怎么用"上的还多。

这一处工作量最大（要写 8 个定义 + 87 行命令索引），但它是唯一一处**能让第 4 天的新人不再问"ack 是哪个 ack"**的改动。而且它可以由 `aep --help` 半自动生成，一次性成本并不高。

---

## 最后：哪些是我的问题，不是文档的问题

我得说清楚，免得这份报告被当成"文档什么都缺"：

**这些是文档没写，我卡住了**：
- 用哪个解释器（N-02）
- 新 agent 的上下文从哪来（N-03）
- 43 个命令是干什么的（N-06）
- 8 个术语的定义（N-14）
- `.aep/workspace.json` 指向别人机器（N-01）

**这些是我需要学，文档不欠我**：
- `commit-reveal` 的密码学含义 —— `INTRODUCTION.zh-CN.md` 讲得很清楚，我读懂了
- `红皇后 / 鲶鱼 / 创造性破坏` 三个隐喻 —— 同上，讲得比大多数商业书好
- UIA / PID 锚定 / 输入框清空那套投递证据链 —— 这是领域知识，`SKILL.md` 写全了，只是**它不该是新人第一份该读的东西**
- `dispatch envelope` 的 17 个必填字段 —— 有 schema 文件，我能读

**这些是我读了三遍还是不懂的**（真实困惑，不是抱怨）：
1. `README.md:5` — "接替者的 `brief` 自动带上同阶段失败证据"。`brief` 是命令（`coord-improvement brief`）还是数据结构？"同阶段"是 task phase 还是 improvement state？读了三遍，卡在 `brief` 和 `同阶段` 这两个词上。
2. `README.md:9` — "`coord-improvement reroute` 支持已证明尚未输入的订单受控改派"。"已证明尚未输入"是什么证据等级？"输入"是指桌面输入吗？我在 `PROJECT_STANDARDS` 的 L0-L3 里找不到对应。
3. `docs/AGENT_OPERATION_GUIDE.zh-CN.md:23` — "派工必须保持原 `event_id` 并进入 `AGENT_BUSY/DEFERRED`"。`AGENT_BUSY/DEFERRED` 是一个状态值还是两个？斜杠是"或"还是"层级"？`DEFERRED` 在 `:29` 单独出现过，`AGENT_BUSY` 再没出现过。

这三个我都没有猜，因为**猜出来的解释会变成下一个人读到的"文档说的"**。
