# 审计报告：主程独立复核（2026-11-01）

本报告与 `AUDIT_20261101_RED/SRE/NEWHIRE` 相互独立。我**不是**在汇总它们，
而是自己开了一组探针，目的是：如果三组的结论和我冲突，我有独立证据可以裁决。

所有探针在 `.tools/probes-0616/`（`.tools/` 在 `.gitignore` 里，不入库）。
所有实验在 `tempfile.TemporaryDirectory()` 里跑，未触碰仓库内的 `coordination/` 与 `.agent-evolution/`。

---

## 摘要

八条，按「它让系统说了什么假话」排序。

| ID | 一句话 | 严重度 | 置信度 |
|---|---|---|---|
| A-01 | 真实采集路径从不设置 `role`/`direction`，所以**没有任何一条真实 ACK 能被接受** | 说假话 | 实测 |
| A-02 | `read_events` 返回**最旧**的 N 条，任何写过 500 条以上记录的 agent 从此无法被确认 | 说假话 | 实测 |
| A-03 | handoff 之后 ACK 闸门问的是继任者那张**从未发送**的订单，回执写 `not_sent` | 说假话 | 实测 |
| A-04 | `state.json` 无任何完整性保护，手改不可检测，且与事件日志永久背离 | 无法审计 | 实测 |
| A-05 | 880 处 `ValueError` vs 142 处 `ProtocolError`，`main()` 只捕后者 → 86% 的拒绝以 traceback + exit 1 逃逸 | 误导 | 实测 |
| A-06 | `--agents ""` 静默回退到全部内置客户端（7 个，含一个 `process`/`exe` 为空的幽灵条目） | 静默失效 | 实测 |
| A-07 | `--workspace "bad id"` 被静默忽略，另建一个新工作区，exit 0 | 说假话 | 实测 |
| A-08 | 代码声明 20 个 `schema` 名，`schemas/` 只有 7 份；且 7 份里 **0 份**能按名解析到文件 | 只是难看 | 实测 |

---

## A-01 真实采集路径从不设置 role，所以没有任何一条真实 ACK 能被接受

**这是本次审计最重的一条，它解释了前四个版本为什么一直在同一个问题上打转。**

### 证据

`ack_protocol.classify` 的第一、二道守卫：

```
src/agent_evolution_protocol/coordination/ack_protocol.py:13  if event.get("role") != "assistant":
src/agent_evolution_protocol/coordination/ack_protocol.py:16  if event.get("direction") != "incoming":
```

`transcript_bus.publish` 的默认值：

```
def publish(coord_root, *, agent, text, source="uia", session_id="",
            window_title="", role="unknown", direction="unknown", ...)
```

三个真实采集路径，**没有一个传 `role` 或 `direction`**：

| 位置 | 调用 | 传了 role？ |
|---|---|---|
| `coordination/transcript_patrol.py:137` | 巡逻采集 | 否 |
| `coordination/commander.py:443` | `perceive` 采集 | 否 |
| `cli.py:2752` | `coord-chat publish` | 否 |

全仓统计：

```
$ grep -rn 'role="assistant"' src/  | wc -l
0
$ grep -rn 'role="assistant"' tests/ | wc -l
17          # 分布在 6 个测试文件
```

**`src/` 里传 `role="assistant"` 的次数是 0。整个 ACK 子系统的前提条件，只有测试代码会制造。**

### 实测（`.tools/probes-0616/ack_role_never_set.py`）

真实路径写一条正确格式的 ACK，再让分类器判：

```
the event the real path wrote:
  role      : 'unknown'
  direction : 'unknown'
  session   : '4242'
  lines     : ['AEP-ACK:EVT-58DB5875B3']

the classifier's answer:
  accepted  : False
  state     : sent_unacked
  reason    : not_assistant
  rejected  : [('not_assistant', 'unknown')]

control: same text, captured with role=assistant (fresh root)
  role      : 'assistant'
  accepted  : True | state: acked
```

**分类器是对的。采集路径从来没给过它 role。**

### 后果

- 生产环境里**每一张订单永远是 `sent_unacked`**。
- `ack_policy="enforce"` 会把整个 DAG 永久钉死——这正好解释了为什么上一版不得不把它降级为 `record`。**用户的直觉是对的，但没人找到原因：不是规则太严，是这条规则的前提在现实中根本不成立。**
- `record` 模式下每条完成回执都写 `ack.verdict="hold"`，而对方其实回话了——**回执在说假话**。
- 1061 个测试全绿，因为 6 个测试文件自己伪造了 `role="assistant"`。

### 为什么四个版本都没抓到

因为测试与实现的**前提是同一批人写的**。测试断言「给定一条 assistant 方向的 incoming 事件，判定成立」——这句话本身没错，
错在**没人检查那条事件会不会真的出现**。这是「测试验证了一个不存在的系统」的教科书案例。

---

## A-02 read_events 返回最旧的 N 条，写过 500 条的 agent 从此无法被确认

### 证据

