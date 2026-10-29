# Tooltip / Toast / Alert / Modal / Popconfirm 处理规范

弹层识别是观察能力，不等于授权操作。AEP 统一先分类、记录证据，再按风险决定是否动作。

| 类型 | 默认策略 | 说明 |
|---|---|---|
| tooltip | `record-only` | 只读取，不主动触发或点击 |
| toast | `record-only` | 记录文本、来源和时间，按 TTL 去重 |
| alert | `pause-and-report` | 只有幂等且已有授权时才允许重试 |
| modal | `pause-and-report` | 阻塞主任务，重新观察控件树 |
| popconfirm | `wait-user-confirmation` | 发送、删除、上传、提交等动作不得默认确认 |

识别优先级为 UIA 控件树 → 浏览器 DOM/CDP → 截图/OCR。每次动作后必须重新读取弹层状态；旧坐标、旧控件索引和旧截图不可复用。

实测的“这轮回复失败 → 重试”属于 `alert`，因为用户明确授权重试且动作可重复验证；点击后必须确认错误提示消失或任务重新运行。
