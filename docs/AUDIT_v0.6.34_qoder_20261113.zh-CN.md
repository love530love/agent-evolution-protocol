# 审计与升级报告 v0.6.34（qoder，2026-11-13）

范围：`K:\PythonProjects5\agent-evolution-protocol` 工作树（v0.6.34，未提交的并发改动共存）。
方法：先读 digest/策略与既有契约，再按「协议写入路径 → 投递门 → 会话身份 → 诊断持久化」顺序做代码审计；每个候选缺陷先在临时目录复现，再修、补测试、跑相关模块与全量回归。未修改另一位 Agent 正在编辑的 README、`cli.py`、`session_attestation.py`、`ui_fallback.py`、`uia3.py` 及其文档。

## 结论摘要

- 已修复 5 项：其中 3 项会导致"协议门被绕过或投递被误拒"（P1），1 项是输入边界（路径穿越，P1），1 项是身份证据篡改与半写（P2）。
- 未修复但已定级 8 项（P2 3 项，P3 5 项），均给出证据坐标与建议，不在本轮扩大改动面。
- 验证：全量 `pytest` = 1737 passed, 2 skipped, 152.38s, exit 0；相关模块 10 个测试文件 = 92 passed；新增 `tests/test_core_hardening_qoder.py` 20 tests。
- 未做：REVIEWED（需独立复核者）、TRIAL/ADOPTED（未部署）、真实桌面投递与原会话 ACK 验证、远端 CI 结果核对。

## 已修复项

### F1（P1）wake 消费绕开逐事件锁，可重复调用 adapter

- 位置：`src/agent_evolution_protocol/coordination/wake_daemon.py:209`
- 现象：`consume()` 直接调用 `_consume_unlocked()`，没有获取 `wake_ingress.wake_lock` 的逐事件锁。两个 daemon（或 cancel 与 daemon）并发时同一事件可能被 adapter 处理两次；锁争用此前抛裸 `ValueError`，调用方无法区分"忙"与"输入非法"。
- 修复：`consume()` 先校验事件 ID，非法文件名不调用 adapter、直接隔离到 `wake_failed` 并记 `invalid-event-id`；合法事件在 `wake_lock(QUEUE.parent, event_id)` 内重新读盘后再消费；锁争用捕获 `TaskWriterBusy` 后记 `deferred-busy` 并保留事件在队列，等下一次轮询。
- 证据：`wake_daemon.py:218`、`:229`、`:235`、`:242`；`battle_orders.py:18`（`TaskWriterBusy` 定义）、`:63`、`:74`（Windows/POSIX 两个争用分支）。
- 测试：`tests/test_core_hardening_qoder.py` 中 "second wake is deferred while the lock is held"（持锁期间 consume 返回 0、adapter 未被调用、事件仍在、日志为 `deferred-busy`）与 "invalid wake filename is quarantined without invoking an adapter"。

### F2（P1）dispatch_gate 不识别 WAKE-/PULSE- 事件 ID，真实订单被系统性拒绝

- 位置：`src/agent_evolution_protocol/coordination/dispatch_gate.py:60-80`
- 现象：用临时目录复现 2026-11-13——`WAKE-<stamp>` 订单被拒为 `one_event_id: found <stamp>`（把 stamp 当成了第二个事件 ID），`PULSE-<20位十六进制>` 订单被拒为 `one_event_id: found none`。生产 inbox 中当时有 7 条 `WAKE-` 订单（共 35 条订单文件），该缝隙与 2026-11-03 修复过的 coord-send 同类：生产者与门使用不同词表。
- 修复：门新增 `WAKE_ID_PATTERN`（:78）与 `PULSE_ID_PATTERN`（:79）并并入统一 `EVENT` 正则（:80）；同时保留原有的 EVT- 与 stamp 词表。没有改生产者的 ID 契约，历史订单仍可被正确解析。
- 证据：`wake_ingress.py` 生成 `WAKE-` 前缀；`mission_pulse.py` 生成 `PULSE-`；`dispatch_gate.py:202` 的 `one_event_id` 断言。
- 测试：`tests/test_core_hardening_qoder.py` 中 WAKE_ID/PULSE_ID 订单通过 gate（verdict=send）、以及"第二个事件 ID 仍被拒"的回归用例。

