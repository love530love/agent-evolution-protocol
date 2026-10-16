# 桌面 MCP 本机接入验收（2026-10-16）

用户指定的五项目目录已核对。所有探测均通过 AEP 的 `coord-desktop` 能力层，执行 MCP initialize、tools/list 并关闭服务，没有调用键鼠、剪贴板或消息发送工具。下列计数仅代表当前源码/产物版本的工具定义数量。

| 本地注册 ID | 启动方式 | 工具数 | 结果 |
| --- | --- | --- | --- |
| sbroenne | AEP .tools 下 v1.3.28 自包含 exe | 18 | TOOLS_DISCOVERED |
| sbroenne_build | 用户源码目录 Debug 构建的 exe | 19 | TOOLS_DISCOVERED |
| shuyu | dotnet.exe + Windows-MCP.Net.dll | 48 | TOOLS_DISCOVERED |
| mcpcontrol | node.exe + build/index.js | 23 | TOOLS_DISCOVERED（修复后） |
| mario | node.exe + dist/index.js --transport=stdio | 50 | TOOLS_DISCOVERED |
| cursortouch | .venv/Scripts/python.exe -m windows_mcp serve --transport stdio | 20 | TOOLS_DISCOVERED |

本机配置与完整 schema 在 `coordination/desktop_providers/`；本机已按精确工具名绑定窗口、定位、读取、输入等各自实际提供的能力。`plan --operation type --previous-state PROVEN_NOT_SENT` 返回六个通道的候选，`execution_started=false`。MCPControl 无专用控件查找工具，因此没有强行绑定 `locate`。

## 两条状态纠正

Mario 的 `node-autoit-koffi` 依赖已含 `dlls/AutoItX3_x64.dll`，没有安装完整 AutoIt3 仍可完成本次工具发现。未来具体操作若需要其他依赖，按操作实测登记，不从工具发现推断全部可用。

CursorTouch 的 venv 解释器为 Python 3.14.0，fastmcp/windows_mcp/comtypes/dxcam 均可发现；无需重新 `uv sync` 或拉取另一套 Python。该配置直接调用已有 venv，避免无人值守时重复下载。遥测通过其已有环境变量关闭。

## MCPControl / keysender 修复复盘

`build/index.js` 存在，但首次启动报 `Cannot find module '../../build/Release/key_sender.node'`，说明 TypeScript 构建没有完成原生依赖安装。

`npm rebuild keysender` 返回 0，同时提示安装脚本被 allowScripts 阻止；产物文件仍不存在。因此必须核对实际文件与运行结果，不能只看 npm 退出码。核对 keysender 的安装脚本后，本机直接执行该版本的 node-gyp rebuild，未修改全局 allowScripts 设置。

Node 24.18.0 / node-gyp 12.3.0 找到了 VS2022，但 MSVC 报 C4596。keysender 2.3.0 的两处类内声明带冗余限定名：`Virtual::getLastCoords` 和 `Helper::mouseEvents`。本机删除这两个类内限定名后成功生成 `build/Release/key_sender.node`，随后 MCPControl 实际初始化和 23 工具发现通过。

修复补丁：[keysender-2.3.0-msvc.patch](../../patches/keysender-2.3.0-msvc.patch)。这是本机 node_modules 的依赖补丁，重装可能丢失；先核对版本和补丁状态，再恢复，不能重复盲改。补丁不改变输入行为。构建过程还提示旧 vcpkg 路径不可用，但原生插件最终编译、加载和 MCP 初始化成功；未将该非致命告警当成运行失败。

## 交付与未完成验收

AEP 增加了 provider 环境变量支持，并在失败回执中保留根因 stderr。正常发现后只配置能力计划，不自动执行任意第三方工具。共享桌面输入锁、唯一窗口与原会话定位、发送后消息列表读回、目标 Agent ACK，仍是下一阶段逐客户端验收的必要证据。

本次不能据此宣布“六个通道都已稳定完成 Agent 唤醒和双向指挥”。可核对的结果是：五个项目及一个发布版均已完成本地注册、实际协议握手、工具定义发现与计划候选接入。
