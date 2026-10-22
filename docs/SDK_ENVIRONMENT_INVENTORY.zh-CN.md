# SDK 实物对账：锁、安装元数据、记录文件三者分开

锁写对了，不表示机器装对了；机器装对版本，也不表示代码没有被修改。这是“红皇后—鲶鱼—创造性破坏”中的实物核验环节：先发现差异，再做证据驱动的修复候选，不直接覆盖环境或放宽验收。

## 使用

```powershell
# 默认预览，不读目标文件、不写报告、不启动目标解释器
./aep.ps1 coord-dependency-inventory --lock runtime/desktop-win-cp313.lock.json --environment .tools/desktop-sdk --output .tools/inventory/review-001
# 以便携核心检查另一个 SDK；只读文件，不导入其包或运行 .pth
./aep.ps1 coord-dependency-inventory --lock runtime/desktop-win-cp313.lock.json --environment .tools/desktop-sdk --output .tools/inventory/review-001 --apply --verify-records
```

请使用可信检查器，例如便携核心，不要用待检 SDK 自己启动检查命令后再宣称“没有执行它的启动代码”。检查器内部不创建子进程、不联网，不修改/修复待检环境；报告输出目录必须在待检 SDK 外，避免对账本身改变目标。

当前限定 Windows venv 布局（pyvenv.cfg、Scripts/python.exe、Lib/site-packages），其他布局明确拒绝。存在 python.exe 不证明其版本、架构、启动能力或来源正确；此工具不执行它。

## 状态与核验范围

|状态|范围|CLI|
|---|---|---|
|PLAN_ONLY|仅计划|0|
|LOCK_VERSIONS_MATCH|锁声明包的元数据版本匹配；未验文件|0|
|LOCK_VERSIONS_AND_RECORDS_MATCH|上述匹配，且锁内包 RECORD 覆盖的哈希文件一致|0|
|ENVIRONMENT_MISMATCH|缺包、版本不同、重复/损坏元数据或未知额外包|2|
|RECORD_CHECK_FAILED|缺/空记录、文件丢失、大小/哈希不符、越界路径或不可核验项|2|

- pip、setuptools、wheel 作为引导包单独列出，**不是静默忽略**。它们不在当前 MCP 锁内，不参与锁内 RECORD 验证，仍需单独审计。其他额外 distribution 报差异。
- RECORD 检查只允许 SHA256/384/512；未哈希的 RECORD 本身和可选 pyc 不强求摘要，但其他未哈希文件不能视作已验证。记录必须包含自己和带哈希的 METADATA。
- 相对路径可指向环境内 Scripts，解析后逃出环境立即拒绝，不读取越界文件。本配置为保守 Windows wheel 安装方式：拒绝绝对路径、反斜杠、盘符等，并非对全部合法 PyPA 安装形式的通用支持。
- 单独列出 .pth/sitecustomize/usercustomize 的哈希及 import 行标志，但不执行、不认可其安全性，不读取 .pth 引用目录中的代码。仓库源码链接不等于源码完整性已认证。
- 输出 inventory.json 与 checksums.json，绑定锁哈希、时间、逐包差异及已查文件数量；拒绝覆盖已有报告。输出是本地快照，安装期间不应并发修改；不是跨文件事务或防恶意篡改机制。

## 不能据此宣称的能力

RECORD 是本地安装器声明，攻击者可以连同文件一起改它；本命令没有将已安装文件逐一与已验收 wheel 的原始字节对比。独立新增的 [coord-wheel-compare](WHEEL_PAYLOAD_COMPARISON.zh-CN.md) 可做有范围的原始载荷对照，不改写本报告的结论。两者均没有扫描所有未登记文件、启动路径覆盖、系统 DLL、源码、解释器或内核。本报告始终 artifact_integrity_verified_against_wheels=false、untracked_files_checked=false、security_verified=false。

因此匹配状态不是部署授权或安全认证，更不能替代已有版本锁、OSV 快照、业务验收、独立评审。将锁、安装报告、漏洞报告一起作为 coord-improvement evidence；差异先调查，不能自动重装、删包或替换生产配置。

## 本轮实际验收

2026-10-22 用便携核心只读检查本机 SDK：30 个锁内包版本匹配，1580 个带哈希的记录文件一致；另列 pip 25.2 为锁外引导包。不启动被检解释器，不执行其启动文件。失败注入覆盖改文件、缺包、错版、重复身份、缺/空 RECORD、越界路径、未哈希代码及 CLI 非零门；保留未知范围，不拿快照证明“永远安全”。

后续目标：与已验收 wheel 的字节对照、锁外引导包漏洞审查、未登记文件扫描、解释器来源/架构核验及快照过期策略。

参考：[PyPA installed-projects/RECORD 规范](https://packaging.python.org/en/latest/specifications/recording-installed-packages/)。