### F3（P1）event_id 未校验，收据与订单文件名可被路径穿越

- 位置：`src/agent_evolution_protocol/coordination/commander_base.py:50`、`:139-140`
- 现象：`submit_order` 接受任意 `event_id` 字符串，而下游用它拼接订单文件名和 `receipts/<event_id>-<target>-cdp.json`。`..\..\pwn`、`EVT-1/../../pwn`、含换行的 ID 都能进入写入路径；ID 来自 wake 事件文件或 CLI 参数，是实实在在的输入边界。
- 修复：提交入口要求 `^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$`，拒绝路径分隔符、换行、前导 `-` 与空串。该正则刻意宽于现有 ID 词表，避免破坏 `EVT-`、stamp、`WAKE-`、`AEP-OLDER`、`e1` 等现有调用方。
- 证据：`commander_base.py:50`（`SAFE_EVENT_ID`）、`:139-140`（拒绝分支）。
- 测试：`tests/test_core_hardening_qoder.py` 覆盖 6 个恶意样本与 5 个合法样本。

### F4（P1）命令记忆把凭据原样落盘

- 位置：`src/agent_evolution_protocol/coordination/command_memory.py:73-104`、`:168`
- 现象：`record()` 将原始命令与输出尾部直接写入 `command_memory/`。bearer token、`Cookie` 会话值、`key=value` 形式的密码、`--token` 值、`sk-`/`AKIA`/`ghp_`/JWT 都会长期明文保留，且会进入按输出生成的失败签名。
- 修复：新增 `redact()`：键值类模式保留键名后替换为 `<redacted>`，纯令牌类整段替换；`record()` 的分类与签名仍基于原始文本（避免脱敏后特征漂移），落盘的命令与输出摘录改用脱敏文本。
- 证据：`command_memory.py:73`、`:90`、`:98-104`、`:168`。
- 测试：8 类凭据样本落盘后均不含原值、含 `<redacted>`，同时 `long_path` 等正常分类不被误伤。

### F5（P2）指纹记录可被手改绕过，且写入非原子

- 位置：`src/agent_evolution_protocol/coordination/session_fingerprint.py:237`、`:274`、`:281`
- 现象：`compare()` 只信任记录里的 `fingerprint` 字符串；把 `session_marker` 改成另一个会话、保留旧 digest，仍返回 `match: True`。`record()` 用普通写入，进程中断会留下半写 JSON。
- 修复：比对前从身份字段重算 digest，记录或观测任一侧与自身字段不一致即判 `False` 并给出 `inconsistent` 原因；`record()` 改用 `foundation.io.write_json_atomic`。
- 证据：`session_fingerprint.py:31`（导入）、`:237`、`:274`、`:281`；重启后 PID 变化仍匹配的既有语义保持不变。
- 测试：篡改 marker + 旧 digest → `False`；重启 PID 变化 → `True`；原子写无固定临时文件残留。

## 未修复项（按风险排序，均未在本轮改动）

### R1（P2）改进流水线默认验证超时短于本机全量测试

- 证据：`src/agent_evolution_protocol/coordination/improvement.py:163`（默认 `timeout=120`）、`:180`（上限 300）。
- 事实：本机全量 `pytest` 实测 152.38s（`docs/AUDIT_v0.6.34_qoder_20261113.pytest.log`）。默认值下走 `validate` 必然超时，慢 CI 上 300s 上限也缺少余量；而超时会被记为验证失败而不是"未完成"。
- 建议：以实测基线重设默认值与上限，或在超时回执中区分"测试超时"与"测试失败"。

### R2（P2）租约 token 明文存于 JSON

- 证据：`improvement_work.py:73-84`（租约写入 `token: uuid4().hex`，含前一代 `previous_token`）。
- 影响：同机可读文件者可伪造 claim 所有权，绕过 fencing。属本机信任边界内的令牌，无远程暴露；但"fencing token"以明文长期躺在可读文件中，与其安全目的不匹配。
- 建议：租约文件收紧权限，或只存 token 的哈希并在校验时比对哈希。

### R3（P2）realtime hub 向任意 loopback 请求回显 token

