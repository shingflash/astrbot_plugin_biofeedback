# CHANGELOG

## 1.1.2 — 2026-11-13

相对 1.1.0：通读后逐条实测的修复，外加把默认值里的本机路径全部摘掉。

### 新增
- `veto_decay_per_hour`：否决分按小时衰减，不再一次否决永久抬疼。
- `collector_script` / `collector_file` / `collector_python`：可挂一个外部采集脚本，把 0-1 比例的实测信号换算成 0-100 分并入痛值。
- `_veto_value()`：否决分独立存储（`state.veto`），随时间衰减后再参与计算。

### 修复
- `_log_offset` 未进 `state.json` 默认表，重启后偏移丢失，会把整个日志重扫一遍、重复计异常。
- `record_veto()` 的分数写在内存 signals 里，下一次 `_collect()` 直接覆盖成 0，等于调用无效。
- 否决分只累积不衰减，加过就永远压在 `incident_floor` 之上，痛值再也降不下来。
- 事故时旧痛值被 `unhealed_ratio` 打折，一次小否决（raw 6.25）能把 78 的痛打到 54.6；改为只升不降，衰减只由无事故时的 `decay` 负责。
- 归一化把未观测的信号当成 0 分参与加权；改为只用已观测权重做分母，缺的项跳过。
- 插件重载时 `initialize()` 会再起一个采样循环，导致多个循环叠加；加单例保护。
- `event.is_admin()` 在部分适配器上不存在，改为 `getattr` 探测，取不到按非管理员处理。
- `probe_interval` 配成非数字时会让采样循环抛异常退出，加兜底并夹到 30-3600 秒。

### 变更（破坏性）
- `base_dir`、`password_file`、`collector_script`、`collector_file` 的默认值全部置空，改为在面板里填自己的路径，仓库里不再带任何本机路径。
- 旧配置项 `accident_coef` 不再生效，由 `unhealed_ratio` 兜底。

## 1.1.0 — 2026-11-13

痛值层补全 + 配置项去本机路径。历史版本不覆盖，0.1.0 仍可在 Tags/Releases 下载。

### 新增
- `log_errors` 信号：增量读日志（记录读取偏移到 `state.json._log_offset`），只统计新增异常行，日志轮转自动归零；`log_path` / `log_error_pattern` 可配。
- 后台采样循环 `initialize()` / `_loop()`：按 `probe_interval` 定时 `_compute()`，采样异常只记警告不中断。
- 配置项：`incident_floor`（事故判定下限）、`unhealed_ratio`（旧痛保留比例）、`throttle_cost_cap`（降频段成本上限）、`veto_step`（否决累加分）。

### 修复
- **痛值累积逻辑补全**：原实现事故时只按单轮加权值重算，前一轮的痛会被抹平；现改为 `pain = max(prev × unhealed_ratio, raw)`，真正做到受伤快、养伤慢。
- **无事故时不误清痛**：`raw <= incident_floor` 时只按 `decay_per_hour` 衰减，不再把 pain 覆盖成 0。
- **衰减时机**：`is_allowed()` 走独立 `_decay_only()`，不会因频繁询问而反复全量重采样。
- **解锁段位判断**：`/bio unlock` 先判段位再改值，非 halt 段直接返回"不需要解锁"，不再无条件下调。
- **口令未配置时**：`password_file` 留空时明确提示配置项，而不是笼统报"口令无效"。
- **状态写入原子化**：先写 `.tmp` 再 `os.replace`，避免写一半崩掉留下坏 `state.json`。

### 变更（破坏性）
- `base_dir` / `password_file` 默认值改为空字符串，不再内置任何本机路径；`base_dir` 为空时退回内置 `priority.COMPACT_RULES` 保证仍有硬约束注入。
- `priority.py` 合并两套实现：判定侧 `check()/Verdict/Act/COMPACT_RULES/render_prompt/floor_note`，文本侧 `core_rules()/full_text()/password_state()` 共存。
- `/prio` 支持 `/prio 0|1|3|power` 按需读全文，无参数仍返回压缩版。
- `/bio unlock`、`/bio reset` 允许"平台管理员身份"或"有效口令"二者之一。
- 插件类名统一为 `BioFeedback`，`register` 版本号 1.1.0。

### 保留
- `_conf_schema.json` 中 `type: object` 的 `items` 写成子字段定义字典（AstrBot `_parse_schema` 递归解析要求，写成元素类型会 `TypeError: string indices are integers`，缺 `items` 会 `KeyError: 'items'`）。
- `state.json` 只存 pain、信号分、时间戳与日志偏移，不存任何聊天内容。

## 0.1.0
- 痛值层：外部信号采集（端口/日志增量/外部 JSON） → 0-100 pain → 四段行为收缩 → 慢衰减。
- 优先级栈：L0 不伤害具体的人（代码内硬顶，配置不可改）/ L1 执行权与逐项许可 / L2 身份认定交给人类。
- 每轮通过 `on_llm_request` 注入 `priority.COMPACT_RULES`，防止模型越权跑偏。
- 命令：`/prio`、`/bio status`（公开），`/bio unlock`、`/bio reset`（管理员）。
- `state.json` 只存 pain、信号分、事故时间戳，不存聊天内容。