```
coordination/transcript_bus.py
    for path in sorted(folder.glob("*.json")):     # 文件名升序 = 时间升序
        ...
        rows.append(item)
        if len(rows) >= limit:
            break                                   # 到 limit 就停 = 取最旧的
```

调用方一律用 `limit=500` 且**不传游标**：

| 位置 | 用途 |
|---|---|
| `coordination/commander.py` ×3 | `check_acks` 轮询 |
| `federation/commander_adapter.py` | `ack_verdict`（闸门） |
| `federation/supervisor.py:196` | `_result_blocks`（产出回读） |

### 实测（`.tools/probes-0616/read_events_oldest.py`）

先写 520 条背景记录，再写那条该被看见的 ACK：

```
events written for alpha : 521
read_events(limit=500)   : 500
  first added_lines      : chatter 0
  last added_lines       : chatter 499
  contains the ACK?      : False        ← ACK 是第 521 条

the gate's verdict:
  state    : sent_unacked
  accepted : False
  advance  : False

  => the reply exists, is correctly attributed, and is invisible.
```

### 后果

- 一个长期在线的 agent，转录一旦超过 500 条，**它的 ACK 永远看不见**。判定会永久停在 `sent_unacked`。
- 同一个盲窗口也作用于 `_result_blocks`——**产出回读同样只看最旧的 500 条**，所以 `match` 会退化成 `None`，而回执会说「没找到可比的东西」。
- **没有任何测试覆盖截断方向**：`tests/test_transcript_bus.py` 只有一处 `limit=10`，且事件数远小于 10。

### 为什么危险

它和 A-01 叠加：A-01 让真实 ACK 根本进不来，A-02 让即便进来了也可能看不见。**两层独立失效，任何一层单独都足以让「没有 ACK」这个结论永远为真。**

---

## A-03 handoff 之后闸门问错了订单，回执写 `not_sent`

v0.6.14 与 v0.6.15 的发布说明都把它写成「已知 caveat，**未实测**」。现在实测了，而且比写的严重。

### 机制

`CommanderAdapter.order_for_task` 只返回**发给这个 agent 的**最新订单；supervisor 按 `task["owner"]` 选适配器。
handoff 之后 owner 变成继任者，于是闸门问的是继任者那张**从未发送**的订单 → `sent=False` → 判 `not_sent` → 放行。
前任那张 `MESSAGE_VISIBLE` 的订单还在盘上，仍然没人确认。

### 实测（`.tools/probes-0616/handoff_bypass_e2e.py`，`ack_policy="enforce"`）

```
order ... -> MESSAGE_VISIBLE, no ACK

step 1 (enforce, alpha reports done)
  held   : [('execute', 'hold_unacked', 'no_ack')]
  execute: running | owner: alpha

step 2 (enforce, alpha stalled -> handoff)
  scheduled: [('execute', 'switch_agent', ...)]
  execute  : running | owner: beta

step 3 (enforce, beta reports done -- no ACK was ever received)
  held   : []
  closed : [('execute', 'complete', 'not_sent')]
  execute: done

the original order, throughout:
  state     : MESSAGE_VISIBLE
  session   : 4242
  ever acked: NO
```

### 后果

1. `enforce` 承诺的「没有 ACK 不得推进」被一次 handoff 绕过。
2. 回执写 `ack.reason = "not_sent"`——**这句话是假的**：那张单确实发出去了（`MESSAGE_VISIBLE` + 会话 4242）。
   闸门如实报告了它看到的东西，但它看的是另一张单。

---

## A-04 state.json 没有完整性保护，手改不可检测

### 证据

`engine.load()` 只校验两件事：

```
if state.get("schema") != STATE_SCHEMA:      raise ValueError("unknown workspace state schema")
validate_workspace_id(state.get("workspace_id"))
if state["workspace_id"] != workspace_id:    raise ValueError("WORKSPACE_MISMATCH")
```

没有哈希、没有签名、**没有和 `events.jsonl` 做任何交叉校验**。而 `events.jsonl` 是 hash-chained 的。

### 实测（`.tools/probes-0616/state_tamper.py`）

把 `execute` 从 `running` 手改成 `done`：

```
events before edit: 4 | chain verify: {'valid': True, 'events': 4, 'head': 'a9bc261c...'}

hand-edited execute: running -> done
engine.load accepted it: done
chain verify after edit: {'valid': True, ...}          ← 链还是"有效"的

events that talk about this task:
  seq=  3 task_added
  seq=  4 task_assigned
  ...and none of them says 'done'.

step after the edit: {'execute': 'done'}
recover() raised nothing; state it returned: done
```

### 后果

- 值班工程师最自然的恢复动作（「改 JSON 让它动起来」）**完全不可检测**，而且会永久让日志变成「省略式假话」——日志没有说谎，但它和状态不再描述同一件事。
- `recover()` 校验链、校验快照，**不校验状态与链的一致性**。链「有效」只证明它内部自洽。
- 换句话说：**hash chain 保护的是历史，不是现在。而决定下一步做什么的是现在。**

---

