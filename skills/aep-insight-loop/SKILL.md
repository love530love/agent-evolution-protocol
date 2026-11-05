---
name: aep-insight-loop
description: 从已落盘的事件流自动发现可复算的问题线索，并把通过的线索写入带证据哈希链的改进日志；只读预览为默认行为。
min_agent_capability: cli
model_required: false
---

# 自动发现问题与自动审计

## 何时用

满足任一条即可执行，**不需要有人先提出具体问题**：

- 一次长任务结束后，想知道这段时间有没有被忽略的失败模式；
- 协作区出现重复报错、未验证的弹窗处置、轨迹里的非零退出码；
- 需要给「我们该改什么」准备一份带证据的候选清单，而不是一句印象。

## 前置条件

1. 当前目录是仓库根 `K:\PythonProjects5\agent-evolution-protocol`（命令里的 `--workspace .` 指它）。
2. 能运行 `python -m ...` 并读取标准输出 JSON。不需要网络、不需要模型、不需要管理员权限。
3. 数据源可选存在，缺了不算错：`coordination/command_memory/observations/*.json`、`overlay_audit.jsonl`、`coordination/trajectories/*.json`。

## 步骤

1. 执行只读预览（**默认不写任何文件**）：

   ```
   python -m agent_evolution_protocol.coordination.insight_miner --workspace .
   ```

   若解释器不是已安装本包的那个，前面加 `set PYTHONPATH=src`（PowerShell 用 `$env:PYTHONPATH='src'`）。

2. 抄下回执里的四个字段：`scanned`、`candidates`、`rejected`、`deployment_performed`。

3. 先核对 `scanned` 里至少有一个数字大于 0。**若全为 0，不得写「没发现问题」**，只能写「没有读到数据」。

4. 按下面的「回执判定表」查表，得到结论和下一步。查表即可，不要自行分析成因。

5. 只有当第 4 步结论为「可以留痕」时，才在原命令末尾追加 `--apply` 重跑一次。

6. 抄下 `ingested[].state`。它只能是 `PROPOSED`。**不要**执行任何 `advance` / `validate` / 晋升命令——那是独立 reviewer 的事。

7. 需要人话标题时才加模型，且必须带「随用随走」参数：

   ```
   python -m agent_evolution_protocol.coordination.insight_miner --workspace . --propose --backend lmstudio --ttl 60 --gpu off
   ```

   模型只写标题、假设、检查方向；服务没开或加载失败会自动降级为纯规则结果，**不要去启动 LM Studio 服务**。

## 回执判定表

| 字段 | 取值 | 结论 | 下一步 |
|---|---|---|---|
| `sum(scanned.values())` | `0` | 没读到数据，不等于没问题 | 检查 `--workspace` 路径；停止 |
| `candidates` | `0` 且 `measurements` 中 count 有值 | 阈值未到，正常 | 结束；不执行第 5 步 |
| `measurements[*].triggered` | `false` 但 count>0 | 规则在跑，只是没到阈值 | 结束；不要改阈值凑结果 |
| `verified` | 非空 | 证据已重算通过 | 可进入第 5 步 |
| `rejected[].reason` | `evidence-changed` | 证据在扫描后变了 | 停止，升级给人类复核 |
| `rejected[].reason` | `evidence-drift-since-snapshot` | 已有提案的快照与新数据不一致 | 停止；**不要**重复 apply |
| `rejected[].reason` | `snapshot-*` | 快照无法复算 | 停止，升级给人类复核 |
| `applied` | `false` | 只是预览，零写入 | 正常 |
| `deployment_performed` | `false`（恒为） | 没改源码、没提交、没部署 | 正常 |
| `deployment_performed` | `true` | 异常，违反设计 | 立即停止并报告 |
| `model_status` | `disabled` | 纯规则跑完，未加载任何模型 | 正常 |
| `model_status` | `unavailable:*` | 模型离线，已降级 | 正常；不要启动服务 |
| `model_session.unloaded` | `true` | 模型已释放，未常驻 | 正常 |
| `ingested[].state` | `PROPOSED` | 只进候选池 | 结束；不要自行晋升 |
| `advisory_used` | >0 | 有人话建议文字 | 只当措辞，不当事实或授权 |

## 失败处置

- `rejected` 非空：一律「停止 + 升级给人类」，不得改阈值、删快照或重跑直到变绿。
- 命令报错或输出不是 JSON：停止，检查 `PYTHONPATH`；不要改用别的脚本猜结果。
- 模型相关失败（`lm-studio-server-off`、`model-load-failed`、`failed:*`）：忽略模型，纯规则结果照常有效；**绝不启动常驻服务**。
- 任何一步出现「要不要自己改代码修一下」的念头：停止。本能力卡不包含落地权限。

## 验收门

```
python -m pytest -q tests/test_insight_miner.py tests/test_local_model_session.py tests/test_capability_cards.py
```

期望全绿（当前 51 passed, 83 subtests）。该测试守四件事：证据必须可复算、模型文本不能进证据与事件身份、模型缺席/失败必须降级、快照冻结后 append-only 日志追加不会把提案卡死；第三份测试守能力卡不漂移。

补充自检：不带 `--apply` 跑第 1 步后，`coordination/insights/`、`coordination/insight_events/`、`coordination/improvements/` 都不应被创建。

## 边界

本卡片只做到「发现 + 审计留痕 + 生成候选」。`improvement.py` 恒为 `deployment_performed: false`，晋升 `ADOPTED` 需要 claim token、独立 reviewer 与回滚点。项目当前没有真正的隔离沙箱，因此**自动改源码不在能力范围内**。完整边界见 `docs/AUTONOMOUS_INSIGHT_LOOP.zh-CN.md`。
