# 严格原会话证明（v0.6.33）

## 目的与边界

这个开关解决的不是“窗口有没有打开”，而是发送前的窄问题：**当前可写输入框是否仍属于已登记的那一个原会话**。

它是五段真实验收链的第一道门，只允许进入输入阶段：

```text
定位原会话 → INPUT_SUBMITTED → MESSAGE_VISIBLE → AGENT_ACK → TASK_RESULT_READBACK
```

`session_attestation` 的 `valid=true` 和 `delivery_proven=false` 可以同时成立；前者绝不自动升级为消息可见、目标 ACK 或任务完成。

严格模式默认关闭。现有 Qoder、Qoder CN、WorkBuddy、WorkBuddy AI、CodeBuddy profile 未经本机实测标定前，**不要**为了“启用生产模式”直接打开它。

## 前置条件

1. 目标 profile 已单独登记，且有明确的 `session_marker`、`model_marker`、可信 `exe` / 可选 version-root 策略。
2. `<coord-root>/clients/fingerprints/<agent>.json` 已由单独的只读注册流程记录，且与当前窗口严格匹配；不存在记录或不匹配均拒绝输入。
3. 客户端可通过 UIA3 暴露足够的树、顶层 HWND 与 UIA runtime id。PowerShell UIA、整窗 OCR、CDP 和只会截图的 adapter 不是严格证明替代品。
4. 标定过程在同一显示缩放、同一客户端布局和同一原会话上完成；升级、窗口结构变化或 runtime id 改变后必须重新标定与重新证明。

## Profile 范式

以下是结构示例，不是可直接套到任何客户端的坐标。所有矩形都是目标窗口内部的归一化坐标 `[0, 1]`：

```json
{
  "agent": "qoder",
  "process": "Qoder",
  "title": "Qoder",
  "exe": "D:\\Programs\\Qoder\\Qoder.exe",
  "transport": "uia",
  "prefer_uia3": true,
  "session_marker": "已核验的原会话标题",
  "model_marker": "已核验的当前模型名称",
  "require_strict_session_attestation": true,
  "session_proof": {
    "session_region":  {"left": 0.25, "top": 0.00, "right": 0.70, "bottom": 0.25},
    "model_region":    {"left": 0.70, "top": 0.00, "right": 1.00, "bottom": 0.25},
    "selector_region": {"left": 0.00, "top": 0.00, "right": 0.25, "bottom": 1.00},
    "composer_region": {"left": 0.25, "top": 0.75, "right": 1.00, "bottom": 1.00}
  }
}
```

`selector_region` 必须与 `session_region`、`model_region`、`composer_region` 完全不相交。这样侧栏中同名标题只能作为一次受控切换候选，不能同时成为“已打开原会话”或“可写输入框”的证据。会话和模型在紧凑标题栏内可以相交；它们是两个不同 marker，仍必须各自唯一。

## 实际发送前的原子流程

在共享 `session_lease` 和 `desktop-input.lock` 内，活路径按以下顺序执行：

1. 读取 UIA3 树；只在 `session_region` 和 `model_region` 中寻找唯一 marker。
2. 若原会话尚未显示，只在 `selector_region` 中允许唯一候选的一次点击；点击后重新读取 1，不能以“已点击”代替证明。
3. 绑定 surface：可信实际 exe、PID、窗口标题、HWND 和 UIA runtime id 一起写入短期 attestation。
4. 对已登记 fingerprint 做严格比对；未知或不匹配都在写 artifact、输入或发送之前停止。
5. 只从 `composer_region` 选择输入控件，聚焦后立即再次读取活动会话和模型 marker，并与同一 surface 比对。
6. 仅当 5 成功时，才执行既有输入、发送、消息可见回读与 challenge ACK 流程。

UIA3 在第 6 步失效时，严格路线不会转去 OCR/PowerShell 的“尽力发送”。这会产生 `SESSION_ATTESTATION_REQUIRED`，不是可重试的盲发。

## 状态与证据

| 结果 | 含义 | 是否允许键入 |
|---|---|---|
| `ATTESTATION_VALID` | 本轮受限区域、fingerprint 与 surface 均匹配 | 仅允许进入既有发送链 |
| `SESSION_FINGERPRINT_REQUIRED` | 没有已登记且匹配的原会话指纹 | 否 |
| `SESSION_FINGERPRINT_MISMATCH` | 当前窗口与登记身份不同 | 否 |
| `ATTESTATION_ACTIVE_SESSION_NOT_VISIBLE` | marker 只在侧栏/不在受限活动区 | 否 |
| `ATTESTATION_SELECTOR_AMBIGUOUS` | 多个可能的会话行 | 否 |
| `ATTESTATION_RUNTIME_ID_UNREADABLE` | 无法绑定 UIA root runtime | 否 |
| `ATTESTATION_COMPOSER_NOT_IN_REGION` | 输入控件不在已标定 composer 区 | 否 |
| `ATTESTATION_SURFACE_CHANGED` | PID/HWND/runtime 或重新读取的活动会话变化 | 否 |

审计元数据（不含整段私聊正文）写入：

```text
<coord-root>/session_attestation_evidence/<agent>-<event-id>.json
<coord-root>/session_attestations/<agent>.json
```

它们仅记录本轮 proof 的摘要、证据来源和 surface；严格会话证明的短租约默认 300 秒，并且每次写入前仍重新证明，不复用旧记录授权输入。

## 标定与回滚

1. 先在只读模式观察本机窗口与 UIA3 树，记录窗口 DPI、布局、真实 header/model/composer 的矩形。
2. 以无害挑战串运行一次严格投递；验收必须独立看见五段链路，不能只看 CLI 成功、输入框文本或文件信箱。
3. 任何不唯一、树不足、运行时 ID 不可读或模型/会话标签漂移，保持 `require_strict_session_attestation=false` 并修正 profile，不要扩大区域规避拒绝。
4. 若需要即时回退，关闭该 profile 的严格开关；这只恢复该 profile 的既有受限路线，**不**让未知原会话获得发送权限，也不删除 attestation / lease 审计文件。

## 验收边界

v0.6.33 的自动化回归覆盖侧栏碰撞、候选歧义、无 fingerprint、UIA3 失败不降级、同 HWND 会话切换、surface/runtime 变化和按钮重寻窗。它们是源码逻辑证据。

在获得每个目标客户端的当日真实 `MESSAGE_VISIBLE → AGENT_ACK → TASK_RESULT_READBACK` 前，生产状态仍是 HOLD。
