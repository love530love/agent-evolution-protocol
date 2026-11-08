# 架构审视报告 — v0.6.29

> 日期：2026-11-08 | 撰写人：架构师视角自动审视 | 目的：完整性 / 可升级性 / 漏洞 / 健壮性

---

## 1. 当前架构总览

```
agent-evolution-protocol/
├── src/agent_evolution_protocol/
│   ├── __init__.py                          # 版本入口
│   ├── cli.py                               # 命令面统一入口
│   ├── coordination/                        # 协调层（核心）
│   │   ├── foundation/                      # ★ 基础层（新增 v0.6.29）
│   │   │   ├── __init__.py                  # 统一导出
│   │   │   ├── errors.py                    # 异常层次（ProtocolError / RelayError）
│   │   │   ├── ids.py                       # 时间 / UUID / SHA256
│   │   │   └── io.py                        # 原子 IO（crash-safe）
│   │   ├── commander.py                     # ★ 指挥核心（v0.6.29 拆分前）
│   │   ├── commander_base.py                # ★ 指挥基础（v0.6.29 新增）
│   │   ├── commander_uia.py                 # ★ 指挥 UIA 面（v0.6.29 新增）
│   │   ├── ui_fallback.py                   # 桌面人机回退中继
│   │   ├── uia3.py                          # UIA3 驱动
│   │   ├── uia_argv.py                      # UIA 参数构建
│   │   ├── transport_cdp.py                 # CDP 传输
│   │   ├── input_routing.py                 # 输入路由
│   │   ├── vision_grounding.py              # 视觉定位
│   │   ├── session_lease.py                 # 会话租约
│   │   ├── dispatch_contract.py             # 分发包契约
│   │   ├── dispatch_gate.py                 # 发送门控
│   │   ├── ack_protocol.py                  # ACK 协议
│   │   ├── ack_tracker.py                   # ACK 追踪
│   │   ├── client_registry.py               # 客户端注册/发现
│   │   ├── session_fingerprint.py           # 会话指纹
│   │   ├── transcript_bus.py                # 转写总线
│   │   ├── overlay_audit.py                 # 覆盖审计
│   │   ├── overlay_detector.py              # 覆盖检测
│   │   ├── provider_readback.py             # 供应商回读
│   │   ├── a11y.py                          # 无障碍/提权检测
│   │   ├── presence.py                      # 在线状态
│   │   ├── coordinator_tick.py              # 协调器 tick
│   │   ├── campaign_cycle.py                # 战役周期
│   │   ├── campaign_tick.py                 # 战役 tick
│   │   ├── relay_drill.py                   # 中继演练
│   │   ├── loop_verdict.py                  # 回环判定
│   │   ├── mission_graph.py                 # 任务图
│   │   ├── mission_intake.py                # 任务摄入
│   │   ├── mission_planner.py               # 任务规划
│   │   ├── mission_pulse.py                 # 任务心跳
│   │   ├── mission_runtime.py               # 任务运行时
│   │   ├── mission_supervisor.py            # 任务监督
│   │   ├── improvement*.py                  # 改进流水线（7 个文件）
│   │   ├── federation/                      # 联邦层
│   │   │   ├── __init__.py
│   │   │   ├── a2a.py                       # A2A 通信
│   │   │   ├── adapter.py                   # 适配器
│   │   │   ├── engine.py                    # 引擎
│   │   │   ├── locking.py                   # 分布式锁
│   │   │   └── supervisor.py                # 联邦监督
│   │   └── ...（其余模块）
│   ├── federation/
│   │   └── （同上）
│   └── isotime.py                           # 时间解析工具
├── tests/                                   # 守线测试
├── docs/                                    # 文档
└── tools/                                   # 工具
```

### 1.1 分层原则

