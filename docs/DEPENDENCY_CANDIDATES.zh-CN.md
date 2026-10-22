# 依赖候选锁：只读审查、隔离验证、显式采纳

依赖更新也是“红皇后—鲶鱼—创造性破坏”的实验对象：发现新版本不等于必须升级；先看变更、最低成本证伪，再经独立评审决定替换。不为追求“最新”自动改已验收锁。

## 使用

```powershell
# 默认仅计划，不读 wheel、不联网、不写候选
./aep.ps1 coord-dependencies --baseline runtime/desktop-win-cp313.lock.json --wheelhouse .tools/desktop-wheelhouse --output .tools/candidates/experiment-001
# 同一已验收锁的离线再现：只能复用文件名、版本、哈希完全一致的文件
./aep.ps1 coord-dependencies --baseline runtime/desktop-win-cp313.lock.json --wheelhouse .tools/desktop-wheelhouse --output .tools/candidates/experiment-001 --apply
# 新目录中的更新 wheel 必须明确允许 PyPI 文件摘要核验；只读请求，无下载/安装
./aep.ps1 coord-dependencies --baseline runtime/desktop-win-cp313.lock.json --wheelhouse .tools/new-wheels --output .tools/candidates/experiment-002 --apply --verify-pypi
# 指定候选锁，先校验，再在新环境验证；不覆盖生产清单或已有环境
./scripts/prepare-desktop-wheelhouse.ps1 -Action Verify -Directory .tools/new-wheels -LockFile .tools/candidates/experiment-002/candidate.lock.json
./scripts/bootstrap-desktop.ps1 -Python 'D:/Python313/python.exe' -EnvironmentPath .tools/sdk-experiment-002 -Wheelhouse .tools/new-wheels -LockFile .tools/candidates/experiment-002/candidate.lock.json -Install
```

生成器不运行 pip 解析，不下载 wheel，不执行包，不自动升级锁。待分析 wheel 由维护者在独立目录明确准备；当前只支持现有 Windows CPython 3.13、MCP 1.30.0 根依赖，尚非通用依赖解析器。

## 证据与边界

- 每个 wheel 作为 ZIP 数据检查，不解压或导入代码。核对文件名、METADATA 身份、目标平台及保守 ABI 标签，限制文件/元数据大小、重复包和清单数量。
- 离线复用严格匹配基线哈希，不代表重新查询了当前撤回或漏洞状态。显式 --verify-pypi 查询固定版本 API，要求文件唯一、SHA256 一致、未 yanked、下载 URL 为预期 PyPI wheel。单次网络 I/O 超时最多 15 秒，网络请求开始预算 90 秒；这不是整个响应的严格墙钟期限，最后在途请求可能延长。响应大小有上限，不自动重试。不是签名或发布者身份认证。
- 输出 candidate.lock.json 与 review.json：包含基线/候选哈希、新增/移除/变更、Requires-Dist 和许可证声明。声明都是不可信数据，不是授权、许可证法律结论或指令。CLI 默认只输出短摘要，详细信息留本地文件。
- 依赖完整性、漏洞扫描、许可证审查、实际安装和采纳字段不会因生成成功变为 true。移除依赖必须重点评估，不能凭“生成成功”认定可用。
- 输出目录拒绝覆盖。两个文件不是跨文件原子事务；写盘失败可能留下部分目录，应保留诊断，不能将部分输出当采纳凭证。轮子文件或基线在检查期间被修改不构成原子快照；安装仍再次哈希验证。

## 采纳门

候选锁 → 新环境无索引安装 → pip check → 原生模块导入 → 工作流验收 → 独立评审 → 显式 Git 更新生产锁。可将候选锁、review.json 和后续测试报告作为现有 coord-improvement 的 evidence，走验证/评审/试运行/采纳流程；生成器不自动执行这些阶段，不绕过 token、授权或桌面租约。

本轮已用真实 30 个 wheel 再现基线并执行逐文件 PyPI 摘要/未撤回核验，变更集为空；候选锁已在新目录完成无索引 SDK 安装与 pip check。这只检验工具链，不是升级依赖或证明新版本优于旧版。已补 [OSV 已知漏洞查询](DEPENDENCY_VULNERABILITY_AUDIT.zh-CN.md)，独立保存报告，不改写候选状态。后续目标：真实有变化候选的完整实验、通报处置、许可证材料汇总、自动解析器适配及受控采纳工具。

参考：[PyPI JSON API](https://docs.pypi.org/api/json/)。
