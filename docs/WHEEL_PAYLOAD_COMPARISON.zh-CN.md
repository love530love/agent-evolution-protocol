# 原始 wheel 与安装字节对照

原来的 RECORD 检查可以发现文件损坏，但修改者也能重写本地 RECORD。因此增加独立参照：先以已审查锁中的 SHA256 核对 wheel，再读取其 ZIP 文件载荷，逐个与安装文件计算的摘要比较。不使用安装后 RECORD 的哈希作为参照，不解包、不导入代码、不启动待检解释器。

这延续“红皇后—鲶鱼—创造性破坏”：主动挑战“本地记录一致所以可信”的假设，新增可证伪实验，而不削弱旧验收或自动修复环境。

## 使用

```powershell
# 默认仅计划，不读目标、不创建报告
./aep.ps1 coord-wheel-compare --lock runtime/desktop-win-cp313.lock.json --wheelhouse .tools/desktop-wheelhouse --environment .tools/desktop-sdk --output .tools/comparisons/review-001
# 明确执行，只读对照，输出必须在待检 SDK 外，已有输出不覆盖
./aep.ps1 coord-wheel-compare --lock runtime/desktop-win-cp313.lock.json --wheelhouse .tools/desktop-wheelhouse --environment .tools/desktop-sdk --output .tools/comparisons/review-001 --apply
```

使用可信便携核心或独立检查器。不要先启动待检 SDK，再把“检查器没有自行启动子进程”描述成启动代码从未运行。环境限定为现有 Windows CPython 3.13 wheel 安装配置，不是通用安装布局识别器。

## 校验及失败门

1. 独立保存版本/额外包/启动文件清单，锁变化会拒绝继续；该快照不执行 .pth。
2. 每个锁定 wheel 校验 SHA256 后才打开 ZIP；同一文件句柄用于该哈希与后续 ZIP 读取，降低路径替换竞态。
3. 核对文件名、目标标签、METADATA 身份和必需元数据；限制归档大小、成员数量及展开总量，拒绝链接成员、加密、危险路径和 Windows 大小写目标碰撞。
4. 普通成员映射到 site-packages；支持 .data/purelib、platlib、scripts、data 的 Windows venv 映射。headers/未知方案及需安装器改写的 #!python 脚本标记不完整，不自动猜测或忽略。
5. 对原 wheel 中映射明确的文件，直接比较实际文件大小和 SHA256；不会修复、重装、删除或覆盖目标文件。

|状态|意义|CLI|
|---|---|---|
|PLAN_ONLY|无执行预览|0|
|WHEEL_PAYLOADS_MATCH|版本预检通过，所有支持的 wheel 载荷文件匹配，无未处理映射|0|
|ENVIRONMENT_MISMATCH|安装元数据与锁不符，或有未知额外包等|2|
|WHEEL_COMPARISON_FAILED|源 wheel 哈希、文件内容、路径或结构失败|2|
|INCOMPLETE_MAPPING|有不能可靠映射或被安装器改写的成员|2|

输出 wheel-comparison.json、checksums.json 和 inventory 子目录的独立预检证据。多文件输出不是事务；写盘失败可能留下部分报告，不能当完整采纳凭证。

## 明确排除和信任边界

- 安装后的 .dist-info/RECORD 合法地被安装器重写，单独列为 installer_mutable，不做原始字节相等判断；不能利用它掩盖其他成员的差异。
- 安装器生成的 exe 包装器、pyc、INSTALLER/REQUESTED 等新文件不在原 wheel 载荷中，本轮不验证。未登记文件、源码链接、解释器、系统 DLL 和锁外引导包也不因此获认证。
- 只核对锁里指定的 wheel，不给整个 wheelhouse 或安装目录颁发安全证明。wheel_payloads_match 仅限报告的载荷范围；security_verified、generated_wrappers_verified、untracked_files_checked 仍为 false。
- 本地已审查锁及其来源是信任起点，不是发布者签名验证。拥有本机写权限者可同时改锁、wheel 或报告。检查期间应停止并发安装/修改；文件读取不是防恶意竞态的原子快照。
- 不联网；漏洞快照、许可证审查、原窗口投递/ACK 与本工具是不同验收项。CLI 0 不自动部署，也不推进 coord-improvement 阶段。

## 2026-10-23 实测

本机 30 个锁定 wheel，1511 个原始载荷文件匹配。与先前 1580 个 RECORD 带哈希文件的数量不同，因为 RECORD 还包括安装器生成内容。故障测试验证：即使本地代码和 RECORD 一起改写，对照仍失败；坏源摘要、缺文件、越界路径、Windows 大小写碰撞和未支持映射均不能静默通过。

下一步：安装器生成内容的受控重建对照、锁外引导包审计、未登记文件扫描、可信来源和签名策略。宏大目标仍需分项可复现证据，不能从载荷一致推导“整机无风险”。

参考：[PyPA wheel 安装与 .data 映射规范](https://packaging.python.org/en/latest/specifications/binary-distribution-format/)。