| 层 | 职责 | 示例模块 |
|---|---|---|
| **Foundation** | crash-safe IO、确定性 ID、统一异常 | `foundation/*` |
| **Transport** | 单一传输协议实现 | `transport_cdp`, `uia3`, `uia_argv` |
| **Coordination** | 协调逻辑（投递、ACK、会话管理） | `commander*`, `dispatch_*`, `ack_*`, `session_*` |
| **Federation** | 跨 Agent 联邦 | `federation/*` |
| **Mission** | 任务生命周期 | `mission_*`, `improvement_*` |
| **CLI** | 命令面 | `cli.py` |

### 1.2 依赖方向（有向无环）

```
foundation → commander_base → commander → coordinator_tick → campaign_cycle
     → federation → federation/supervisor
     → ui_fallback → uia3 → uia_argv
     → (all) → cli
```

**关键保证**：`commander_uia` 不导入 `commander.dispatch`，保持无环。

---

## 2. 模块边界与职责

### 2.1 Foundation 包（v0.6.29 新增）

**设计意图**：消除全项目的 IO 重复。此前 `_atomic` 在 `commander_base.py`、`ui_fallback.py` 各有一份实现，`_utc`/`iso_now` 也分散。

| 文件 | 职责 | 外部依赖 |
|---|---|---|
| `errors.py` | `ProtocolError`（协议级）、`RelayError`（中继级） | 无 |
| `ids.py` | `iso_now()`、`uuid4`、`sha256` | 无 |
| `io.py` | `write_json_atomic()`（tmp+rename + fsync）、`read_json()`（encoding-protected） | 无 |
| `__init__.py` | 统一导出 | 无 |

**验收**：19 条测试覆盖 crash safety、编码保护、 corrupted JSON、并发唯一性。

### 2.2 Commander 三文件拆分（v0.6.29）

| 文件 | 行数 | 职责 | 不依赖 |
|---|---|---|---|
| `commander_base.py` | ~405 | 常量、订单队列、持久化、re-export | 不依赖桌面传输面 |
| `commander_uia.py` | ~348 | 投递/观察/感知叶子函数 | 不导入 `commander.dispatch` |
| `commander.py` | ~785 | 递归核心 `_dispatch`、`tick`、`dispatch` | 导入 base + uia |

**验证要点**：
- `commander` 通过 `from .commander_base import (...)` 和 `from .commander_uia import (...)` 重导出所有旧符号
- 所有旧 `from ...commander import <name>` 调用点零修改
- `commander_uia` 中的 `_uia_deliver`、`_uia_observe` 等函数体 verbatim 保留

### 2.3 ui_fallback IO 收敛（v0.6.29）

`ui_fallback.py` 的 `_atomic` 和 `_utc` 已替换为 foundation 等效函数：
```python
from agent_evolution_protocol.coordination.foundation import (
    iso_now,
    write_json_atomic,
)
_atomic = write_json_atomic
_utc = iso_now
```

**好处**：
- 单一 truth source：`foundation/io.py` 是唯一原子 IO 实现
- 修复一致性：foundation 的 `write_json_atomic` 已处理 `encoding="utf-8"` 和 `os.fsync`
- 可测试性：foundation 层有独立测试，ui_fallback 无需重复

---

## 3. 漏洞与风险

### 3.1 已修复 / 已收敛

| # | 风险 | 状态 |
|---|---|---|
| 1 | `_atomic` 定义分散（commander_base + ui_fallback） | **已收敛** → foundation/io.py |
| 2 | `_utc`/`iso_now` 不一致 | **已收敛** → foundation/ids.py |
| 3 | commander.py 单文件 784 行，维护困难 | **已拆分** → base(405) + uia(348) |
| 4 | 导入环风险（uia → commander → uia） | **已消除** → commander_uia 不导入 commander |

### 3.2 剩余风险

