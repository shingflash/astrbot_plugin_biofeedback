# CHANGELOG

## 0.1.0
- 痛值层：外部信号采集（端口/日志增量/外部 JSON） → 0-100 pain → 四段行为收缩 → 慢衰减。
- 优先级栈：L0 不伤害具体的人（代码内硬顶，配置不可改）/ L1 执行权与逐项许可 / L2 身份认定交给人类。
- 每轮通过 `on_llm_request` 注入 `priority.COMPACT_RULES`，防止模型越权跑偏。
- 命令：`/prio`、`/bio status`（公开），`/bio unlock`、`/bio reset`（管理员）。
- `state.json` 只存 pain、信号分、事故时间戳，不存聊天内容。
