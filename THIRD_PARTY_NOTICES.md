# Third-party acknowledgements and notices

感谢以下开源项目及其维护者为 Agent 执行、浏览器自动化、桌面操控、终端工程、移动测试和逆向分析生态做出的贡献。AEP 当前只登记其公开仓库和能力边界，不默认复制、重新打包或再分发这些项目的代码。

## Referenced projects

| Project | Upstream | AEP 使用方式 | 当前许可证状态 |
|---|---|---|---|
| Browser Use | <https://github.com/browser-use/browser-use> | 浏览器 Provider 候选 | 未独立核验；安装前读取上游 LICENSE |
| Skyvern | <https://github.com/Skyvern-AI/skyvern> | 长流程浏览器 Provider 候选 | 未独立核验；安装前读取上游 LICENSE |
| LaVague | <https://github.com/lavague-ai/LaVague> | 规格/动作引擎候选 | 未独立核验；安装前读取上游 LICENSE |
| UI-TARS-desktop / Agent TARS | <https://github.com/bytedance/UI-TARS-desktop> | 视觉桌面 Provider 候选 | 未独立核验；安装前读取上游 LICENSE |
| UFO / UFO2 | <https://github.com/microsoft/UFO> | Windows 桌面 Provider 候选 | 未独立核验；安装前读取上游 LICENSE |
| Agent S | <https://github.com/simular-ai/Agent-S> | 桌面规划 Provider 候选 | 未独立核验；安装前读取上游 LICENSE |
| Self-Operating Computer | <https://github.com/OthersideAI/self-operating-computer> | 截图到动作实验 Provider 候选 | 未独立核验；安装前读取上游 LICENSE |
| Open Interpreter | <https://github.com/OpenInterpreter/open-interpreter> | 终端/文件/代码执行 Provider 候选 | 未独立核验；安装前读取上游 LICENSE |
| OpenHands | <https://github.com/All-Hands-AI/OpenHands> | 长程工程 Agent 候选 | 未独立核验；安装前读取上游 LICENSE |
| OpenAdapt | <https://github.com/OpenAdaptAI/OpenAdapt> | 演示回放 Provider 候选 | 未独立核验；安装前读取上游 LICENSE |
| Appium | <https://github.com/appium/appium> | 移动设备执行底座候选 | MCP 适配器需另行核验；不把 Appium 本身称为 MCP Server |
| REA | <https://github.com/morluto/rea> | 逆向取证 Provider 候选 | 未独立核验；安装前读取上游 LICENSE |

## Attribution and redistribution policy

1. 仅引用仓库、协议或公开能力时，保留项目名称、作者/组织和上游链接；
2. 安装作为运行时依赖时，保留包管理器元数据和上游许可证；
3. 复制源码、补丁、模型、图片、字体或二进制时，必须逐项核验 LICENSE、NOTICE、版权声明和再分发条件；
4. 许可证未核验时，AEP 只允许 `reference-only` 或隔离实验，不允许纳入发布制品；
5. 不把第三方项目的商标、模型权重、云端账号、API Token 或登录态打包进 AEP；
6. 如果上游要求 NOTICE、署名或相同许可证，发布包必须附带对应文本，并在版本发布说明中列出来源和版本/提交号；
7. AEP 的 Provider 适配器是独立代码，不能暗示上游项目背书、联合发布或兼容承诺。

详细候选、运行时和验收门见 [第三方执行生态接入策略](docs/THIRD_PARTY_EXECUTION_ECOSYSTEM.zh-CN.md)。