| # | 风险 | 严重度 | 建议 |
|---|---|---|---|
| 1 | `uia_argv.run()` 超时参数不统一（75s / 45s / 90s） | **中** | 提取 `DEFAULT_TIMEOUT` 常量到 `uia_argv.py` |
| 2 | `PowerShellUiaDriver._run()` 的 `env` 参数未在所有调用点传入 | **低** | 检查所有 `_run` 调用是否继承 `AEP_DPI_AWARE` 等环境变量 |
| 3 | `session_lease.acquire()` 的 `legacy_roots` 参数在多个调用点硬编码 | **中** | 提取为配置项或构造函数参数 |
| 4 | `federation/locking.py` 的 `LockTimeout` 跨模块依赖 | **低** | 已正确隔离，但需确保 `federation` 层不反哺导入 `coordination` |
| 5 | `improvement_*` 模块（7 个文件）之间的循环导入风险 | **中** | 建议增加 `importlib` 检查脚本在 CI 中验证 |
| 6 | `cli.py` 是命令面入口但无显式导出列表 | **低** | 建议 `__all__` 显式声明 CLI 命令名 |

### 3.3 安全审计

| 检查项 | 状态 |
|---|---|
| 无外部网络请求（除 CDP/OCR 外部服务） | ✅ |
| 无凭据硬编码 | ✅ |
| `os.fsync` 在原子写入中 | ✅ |
| 路径注入防护（`validate_workspace_id`） | ✅ |
| 子进程参数转义（`uia_argv.run` 列表式） | ✅ |

---

## 4. 可升级性建议

### 4.1 短期（v0.6.30~v0.6.32）

1. **UIA 传输超时标准化**
   - 目标：`uia_argv.DEFAULT_TIMEOUT = 90`（秒），所有调用点引用常量
   - 影响：`uia3.py`、`commander_uia.py`、`uia_argv.py` 自身

2. **improvement 模块循环导入检查**
   - 新增测试：`tests/test_import_cycles.py`
   - 方法：`importlib.import_module` 逐个导入，验证无 `ImportError`

3. **CLI `__all__` 声明**
   - 文件：`cli.py`
   - 内容：`__all__ = ["main", "coord_uia", "coord_tick", ...]`

### 4.2 中期（v0.6.33~v0.6.35）

4. **foundation 包扩展**
   - `foundation/config.py`：统一配置加载（TOML/YAML）
   - `foundation/logging.py`：结构化日志（JSON）
   - 目标：消除各模块各自的 `print`/`logging` 调用

5. **transport 层统一接口**
   - 当前 `transport_cdp.py`、`uia3.py`、`uia_argv.py` 接口不一致
   - 建议定义 `TransportProtocol`（`Protocol` 类），统一 `deliver()`、`observe()` 签名

6. **federation 层依赖检查**
   - 确保 `federation/` 不反哺导入 `coordination/`
   - 新增 CI 检查：`python -c "import ast; ..."` 验证依赖方向

### 4.3 长期（v0.7.x）

7. **配置驱动而非硬编码**
   - `CEASE_AFTER_FAILURES`、`AUTO_RESUME_LIMIT` 等常量应从配置文件读取
   - 运行时可覆盖，测试用 `monkeypatch`

8. **插件化传输层**
   - 当前 CDP/UIA/OCR 是硬编码分支
   - 建议 `transport_catalog.py` 注册表 + 工厂模式

---

## 5. 代码健壮性评估

### 5.1 基础层（foundation）— 高

| 检查项 | 结果 |
|---|---|
| 无外部依赖（纯 stdlib） | ✅ |
| crash-safe atomic IO | ✅ tmp+rename + fsync |
| encoding-protected | ✅ `encoding="utf-8", errors="replace"` |
| 确定性 ID | ✅ ISO 时间 + UUID4 |
| 异常层次 | ✅ `ProtocolError` / `RelayError` |
| 测试覆盖 | ✅ 19 条 |

### 5.2 Commander 层 — 高

| 检查项 | 结果 |
|---|---|
| 幂等 dispatch | ✅ 锁 + 状态机 |
| 预输入重试证明 | ✅ `_preinput_retryable` |
| ACK 验证 | ✅ `ack_protocol.verdict` |
| 会话指纹 | ✅ `session_fingerprint` |
| 投递安全 | ✅ `dispatch_gate.check` |
| 测试覆盖 | ✅ 30+ 条 |

### 5.3 ui_fallback 层 — 中高

