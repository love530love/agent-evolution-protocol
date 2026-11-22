---
name: aep-artifact-corroboration
description: 只读检查一个本地结果工件是否同时含事件 ID 与一次性校验串；严格把「工件佐证」限定为 advisory，绝不升级为 AGENT_ACK、投递证明或可推进状态。全程零副作用、零服务、零登录。
min_agent_capability: cli
model_required: false
---

# aep-artifact-corroboration

一个 Agent 收到桌面任务后可能生成一个结果文件。若该文件同时含**事件 ID**与
**一次性校验串**，这只说明写这个文件的人当时知道这两个值——是有用的**佐证**，
但**不是**原会话转写：任何人或本地进程都能写它，文件不能证明是谁写的、来自哪个
会话、任务结果是否被回读。**绝不能**据此标记 `AGENT_ACK` 或推进订单。

## 何时用

- 投递审计出现 `ARTIFACT_VERIFIED` 待补项，想判断某结果工件是否至少包含
  事件 ID + 校验串；
- 有人声称「对方在文件里回了我的校验串，算接单了」，需要按边界判定；
- 想确认一个工件佐证判定器的输出不会意外放宽 `ack_protocol` 的确认门槛。

**不适用于**：真正发送消息、唤醒/插话、判定对方 ACK、证明投递、证明任务结果已回读、
注册 OS 任务、连接任何需要账号的云端服务。本卡不碰窗口、不写订单。

## 前置条件

- 有 Python 与仓库 `src/` 可导入；**不需要**桌面窗口、管理员权限、模型、数据库、
  网络、`coordination/` 运行时目录或任何 API Key。
- 输入是一个 UTF-8 文件；超过 2 MiB 直接拒扫（防止不可信工件诱导扫描超大文件）。

## 步骤

1. **只读检查**（默认，零写入）：

   ```text
   python -m agent_evolution_protocol.coordination.artifact_corroboration <工件路径> \
     --event-id <EVENT_ID> --expected-ack AEP-ACK:<校验串>
   ```

2. **抄回执**：`status` / `corroborated` / `event_id_present` / `challenge_present` /
   `challenge_lines` / `artifact_sha256` / `proves.*` / `advisory_only` / `may_advance`。

3. **按回执判定表判断**，不要把「佐证成立」读成「对方确认」。

## 回执判定表

| 字段 | 结论 |
|---|---|
| `status=ARTIFACT_CORROBORATES_CHALLENGE` + `corroborated=true` | 工件同时含事件 ID 与校验串；**仅佐证文字**，不是 ACK |
| `status=CHALLENGE_UNBOUND_TO_EVENT` | 有校验串但无事件 ID ⇒ 不能绑定到该订单 |
| `status=EVENT_PRESENT_CHALLENGE_ABSENT` | 有事件 ID 但无该校验串 ⇒ 不是佐证 |
| `status=ARTIFACT_MISSING / TOO_LARGE / NOT_UTF8 / UNREADABLE` | 无可用文本证据；`proves.artifact_file_observed` 如实区分 |
| `proves.original_session_ack` | 恒 `false`——工件永远不能证明原会话 ACK |
| `proves.task_result_readback` / `proves.delivery` / `proves.acceptance` | 恒 `false`——不能证明结果回读、投递或接单 |
| `proves.artifact_authorship` / `proves.agent_identity` | 恒 `false`——不能证明谁写的、是哪个 agent |
| `advisory_only=true` + `may_advance=false` | 恒定；任何调用方都不得用它推进订单 |
| `DEPOSITED / MESSAGE_VISIBLE / ARTIFACT_CORROBORATES_CHALLENGE` | 都不是关单证据；**只有 `VERIFIED` 能关单** |

## 失败处置

- **禁止**把 `corroborated=true` 传给 `ack_protocol.may_advance` 或据此写 `AGENT_ACK`；
  真正确认只能用 `ack_protocol` 对可归属的原会话转写判定。
- **禁止**为了让汇总好看而把 `proves.*` 里任何恒 `false` 字段改成 `true`。
- **禁止**把这个 CLI 塞进派工/投递路径；它是审计辅助，不是执行器。
- 失败只允许三选一：**停止 / 延期 / 升级给人类**。

## 验收门

```text
python -m pytest -q tests/test_artifact_corroboration.py tests/test_capability_cards.py
```

全量回归本机须带 `CODEBUDDY_SAFE_DELETE_ENABLED=0`。模块 docstring 已写明佐证/确认
边界；判定语义与 `docs/DESKTOP_DELIVERY_PREFLIGHT.zh-CN.md` 的「只有 VERIFIED 能关单」
一致。
