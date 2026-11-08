---
name: aep-delivery-fixture
description: 离线复算桌面投递回执的四份真机夹具；用 requiredEvidence 守住「缺证据即 uncertain」，验证布尔假阳性、OCR band 假阴性与不确定结果不被误判。全程只读、零服务、零登录。
min_agent_capability: cli
model_required: false
---

# aep-delivery-fixture

把四次真机投递的判定失败/成功固化成**可复算、自包含、不依赖运行时文件**的夹具，
让判定器以后不会被「回执里写了成功」或「OCR band 切错位置」带偏。
**本卡不是投递能力**：不打开窗口、不打字、不启动适配器、不连任何云端服务、不登录。

## 何时用

- 改过 `action_verification` / `delivery_preflight.postflight` /
  `uia_transcript_verdict` 之后，要确认判定语义没有漂移；
- 真机回执出现「`task_delivered=true` 却没有检查字段」或
  「`task_delivered=false` 但 UIA 转写证明送达」这类矛盾时；
- 想复核「不确定结果绝不重放」「第二判定源不放宽主判」这两条硬规则；
- 新增一个真机案例，要把它冻结成未来可重跑的验收证据。

**不适用于**：真正发送消息、唤醒/插话、修复窗口、注册 OS 任务、
替 commander 改判定、连接 LobeHub 云端或任何需要账号的功能。

## 前置条件

- 有 Python 与仓库 `src/` 可导入；**不需要**桌面窗口、管理员权限、模型、数据库、
  网络、`coordination/` 运行时目录或任何 API Key。
- 四份夹具数据内嵌在模块里（从真机回执提炼的合成数据），CI 上也能独立跑。

## 步骤

1. **只读复算**（默认，零写入）：

   ```text
   python -m agent_evolution_protocol.coordination.delivery_acceptance_fixture
   ```

   加 `--json` 看完整六段契约报告；`--list` 看夹具名、来源与 sha256。

2. **抄回执**：`cases` / `passed` / `held` / `failed` / `uncertain`，
   以及每个 `results[].result.outcome` 与 `matched`。
   正常基线是 **3 passed + 1 held + 0 failed**：
   `boolean_false_positive` 因为 `SEND_REPORT_CHECKS` 四项全缺且没有 UIA 转写，
   必须被**持住**为 `uncertain`，不是通过、也不是失败。

3. **按回执判定表判断**，不要把 `uncertain` 读成「测试挂了」。

4. **改动判定器后重跑**；若 `failed>0` 或 `held` 变成 0，说明契约被放宽或证据被伪造，
   先查 `MISSING_REQUIRED_EVIDENCE` / `ORACLE_MISMATCH` 原因，不要改 oracle 去迁就实现。

## 回执判定表

| 字段 | 结论 |
|---|---|
| `result.outcome=pass` + `matched=true` | 复算结果与事先声明的 oracle 一致；只证明**判定器**正确，不证明那次真投递成功 |
| `result.outcome=uncertain` + `MISSING_REQUIRED_EVIDENCE:...` | 声明的 `requiredEvidence` 缺失 ⇒ **持住交付**。回执文字、`task_delivered=true`、`status=message-visible` 都不算证据 |
| `result.outcome=fail` + `ORACLE_MISMATCH:...` | 复算与 oracle 不符，判定语义漂移；查实现，别改 oracle |
| `receipt.postflight.verified` | 主判，由四项 `SEND_REPORT_CHECKS` 决定；**任何第二源都不能改写它** |
| `receipt.postflight.transcript_verdict.verified` | 并列的 UIA 转写判定；`null` 表示边界未知（如 `NO_COMPOSER_BOUNDARY`），不是失败 |
| `receipt.postflight.sources_in_conflict=true` | 两源不一致，只报事实（`OCR_BAND_FALSE_NEGATIVE_SUSPECTED` 等），不裁决「其实送达了」 |
| `receipt.fixture_sha256` | 从夹具内容稳定重算；内容被改就会变，用于防漂移 |
| `DEPOSITED / MESSAGE_VISIBLE / AGENT_ACK` | 都不是关单证据；**只有 `VERIFIED` 能关单** |

## 失败处置

- **禁止**执行 `coord-uia deliver` / `compose` / `--urgent-interject` / `coord-commander tick`，
  本卡不碰窗口。
- **禁止**把 `held=1` 改成 `passed`、删掉 `required_evidence` 或放宽
  `SEND_REPORT_CHECKS` 来让汇总好看——这等于放宽别人的验收契约让自己通过。
- **禁止**让 `composer_cleared=true` / `submitted-unconfirmed` 这类不确定结果触发重放；
  夹具只复算，不重试。
- **禁止**引入任何需要登录的云端能力：`lh login` / `lh notify` / Marketplace CLI /
  agent-gateway / 自托管 Postgres+Redis+S3 运行时一律不在范围。
- 方法论借鉴自上游验收实践（`requiredEvidence → uncertain`、六段契约），
  **独立实现、不复制也不再分发任何上游源码**；上游 `LICENSE` 是
  LobeHub Community License（衍生分发需商业授权），不得越界。
- 失败只允许三选一：**停止 / 延期 / 升级给人类**。

## 验收门

```text
python -m pytest -q tests/test_delivery_acceptance_fixture.py tests/test_delivery_preflight.py tests/test_capability_cards.py
```

全量回归本机须带 `CODEBUDDY_SAFE_DELETE_ENABLED=0`。四份真机案例的来源、注入故障与
六段契约定义见 `docs/DESKTOP_DELIVERY_PREFLIGHT.zh-CN.md`「离线验收夹具」节；
LobeHub 云端/本地边界见 `docs/EXTERNAL_TOOLS.zh-CN.md` 与
`docs/THIRD_PARTY_EXECUTION_ECOSYSTEM.zh-CN.md`。
