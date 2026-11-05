# AEP 技能清单

> **本文件由 `aep coord-skill-manifest write` 生成，请勿手改。**
> 手改会让 `tests/test_skill_manifest.py` 失败——这是刻意的：
> 上一版研究文档把 `schema=aep-next-action-v1` 写错了整整两天，因为散文不会报错。

- 命令总数：**91**
- 风险分布：read-only 21、mutating 55、irreversible 15
- 用途已记录：**27** / 91（未记录 64，只允许减少）
- 清单摘要：`sha256:c5519cf764f128b3c7f4c656d362cc7cac21218af9f0b81f5c0a723dec1f2cfa`

风险分级是**声明**而不是推导：未登记的命令一律按 `mutating` 处理。
把会写的命令标成 `read-only` 是这里唯一有代价的错误，所以默认值朝安全方向倒。

用途优先取 argparse 的 `help`，取不到就退到处理函数的 docstring 首句。
标 `未记录` 的命令不是渲染问题——它在定义处就没有说明，`tests/test_skill_manifest.py`
把未记录数量钉成棘轮，只许降不许升。

## 工作区与回合（其他）

| 命令 | 风险 | 必填 | 用途 |
| --- | --- | --- | --- |
| `audit` | mutating | `--round-id` | *未记录* |
| `challenge` | mutating | `--round-id --actor --file` | *未记录* |
| `commit` | irreversible | `--round-id --card` | *未记录* |
| `decide` | mutating | `--round-id --actor --file` | *未记录* |
| `doctor` | read-only | `—` | *未记录* |
| `hold` | mutating | `--round-id --actor --reason` | *未记录* |
| `init` | mutating | `--round-id --task --leader --cells --budget` | *未记录* |
| `inspect` | read-only | `—` | *未记录* |
| `join` | mutating | `--agent` | *未记录* |
| `record-result` | mutating | `--round-id --result` | *未记录* |
| `resume` | mutating | `--round-id --actor --reason` | *未记录* |
| `reveal` | read-only | `--round-id --card` | *未记录* |
| `route` | mutating | `--agent --intent` | *未记录* |
| `status` | read-only | `--round-id` | *未记录* |
| `threat` | mutating | `--round-id --actor --file` | *未记录* |
| `workspace-init` | mutating | `—` | *未记录* |

## 协调面（coord-*）