- 证据：`realtime_hub.py:312`（`/api/bootstrap` 返回 `TOKEN`）；`:376-380`（仅靠绑定 `127.0.0.1`/`localhost`/`::1`）。
- 影响：本机任意进程、以及能从浏览器发起 loopback 请求的页面（如 DNS rebinding 场景）可取得 token 并订阅/写入事件。当前没有 Origin 校验。
- 建议：校验 `Origin`/`Host`，或 bootstrap 不下发 token 而改用一次性配对码。

### R4（P3）bridge release 是 check-then-act

- 证据：`bridge.py:258-264`（读 `owner.json` 校验 owner 后 `unlink` + `rmdir`）；claim 侧用 `mkdir(exist_ok=False)` 独占（`:240-246`）。
- 影响：release 与 claim 并发时，新 claim 会先看到目录存在而返回"未认领"（假失败），或 release 在 owner 已被换写时删错记录。由于 claim 的目录创建是原子的，不会发生两份 owner 并存的数据覆盖；属健壮性问题。
- 建议：release 也走同一把 claims 锁，或在锁内完成校验与删除。

### R5（P3）非 Windows 的验证进程杀不干净

- 证据：`validation_execution.py:59-62`：POSIX 分支直接 `subprocess.run(..., timeout=...)`，超时只终止直接子进程；Windows 分支使用 Job object 管进程树。
- 影响：Ubuntu CI 上超时的验证可能留下孙进程继续占用工作区，后续运行互相污染。
- 建议：POSIX 用 `start_new_session=True` + 进程组 kill 对齐 Windows 语义。

### R6（P3）两套 ProtocolError 并存

- 证据：`cli.py:110` 定义 `ProtocolError(ValueError)`，`main()` 在 `:4023` 只捕获这一个并返回退出码 2；`foundation/errors.py:20` 另定义 `ProtocolError(AEPError)`，当前无任何模块抛出。若将来有模块抛 foundation 版本，CLI 会直接 traceback 而不是干净的 `protocol error`。
- 建议：收敛到单一层级（`foundation` 版本继承 `ValueError` 或 CLI 同时捕获 `AEPError`）。因 `cli.py` 正被并发编辑，本轮不动。

### R7（P3）文档与实现漂移

- `README.md:181` 与 `README.zh-CN.md:19`、`:66` 声称协作 CLI "不调用模型"，但 `route --propose` 会调用本机 Ollama（`cli.py:1998-2001`，默认端点 `routing_proposer.py:22` = `http://127.0.0.1:11434`）。准确表述应为"默认不调用；`--propose` 显式请求时才咨询本机模型"。
- `docs/SYSTEM_OVERVIEW.zh-CN.md:17` 仍写 "v0.3.0 能力与边界"，包版本已是 0.6.34。
- README 正被另一位 Agent 编辑，本轮只登记不修改，避免覆盖。

### R8（P3）`cli.py` 单体规模

- 4023 行、92 个子命令（`build_parser()` 实测）。是并发编辑冲突的主要来源，也是审计成本的主要来源。拆分需独立提案与行为回归，不适合夹带在本次修复里。

### 观察（非缺陷）

- 接手时基线出现过 2 个失败（`test_skill_manifest.py` 的 DIGEST_MISMATCH/SKILL_STALE），来自另一位 Agent 当时未提交的 `coord-uia calibrate` 相关改动；其并发工作完成后，现工作树全量通过。这不是本仓库的稳定缺陷，未登记为问题。

## 验证记录

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 新增回归测试 | `python -m pytest -q tests/test_core_hardening_qoder.py` | 20 passed |
| 相关模块回归 | 10 个测试文件（wake/dispatch/command_memory/fingerprint 等） | 92 passed in 2.24s |
| 全量 | `python -m pytest -q` | 1737 passed, 2 skipped in 152.38s, exit 0 |
| 契约保持 | 四类事件 ID 均过 gate；合法 event_id 仍可提交；指纹重启语义不变 | 由上述测试覆盖 |

全量日志：`docs/AUDIT_v0.6.34_qoder_20261113.pytest.log`。

## 未声明事项

- 本报告是审计与本地修复记录，不是部署、不是采纳决定，也不代表远端 CI 已通过。
- 未声称真实桌面投递、原会话 ACK 或跨机器一致性已验证；F1–F5 的证据均为隔离单测与本地全量回归。
- 未修改并发改动中的文件；R7 的两处文档漂移需要在并发编辑结束后再处理。