| 检查项 | 结果 |
|---|---|
| IO 收敛到 foundation | ✅ v0.6.29 |
| 会话租约 | ✅ `session_lease` |
| 挑战/响应 | ✅ `_challenge` / `_ack_seen` |
| 输入路由 | ✅ `input_routing` |
| 视觉定位 | ✅ `vision_grounding` |
| 测试覆盖 | ✅ 28 条 |

### 5.4 Federation 层 — 中

| 检查项 | 结果 |
|---|---|
| 分布式锁 | ✅ `locking.py` |
| A2A 通信 | ✅ `a2a.py` |
| 状态快照 | ✅ `snapshot.py` |
| 测试覆盖 | 需要补充（当前较少） |

---

## 6. 测试策略

### 6.1 守线测试（当前）

| 文件 | 测试数 | 覆盖 |
|---|---|---|
| `test_foundation.py` | 19 | foundation 包 |
| `test_ui_fallback.py` | 28 | ui_fallback + 路由 |
| `test_commander_delivery_safety.py` | 18 | 投递安全 |
| `test_commander_reinforce.py` | 9 | 补位机制 |
| `test_delivery_acceptance_fixture.py` | 25 | 离线验收夹具 |
| `test_commander_watch.py` | 待补充 | watch 守护进程 |

### 6.2 建议新增

1. **import_cycle test**：验证无循环导入
2. **foundation_config test**（中期）：配置加载
3. **transport_protocol test**（中期）：传输接口一致性
4. **federation_dep test**：federation 不反哺 coordination

---

## 7. 命令面一致性

### 7.1 当前命令面

| 命令 | 模块 | 说明 |
|---|---|---|
| `coord-uia` | `commander_uia` / `cli.py` | UIA 投递/观察 |
| `coord-tick` | `coordinator_tick` / `cli.py` | 协调器 tick |
| `coord-client` | `client_registry` / `cli.py` | 客户端管理 |
| `coord-dispatch` | `commander` / `cli.py` | 投递 |
| `coord-ack` | `ack_protocol` / `cli.py` | ACK 查询 |
| `coord-watch` | `commander` / `cli.py` | 守护进程 |
| `coord-skill-manifest` | `skill_manifest` / `cli.py` | 技能清单 |
| `coord-announce` | `legion_announce` / `cli.py` | 军团通报 |
| `coord-send` | `bridge` / `cli.py` | 投递 |
| `mission-pulse` | `mission_pulse` / `cli.py` | 心跳 |
| `relay-drill` | `relay_drill` / `cli.py` | 中继演练 |
| `improvement-*` | `improvement_*` / `cli.py` | 改进流水线 |

### 7.2 命令面守线

`coordination/skill_manifest.py` 是命令面唯一真理源，`docs/coordination/*` 由 `build_parser()` 生成。
**任何 Agent 改 `cli.py` 后必须跑** `aep coord-skill-manifest write`。

---

## 8. 总结

### 8.1 v0.6.29 完成项

| 项 | 状态 |
|---|---|
| foundation 基础层包 | ✅ |
| commander 三文件拆分 | ✅ |
| ui_fallback IO 收敛到 foundation | ✅ |
| 重导出保证零调用点修改 | ✅ |
| 测试全部通过（99 passed） | ✅ |

### 8.2 架构健康度

| 维度 | 评分 | 说明 |
|---|---|---|
| 分层清晰度 | 9/10 | foundation/transport/coordination/federation 界限明确 |
| 循环导入风险 | 8/10 | commander 拆分后无环，但 improvement 模块需检查 |
| 代码重复度 | 8/10 | foundation 收敛后大幅改善，仍有超时参数分散 |
| 测试覆盖 | 7/10 | 核心路径覆盖充分，federation 层需补充 |
| 可升级性 | 7/10 | 模块化良好，但常量硬编码、传输接口不一致 |
| 健壮性 | 9/10 | crash-safe IO、幂等 dispatch、ACK 验证齐全 |

**综合评分：8/10** — 架构整体健康，foundation 拆分是重要一步，后续需补充 federation 层测试和传输接口标准化。