| 命令 | 风险 | 必填 | 用途 |
| --- | --- | --- | --- |
| `coord-a11y` | mutating | `ACTION` | Report or set the Windows screen-reader flag that gates Electron UIA trees. |
| `coord-ack` | mutating | `--order-id` | Show the ACK judgement for one order, including why a token did not count. |
| `coord-ack-message` | mutating | `--agent --id` | *未记录* |
| `coord-ack-topic` | mutating | `--agent --topic` | *未记录* |
| `coord-adversarial` | mutating | `ACTION --task --submission` | *未记录* |
| `coord-agent-mcp` | mutating | `ACTION` | Manage per-Agent MCP bundles without claiming delivery or ACK. |
| `coord-archive-stale` | irreversible | `—` | *未记录* |
| `coord-assign` | irreversible | `--workspace --task --target` | One call that writes a DAG node, takes the lease and deposits the order. |
| `coord-battle-order` | irreversible | `—` | *未记录* |
| `coord-bootstrap` | mutating | `—` | *未记录* |
| `coord-capabilities` | mutating | `ACTION` | *未记录* |
| `coord-capability-plan` | read-only | `--intent --operation` | *未记录* |
| `coord-cdp` | irreversible | `ACTION` | Chrome DevTools Protocol transport: exact DOM read and composer write. |
| `coord-chat-ack` | mutating | `--agent --through` | *未记录* |
| `coord-chat-digest` | mutating | `--agent` | What has the rest of the legion said recently, for an agent just joining. |
| `coord-chat-publish` | irreversible | `--source --role` | *未记录* |
| `coord-chat-read` | mutating | `--agent` | *未记录* |
| `coord-claim` | mutating | `--agent --task` | *未记录* |
| `coord-claim-renew` | mutating | `--agent --task` | Extend the caller's claim lease through the normal ``aep`` entrypoint. |
| `coord-clawchat` | irreversible | `--sender --target --event-id` | *未记录* |
| `coord-client` | mutating | `ACTION` | Name, find, anchor and open the desktop clients the legion can command. |
| `coord-commander` | irreversible | `ACTION` | Queue directives, run the unattended loop, and prove every transition. |
| `coord-delivery-audit` | read-only | `—` | Read-only evidence ladder for all locally recorded deliveries. |
| `coord-delivery-status` | read-only | `--to --id` | Report observed mailbox state; never infer model receipt from file presence. |
| `coord-dependencies` | read-only | `--baseline --wheelhouse --output` | *未记录* |
| `coord-dependency-audit` | read-only | `--lock --output` | *未记录* |
| `coord-dependency-gate` | mutating | `--lock --audit-dir --inventory-dir --wheel-dir --output` | *未记录* |
| `coord-dependency-inventory` | read-only | `--lock --environment --output` | *未记录* |
| `coord-desktop` | mutating | `ACTION` | *未记录* |
| `coord-desktop-ipc` | mutating | `ACTION --workspace-id --agent` | Persist/recover Supervisor ↔ Desktop Runner events and receipts. |
| `coord-desktop-runner` | mutating | `ACTION --agent` | Plan/launch/wait for a GUI Agent in the interactive user session. |
| `coord-digest` | read-only | `--agent` | *未记录* |
| `coord-dispatch` | mutating | `—` | Preview and gate queued orders without touching the desktop. |
| `coord-evolution` | mutating | `ACTION` | *未记录* |
| `coord-governance` | mutating | `ACTION` | *未记录* |
| `coord-guided-retry` | mutating | `--attempted-action --error` | *未记录* |
| `coord-improvement` | mutating | `ACTION` | *未记录* |
| `coord-joint-case` | mutating | `ACTION --task --submission` | *未记录* |
| `coord-legion` | mutating | `ACTION` | *未记录* |
| `coord-loop-verdict` | mutating | `--event-id` | Recompute one handoff loop verdict from filesystem evidence. |
| `coord-mechanism-audit` | read-only | `--metrics-file` | *未记录* |
| `coord-onboarding` | mutating | `--agent` | *未记录* |
| `coord-orca` | mutating | `ACTION` | *未记录* |
| `coord-policy` | read-only | `ACTION --plan --window` | Validate a proposed action plan without touching the desktop. |
| `coord-process-probe` | read-only | `--pid` | *未记录* |
| `coord-provider-catalog` | read-only | `—` | List optional execution providers without installing or probing them. |
| `coord-providers` | mutating | `ACTION` | List, promote or retire optional execution providers. |
| `coord-reasoning` | mutating | `ACTION` | *未记录* |
| `coord-relay-drill` | mutating | `—` | One-shot relay drill: plan or execute in-turn multi-agent dispatch. |
| `coord-release` | mutating | `--agent --task` | *未记录* |
| `coord-route` | read-only | `--observation --window` | Replay a routing decision from recorded receipts, offline. |
| `coord-send` | irreversible | `--from --to --topic --kind` | *未记录* |
| `coord-session-set` | mutating | `--agent` | *未记录* |
| `coord-skill-manifest` | mutating | `—` | Describe the command surface from the parser that defines it. |
| `coord-status` | read-only | `—` | *未记录* |
| `coord-task-state` | mutating | `--agent --task --phase --goal` | *未记录* |
| `coord-trajectory` | mutating | `ACTION` | Record, check and replay a run without letting the replay re-fire it. |
| `coord-transcript` | irreversible | `ACTION` | Shared conversation bus so every agent can perceive every other session. |
| `coord-uia` | irreversible | `ACTION --agent` | Desktop transport probe: observe, capture or deliver to an original window. |
| `coord-vision` | read-only | `ACTION` | Ask a grounder where a control is, and compare it with a known answer. |
| `coord-volley-plan` | mutating | `--manifest-file --results-file --quota-remaining` | *未记录* |
| `coord-wake` | irreversible | `--from --to --task --reason` | *未记录* |
| `coord-wake-cancel` | irreversible | `--id --actor --reason` | Cancel only an unconsumed wake request; never retract adapter input. |
| `coord-wake-dispatch` | irreversible | `--id` | *未记录* |
| `coord-wake-status` | mutating | `—` | *未记录* |
| `coord-wheel-compare` | read-only | `--lock --wheelhouse --environment --output` | *未记录* |

## 联邦入口（federate）

| 命令 | 风险 | 必填 | 用途 |
| --- | --- | --- | --- |
| `federate` | irreversible | `ACTION` | Heartbeat, intake, status and revival for the persistent federation workspace. |

## 恢复内核（kernel-*）

| 命令 | 风险 | 必填 | 用途 |
| --- | --- | --- | --- |
| `kernel-checkpoint` | mutating | `--task-id --executor` | *未记录* |
| `kernel-continuation-plan` | read-only | `--agent --workspace` | *未记录* |
| `kernel-lease-acquire` | mutating | `--lease-type --resource --executor` | *未记录* |
| `kernel-lease-release` | mutating | `--lease-type --resource --executor --lease-epoch` | *未记录* |
| `kernel-op-reserve` | mutating | `--operation-key` | *未记录* |
| `kernel-op-transition` | mutating | `--operation-key --state` | *未记录* |
| `kernel-session-bind` | mutating | `--agent --provider --workspace --session-id` | *未记录* |
| `kernel-status` | read-only | `—` | *未记录* |

## 全局参数

| 参数 | 默认 | 用途 |
| --- | --- | --- |
| `--root` | `.agent-evolution` | — |
| `--coord-root` | `—` | local coordination root for coord-* commands |
| `--kernel-root` | `—` | local recovery kernel root for kernel-* commands |
