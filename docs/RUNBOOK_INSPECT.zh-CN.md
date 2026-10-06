# `aep inspect` 作战简报

`aep inspect` 会把协作平面和恢复内核合并成一份当前态报告，适合给人类或新 agent 作为第一份状态简报。

## 命令

```powershell
aep --coord-root coordination --kernel-root .aep-kernel inspect
aep --coord-root coordination --kernel-root .aep-kernel inspect --markdown
```

## 报告包含

- unread digest 总量；
- wake queue 数量与过期 wake；
- active claims；
- task states 与 stale task；
- kernel hash chain 是否有效；
- active leases；
- UNKNOWN / inflight operations；
- sticky session 缺失的 agent；
- latest checkpoints；
- 自动生成的 next commands。

## 推荐用法

新 agent 第一回合：

```powershell
aep --coord-root coordination --kernel-root .aep-kernel inspect --markdown
aep --coord-root coordination coord-onboarding --agent <agent>
aep --coord-root coordination coord-digest --agent <agent>
```

执行写操作前：

```powershell
aep --coord-root coordination --kernel-root .aep-kernel inspect
aep --coord-root coordination coord-claim --agent <agent> --task <task>
aep --kernel-root .aep-kernel kernel-lease-acquire --lease-type TASK_WRITE_LEASE --resource <resource> --executor <agent>
```

失败后：

```powershell
aep --coord-root coordination coord-guided-retry --attempted-action "<action>" --error "<error>" --risk-level medium
aep --coord-root coordination --kernel-root .aep-kernel inspect
```

## 判断原则

- 有 `UNKNOWN` operation 时，先查清结果，不要重放相关外部写动作。
- 有 active lease 时，尊重 owner，不绕过。
- 有 stale wake 时，先归档或压缩成 digest，不直接执行。
- sticky session 缺 ID 时，先绑定或 digest-only，不开新会话。
