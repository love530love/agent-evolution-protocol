# 浏览器适配器

AEP 支持将 Playwright、CDP 和 Agent 内置浏览器作为可替换执行层。浏览器适配器只负责定位、输入、截图、网络状态和页面回读；任务是否完成仍由 `aep-readback-v1` 和 Agent ACK 决定。

可选安装：

```powershell
pip install -e ".[browser]"
playwright install chromium
```

推荐顺序：Agent 内置浏览器 → Playwright/CDP → UIA → OCR/文件信箱。每次切换通道都要保留原 event ID，并将失败签名写入能力评分，避免同一个坏定位器反复消耗资源。
