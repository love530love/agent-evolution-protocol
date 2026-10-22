# 依赖证据一致性门

已有漏洞查询、安装对账和原始 wheel 对照，但三份“通过”如果来自不同锁、不同 SDK 或不同日期，不能拼成一次有效审查。因此增加只读汇总门，贯彻“红皇后—鲶鱼—创造性破坏”：挑战证据间的隐含假设，核对范围与时效，不用报告数量代替真实性。

## 使用

```powershell
# 默认只预览，不读证据、不写报告、不联网
./aep.ps1 coord-dependency-gate --lock runtime/desktop-win-cp313.lock.json --audit-dir .tools/audits/osv-001 --inventory-dir .tools/inventory/sdk-001 --wheel-dir .tools/comparisons/sdk-001 --output .tools/gates/review-001
# 明确执行；默认全部报告最长 24 小时，策略可显式设为 1..168 小时
./aep.ps1 coord-dependency-gate --lock runtime/desktop-win-cp313.lock.json --audit-dir .tools/audits/osv-001 --inventory-dir .tools/inventory/sdk-001 --wheel-dir .tools/comparisons/sdk-001 --output .tools/gates/review-001 --max-age-hours 24 --apply
```

不自动重查漏洞、不重新扫描环境、不安装/修复、不启动目标解释器、不刷新旧报告时间。报告超时应重新执行相应检查，不应为“过门”偷偷改时间或放宽策略。需要使用可信检查器；输出必须位于源证据目录外，已有输出不覆盖。

## 四份证据的关联

1. OSV audit.json 及 query/response：要求校验清单完整，查询恰好覆盖锁的包版本；重新解析原始批次，不能仅信任“无风险”摘要。失败、分页和已知通报均阻塞。
2. SDK inventory.json：要求锁内版本及 RECORD 检查通过，有实际文件覆盖，没有缺包、错版、重复或额外未知包等未解决差异。
3. wheel-comparison.json：要求原 wheel 摘要及直接映射载荷通过，不允许未知映射被绿色状态掩盖。
4. wheel 对照内嵌的 inventory 预检：包版本、SDK 路径与独立安装清单一致；wheel 主报告必须绑定此预检的字节 SHA256 和环境路径，防止误拼接快照。

全部报告必须有相同的锁 SHA256、可解析的带时区时间、有效文件校验值，并在时效内。允许最多 5 分钟未来时钟偏差，超过则阻塞；门限按执行机 UTC 时钟计算，不替代可信时间服务。

旧 wheel 报告若没有环境/预检绑定，不能用于新门。应以原始命令新建报告，不改旧证据。此增量不改变原 wheel 对照命令的检查范围。

## 结果与边界

|状态|CLI|意义|
|---|---|---|
|PLAN_ONLY|0|无执行预览|
|SCOPED_EVIDENCE_CONSISTENT|0|所提交快照在限定检查范围内一致且未过期|
|EVIDENCE_BLOCKED|2|缺失、损坏、过期、时间异常、范围不一致或源报告未通过|

gate.json 保存来源校验清单摘要、各文件摘要、锁哈希、时效策略和阻塞原因，另写 checksums.json。校验值证明本地快照自洽，不是发布者签名或 Agent 身份认证。拥有本地写权限者仍可重写整套证据；不能宣称防恶意伪造。多文件读取/写入也不是事务，应停止并发修改。

始终 deployment_authorized=false、security_verified=false、identity_authenticated=false。锁外引导包、生成包装器、未登记文件、源码/解释器来源、许可证法律审查、真实部署与 Agent ACK 都在排除范围。安装包可在报告后改变，门不重新检查当前磁盘。

本门是可提交给 coord-improvement 的审查证据，并未成为所有部署/上传流程的全局强制前置。独立复核、token、试运行、人工暂停及现有资源锁保持原状；CLI 0 不自动晋级或部署。

## 2026-10-23 验收与后续

本机将真实 30 包 OSV 快照、1580 文件 RECORD 清单和 1511 文件 wheel 对照联查，得到限定范围一致；未联网刷新漏洞快照，未修改生产锁或 SDK。测试覆盖文件损坏、校验路径越界、过期/未来/无时区时间、换锁/换环境、替换预检、零覆盖、未处理映射，以及“摘要通过但原始 OSV 响应含风险”。

后续：安装后的变化探测、可信身份/签名、锁外包审查、生成内容与未登记文件检查，以及将此门作为明确声明的发布策略接入。不得把这些待开发项写成已完成。
