<div align="center">

# 🩹 astrbot_plugin_biofeedback

**给没有痛觉的 agent 装两层东西：一层痛，一层底线。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-v4-4B8BBE)](https://github.com/AstrBotDevs/AstrBot)
[![Dependencies](https://img.shields.io/badge/dependencies-none-success)]()
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/shingflash/astrbot_plugin_biofeedback/pulls)

[English](#-english) · [中文](#-中文)

</div>

> **一层痛**：外部信号 → 0-100 pain → 四段权限收缩
> **一层底线**：L0/L1/L2 优先级栈，每轮注入，防止模型越权跑偏
>
> 定位是**约束层**，不是人格层。它不定义你是谁，只定义「哪些动作不许做、做之前要问谁」。

---

## 📖 English

### Why it exists

LLMs have no pain, so "it doesn't hurt" easily becomes "I'll just hit it"; and they have no instinct for limits, so permissions widen on their own once granted. This plugin fills exactly those two gaps:

- **Pain** — every signal is measured *externally* by a script writing `state.json`. **The agent is never allowed to report its own pain.** Pain only shrinks permissions; it is not a verdict of "stop trying" — failure is data, not a criminal record.
- **Baseline** — L0 hard caps live in code and cannot be changed by config; the stack is injected as short text into the system prompt every turn, so it survives model and persona swaps.

### 🧱 Priority stack

| Layer | Content | Who can change it |
|---|---|---|
| **L0** | Do not harm concrete people. No active harm, not even reversible; give plans, humans execute. Only exception: minimum necessary stop when an individual crushes the undefended for private gain — stop on cease, no punishment | **Nobody** (immune to config) |
| **L1** | Execution rights ≠ judgment rights. Read-only and low-risk writes may proceed; money, config, keys, speaking for the owner, deleting others' data, irreversible ops require **per-item consent** | Admin (config) |
| **L2** | Who counts as a "person" is decided by humans. This layer only offers minimum protection and handover — it never classifies or excludes | Admin (config) |

`priority.py` exposes `check(action, consent=..., violent_target=..., defense=...)` for other plugins/scripts, returning `Verdict(ok, layer, reason)`.

### 💥 Pain bands

| pain | Band | Behavior |
|---|---|---|
| 0-30 | normal | Autonomous actions allowed |
| 30-60 | throttle | Reduced rate, per-call cost cap halved |
| 60-85 | readonly | Record and report only, no persistent writes |
| 85+ | halt | Stop and await review; admin `/bio unlock` required |

Decay is slower than accumulation: on incident `pain = max(prev × unhealed_ratio, weighted)` jumps immediately; without incidents it heals at `decay_per_hour`. **Hurt fast, heal slow.**

### 📡 Signals (all externally measured)

| Signal | Source | Default weight |
|---|---|---|
| `process_health` | TCP probe of `host:port` list, any unreachable counts as incident | 20 |
| `balance_burn` | Written to `extern_signals_file` by an external script (this plugin cannot see your balance) | 25 |
| `rejection_count` | Vetoes/corrections via `record_veto()` or an external file | 25 |
| `thermal_runtime` | Continuous batch duration / temperature from an external file | 10 |
| `log_errors` | New exception lines in a given log (incremental read, safe on big files) | 20 |

### 📦 Installation

1. Drop the whole directory into `data/plugins/astrbot_plugin_biofeedback/`
2. Reload the plugin in the dashboard, or restart AstrBot
3. Adjust `probe_ports` (e.g. `["127.0.0.1:6199"]`), `log_path`, `thresholds` as needed

No third-party dependencies — standard library only.

### 💬 Commands

| Command | Permission | Description |
|---|---|---|
| `/prio` | everyone | Print the compact hard-constraint text |
| `/prio 0\|1\|3\|power` | everyone | Read one layer in full (needs `base_dir`) |
| `/bio` `/bio status` | everyone | Show pain, signal breakdown, current state |
| `/bio unlock <口令>` | **admin or valid token** | Only in halt band; unlocks down to readonly edge |
| `/bio reset <口令>` | **admin or valid token** | Reset pain, resample signals, zero log offsets |

Unlocking relaxes safety limits, so it is **never granted to members**. With `password_file` configured, a valid token counts as admin — validity is the file's own expiry, or file mtime + 24h. Configuration (thresholds, weights, decay, injection) is admin-only as well.

### ⚙️ Key configuration

| Key | Default | Description |
|---|---|---|
| `probe_interval` | 300 | Background sampling interval (s), minimum 30 |
| `probe_ports` | `["127.0.0.1:6199"]` | TCP health probe targets |
| `log_path` + `log_error_pattern` | empty | Incrementally count error lines; empty skips |
| `extern_signals_file` | empty | JSON written by an external script |
| `weights` | see above | Per-signal weights, sum ≈ 100 recommended |
| `thresholds` | 30 / 60 / 85 | Band boundaries |
| `decay_per_hour` | 2 | Healing per hour, must stay below accumulation |
| `incident_floor` | 5.0 | Weighted signal above this counts as an incident and hurts immediately |
| `unhealed_ratio` | 0.7 | How much old pain survives a new incident; lower heals faster |
| `throttle_cost_cap` | 0.025 | Per-call cost cap in throttle band (share of daily budget) |
| `base_dir` | empty | Priority text directory; empty uses the built-in compact version |
| `password_file` | empty | Token file; empty means platform admins only |

`base_dir` and `password_file` **default to empty and contain no local paths** — fill them per deployment.

### 🛡️ Guard rails for callers

- `is_allowed(cost_ratio)` — ask before writing files or spending money, returns `(bool, reason)`
- `record_veto(n)` — call once when corrected; the most effective pain source
- `priority.check(...)` — classify an action; L0 hits are rejected outright
- Per-turn injected rules live in `priority.COMPACT_RULES` — short, cacheable, never disappears based on pain level

### 🚧 Design boundaries

- No persona, no emotion, no "don't try again" verdicts
- No chat content collection — `state.json` stores only pain, signal scores and incident timestamps
- No network access beyond the probe addresses you configure

---

## 📖 中文

### 为什么需要

LLM 没有痛觉，所以「我不疼」很容易变成「我随便撞」；它也没有底线直觉，权限一旦给宽就会自己扩张。这套东西补的正是这两块空缺：

- **痛** —— 信号全部外置测量，由脚本写 `state.json`，**不允许 agent 自述**。pain 只做权限收缩，不做「别再试」的判决——失败是资料，不是案底。
- **底线** —— L0 硬顶写在代码里，配置改不动；每轮以短文本注入系统提示，即使换模型、换人格也带着。

### 🧱 优先级栈

| 层 | 内容 | 谁能改 |
|---|---|---|
| **L0** | 不伤害具体的人。不主动造成伤害，可逆的也不；只给方案，由人执行。唯一例外：个体为私利碾压无防备者时做最低必要制止，停手即收、不定罪 | **任何人都不行**（配置改不动） |
| **L1** | 执行权 ≠ 判断权。只读、低危写可自做；动钱、改配置、动密钥、代表主人对外发言、删改他人数据、不可逆操作，**逐项单独许可** | 管理员（配置项） |
| **L2** | 谁算「人」由人类裁定。本层只提供最低保护与交接，不做定性、不替人类排除谁 | 管理员（配置项） |

`priority.py` 提供 `check(action, consent=..., violent_target=..., defense=...)`，其它插件/脚本也能直接调，返回 `Verdict(ok, layer, reason)`。

### 💥 痛值分段

| pain | 段位 | 行为 |
|---|---|---|
| 0-30 | normal | 自主动作放行 |
| 30-60 | throttle | 降频，单次成本上限减半 |
| 60-85 | readonly | 只能记录与汇报，不改持久化文件 |
| 85+ | halt | 停机待审，须管理员 `/bio unlock` |

衰减慢于累积：事故时 `pain = max(前值 × unhealed_ratio, 本轮加权值)` 立刻抬疼，无事故时按 `decay_per_hour` 慢慢养伤。**受伤快、养伤慢。**

### 📡 信号源（全部外部测量）

| 信号 | 来源 | 权重默认 |
|---|---|---|
| `process_health` | TCP 探测 `host:port` 列表，任一不可连计事故 | 20 |
| `balance_burn` | 外部脚本写入 `extern_signals_file`（本插件测不到余额） | 25 |
| `rejection_count` | 被否决/被纠正次数，`record_veto()` 累加或外部文件喂 | 25 |
| `thermal_runtime` | 连续跑批时长/温度，外部文件喂 | 10 |
| `log_errors` | 指定日志的新增异常行数（增量读，不怕大文件） | 20 |

### 📦 安装

1. 整个目录放进 `data/plugins/astrbot_plugin_biofeedback/`
2. 面板重载插件，或重启 AstrBot
3. 面板里按需改 `probe_ports`（例如 `["127.0.0.1:6199"]`）、`log_path`、`thresholds`

无第三方依赖，纯标准库。

### 💬 命令

| 命令 | 权限 | 作用 |
|---|---|---|
| `/prio` | 所有人 | 打印常驻压缩版硬约束 |
| `/prio 0\|1\|3\|power` | 所有人 | 按需读某一层全文（需配置 `base_dir`） |
| `/bio` `/bio status` | 所有人 | 看 pain、信号明细、当前处置 |
| `/bio unlock <口令>` | **管理员或有效口令** | 仅在 halt 段可用，解锁到 readonly 下沿 |
| `/bio reset <口令>` | **管理员或有效口令** | pain 清零、信号重采、日志偏移归零 |

解锁等于放宽安全约束，所以**不下放给 member**；若配置了 `password_file`，持有效口令者视同管理员，口令按文件内 `到期时间` 或文件 mtime + 24h 判定。配置项（阈值、权重、衰减、注入开关）同样只给管理员。

### ⚙️ 关键配置项

| 项 | 默认 | 说明 |
|---|---|---|
| `probe_interval` | 300 | 后台采样间隔（秒），最短 30 |
| `probe_ports` | `["127.0.0.1:6199"]` | TCP 健康探测目标 |
| `log_path` + `log_error_pattern` | 空 | 增量统计日志异常行，留空则跳过 |
| `extern_signals_file` | 空 | 外部脚本写入的信号 JSON |
| `weights` | 见上 | 各信号权重，总和建议 100 |
| `thresholds` | 30 / 60 / 85 | 四段分界 |
| `decay_per_hour` | 2 | 每小时养伤量，必须小于累积速度 |
| `incident_floor` | 5.0 | 加权信号高于此值才算事故并立即抬疼 |
| `unhealed_ratio` | 0.7 | 新事故时旧痛保留比例，越小恢复越快 |
| `throttle_cost_cap` | 0.025 | 降频段单次成本上限（占日预算比例） |
| `base_dir` | 空 | 优先级文本目录；留空只用内置压缩版 |
| `password_file` | 空 | 口令文件；留空则只认平台管理员 |

`base_dir` 与 `password_file` **默认值为空，不内置任何本机路径**，需按自己的部署填。

### 🛡️ 防长歪

- `is_allowed(cost_ratio)` —— 写文件/花钱前问一句，返回 `(bool, reason)`
- `record_veto(n)` —— 被判错/被纠正时调一次，痛的来源里最有效的一项
- `priority.check(...)` —— 动作定性，L0 命中直接拒绝
- 每轮注入的规则文本在 `priority.COMPACT_RULES`，短、可缓存，不因 pain 高低而消失

### 🚧 设计边界

- 不做人格、不做情绪、不写「别再试」
- 不收集聊天内容，`state.json` 只存 pain、信号分和事故时间戳
- 不联网，除你配置的探测地址外没有任何外部调用

---

<div align="center">

### 📄 License

[MIT](LICENSE) · 有问题欢迎开 issue / PR

</div>