## A-05 CLI 只捕获 ProtocolError，86% 的拒绝以 traceback 逃逸

```
$ grep -rhoE "raise [A-Za-z]+Error" --include=*.py src/ | sort | uniq -c | sort -rn
    880 raise ValueError
    142 raise ProtocolError
```

```
def main():
    ...
    except ProtocolError as exc:
        print(f"protocol error: {exc}", file=sys.stderr)
        return 2
```

只有 `ProtocolError` 被翻译成「拒绝 + exit 2」。其余 880 条（86%）直接冒泡。

实测：

```
--- federate step，不存在的 workspace ---
exit=1
    raise ValueError("workspace state not found")
ValueError: workspace state not found
```

对比同族的正确形态：

```
--- coord-ack 不存在的订单 ---
exit=2
protocol error: no order no-such-order
```

### 后果

脚本区分不了「我拒绝执行」和「工具崩了」。文档承诺「被拒 exit 2」，实际只有 14% 的拒绝路径兑现。

---

## A-06 `--agents ""` 静默展开成全部内置客户端

`_federate_agents()`：显式列表为空时回退到 `client_registry.known()`，而它是 `BUILTIN` 的全部键。

```
$ python -c "from ... import client_registry as cr; print(sorted(cr.BUILTIN))"
['agents', 'chatgpt', 'chrome', 'doubao', 'qoder', 'qoder_cn', 'workbuddy']
```

实测 `--agents ""` 的产物：

```
  "agents": ["agents","chatgpt","chrome","doubao","qoder","qoder_cn","workbuddy"],
  "started": [{"task": "execute", "agent": "agents", "order": "...", "event_id": "..."}]
```

其中 `"agents"` 是个幽灵条目：`process` 与 `exe` 都是空字符串，只有一个窗口标题 `"Agents"`。

### 后果

一个未设置的 shell 变量（`--agents "$AGENT_LIST"`）会把一次定向派工变成**对全部 7 个真实客户端的扇出**，并且 `exit=0` 报告成功。
`matches()` 有 `if process and ...` 保护，所以幽灵条目不会匹配到任意窗口——它只会永远找不到窗口然后失败。但那是一张发给不存在客户端的订单，且 intake 说它 `started`。

---

## A-07 `--workspace "bad id"` 被静默忽略

```
$ aep federate intake --workspace "bad id" --agents a --text x
{
  "schema": "aep-intake-v1",
  "workspace": "W-a5962ceca3ba4eb1ab7ec45d",   ← 不是我要求的
  ...
}
exit=0
```

我要求了一个工作区，得到了另一个，而工具说成功。

---

## A-08 schema 名到文件的映射全部断开

代码里声明了 **20 个** `schema` 名，`schemas/` 只有 **7 份**定义。

而这 7 份里，**文件名与它声明的 `const` 全部不一致**（文件名无 `aep-` 前缀，`const` 有）：

```
agent-capability-bundle-v1        -> aep-agent-capability-bundle-v1        否
dispatch-envelope-v1              -> aep-dispatch-envelope-v1              否
event-envelope-v1                 -> aep-event-envelope-v1                 否
execution-provider-catalog-v1     -> aep-execution-provider-catalog-v1      否
handoff-packet-v1                 -> aep-handoff-packet-v1                  否
task-envelope-v2                  -> aep-task-envelope-v2                   否
workspace-snapshot-v1             -> aep-workspace-snapshot-v1              否
```

另外 `execution-provider-catalog-v1` 的 `$id` 用的是 `agent-evolution-protocol.dev`，其余六份用 `.local`——`.dev` 是真实存在的 TLD 且强制 HTTPS。

### 后果

消费方拿到 `"schema": "aep-handoff-packet-v1"`，按名去找 `schemas/aep-handoff-packet-v1.schema.json`，**7 份全部 404**。这不是功能性缺陷，但它让「schema 是契约」这句话失去可执行性——而这个项目明令「不写第二套并存的标准」。

---

## 我认为最危险的三个

**A-01 第一，A-02 第二，A-03 第三。**

为什么是这三个：

- **A-01** 不是「某个分支写错了」，而是**整个 ACK 子系统的前提在生产中不成立**。它让前四个版本的讨论（`enforce` 还是 `record`、超限告警、签字放弃）全部建立在一个假前提上。而且它是**静默的**：没有任何日志说「role 缺失」，判定只是永远返回 `not_assistant`。
- **A-02** 是**第二层独立失效**，而且方向和 A-01 相反（A-01 让数据进不来，A-02 让进来的数据看不见）。两层叠加后，「没有 ACK」这个结论在任何情况下都为真——而它本来是要作为证据用的。
- **A-03** 让 `enforce` 的承诺失效，并且**在回执里写下一句可证伪的假话**（`not_sent` 之于一张 `MESSAGE_VISIBLE` 的单）。它最容易被忽略，因为每一步看起来都对。

A-04 排第四：它的严重性取决于「有没有人真的会手改」，但 A-01/A-02 是不需要任何人做错事就会发生的。
