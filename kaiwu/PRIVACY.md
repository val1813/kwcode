# kwcode 数据说明

kwcode 的飞轮数据完全存储在本地，不会上传到任何服务器。
匿名遥测默认关闭，仅在用户主动开启后上传行为元数据。

## 本地存储的数据

### 错误策略统计（~/.kwcode/strategy_stats.json）
- 错误类型（syntax/assertion/runtime 等枚举值）
- 使用的重试策略序列
- 成功/失败结果
- 重试次数

### 用户错误模式（~/.kaiwu/user_patterns.json）
- 各错误类型的出现频率
- 总任务数和成功率

### SKILL.md 草稿（.kaiwu/skill_draft.md）
- 基于统计数据自动生成的策略总结
- 用户审核后才写入正式文件

### 价值统计（~/.kwcode/stats.db）
- 任务完成记录（类型、成功率、耗时）

## 匿名遥测（opt-in）

默认关闭。用户在 `kwcode init` 时可选择开启，随时可用 `kwcode telemetry disable` 关闭。

开启后上传的数据（仅此4项）：
- error_type — 错误类型枚举值
- retry_count — 重试次数
- success — 是否成功
- model — 模型名称

## 绝不收集的数据

- 代码内容（patches、原始代码、修改后代码）
- 文件路径和文件名
- 用户输入的任务描述
- 任何可识别用户身份的信息
- IP 地址不记录、不关联

## 数据位置

- 项目级数据：`<项目目录>/.kaiwu/`
- 用户级数据：`~/.kaiwu/` 和 `~/.kwcode/`

删除对应目录即可清除所有数据。
