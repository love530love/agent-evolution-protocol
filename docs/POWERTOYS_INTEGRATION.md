# PowerToys 桌面增强层

PowerToys 是可选的桌面稳定层，不是消息总线：

- Awake：无人值守任务期间防止系统休眠。
- Workspaces：恢复 Qoder、WorkBuddy、浏览器的固定窗口集合。
- FancyZones：为不同 Agent 固定窗口区域，减少错窗。
- Always On Top：置顶指挥台和回执窗口。
- PowerToys Run / Keyboard Manager：绑定唤醒、暂停、紧急停止快捷键。

核心发送仍由 UIA/CDP 和事件回执完成，即使 PowerToys 未运行，协作机制也保持可用。
建议将 Workspaces 和 FancyZones 配置导出到部署手册，首次安装后由人工确认布局，再启用无人值守。
