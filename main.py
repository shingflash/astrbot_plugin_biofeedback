"""生物反馈：外部信号 → 0-100 pain → 四段权限收缩。

设计要点：
  * 只收缩权限，不做"别再试"的判决；失败是资料不是案底。
  * 衰减慢于累积（受伤快、养伤慢）。
  * pain 全部由外部测量折算，不允许 agent 自述。
  * pain > halt 时必须由管理员解锁，agent 自己不能解锁。

三层信号：
  1. 端口/进程健康（probe_ports，TCP 连不上即事故）
  2. 日志异常增量（log_path，增量读，不怕大文件）
  3. 外部脚本写入的 JSON（extern_signals_file：余额燃烧、否决次数、运行时长等）

所有个人路径一律走配置项，无默认值；未配置即跳过对应信号。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import time
from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api import logger

try:
    from . import priority
except ImportError:
    import priority  # type: ignore

LEVELS = ("normal", "throttle", "readonly", "halt")


@register(
    "astrbot_plugin_biofeedback",
    "shingflash",
    "生物反馈：外部信号折算痛值，按四段收缩权限",
    "1.1.0",
)
class BioFeedback(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.conf = config or {}
        self.base_dir = str(self.conf.get("base_dir") or "").strip()
        self.pw_file = str(self.conf.get("password_file") or "").strip()
        self.data_dir = Path(StarTools.get_data_dir("astrbot_plugin_biofeedback"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.data_dir / "state.json"
        self._state = self._load_state()
        self._task: asyncio.Task | None = None
        self._log_offset = int(self._state.get("_log_offset", 0) or 0)

    # ---------- 状态 ----------

    def _load_state(self) -> dict:
        default = {
            "pain": 0.0,
            "signals": {},
            "last_incident": 0.0,
            "last_decay": time.time(),
            "unlocked_at": 0.0,
        }
        try:
            if self.state_file.exists():
                d = json.loads(self.state_file.read_text(encoding="utf-8"))
                default.update({k: d[k] for k in d if k in default})
        except Exception as e:
            logger.warning("[biofeedback] state load failed: %s", e)
        return default

    def _save_state(self) -> None:
        self._state["updated_at"] = int(time.time())
        self._state["_log_offset"] = self._log_offset
        try:
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.state_file)
        except Exception as e:
            logger.warning("[biofeedback] state save failed: %s", e)

    # ---------- 信号采集 ----------

    def _port_ok(self, target: str) -> bool:
        host, _, port = str(target).rpartition(":")
        try:
            with socket.create_connection((host or "127.0.0.1", int(port)), timeout=3):
                return True
        except Exception:
            return False

    def _log_errors(self) -> float:
        """增量读日志，只统计新出现的异常行，不怕大文件。"""
        path = str(self.conf.get("log_path") or "").strip()
        if not path or not os.path.isfile(path):
            return 0.0
        pat = re.compile(self.conf.get("log_error_pattern") or "Traceback|ERROR|CRITICAL")
        try:
            size = os.path.getsize(path)
            if size < self._log_offset:  # 被截断/轮转
                self._log_offset = 0
            with open(path, encoding="utf-8", errors="ignore") as f:
                f.seek(self._log_offset)
                new = f.read()
                self._log_offset = f.tell()
        except Exception:
            return 0.0
        hits = len(pat.findall(new))
        return min(100.0, hits * 20.0)

    def _extern(self) -> dict:
        path = str(self.conf.get("extern_signals_file") or "").strip()
        if not path or not os.path.isfile(path):
            return {}
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return {}
        out = {}
        for k, v in (d or {}).items():
            try:
                out[k] = max(0.0, min(100.0, float(v)))
            except (TypeError, ValueError):
                continue
        return out

    def _collect(self) -> dict:
        sig: dict[str, float] = {}

        ports = [p for p in (self.conf.get("probe_ports") or []) if str(p).strip()]
        if ports:
            down = [p for p in ports if not self._port_ok(str(p))]
            sig["process_health"] = 0.0 if not down else min(100.0, 60.0 * len(down))
        else:
            sig["process_health"] = 0.0

        sig["log_errors"] = self._log_errors()
        sig.update(self._extern())
        for k in ("balance_burn", "rejection_count", "thermal_runtime"):
            sig.setdefault(k, 0.0)
        return sig

    # ---------- 痛值累积与衰减 ----------

    def _compute(self) -> float:
        """受伤快、养伤慢。

        raw   = Σ(权重 × 信号)/100，落在 0-100
        raw 高于 incident_floor 时，pain 至少抬到 raw，并保留 prev 的 unhealed_ratio
        raw 低于 incident_floor 时，按 decay_per_hour 慢慢养伤
        """
        weights: dict = self.conf.get("weights") or {}
        sig = self._collect()
        self._state["signals"] = {k: round(v, 1) for k, v in sig.items()}

        raw = 0.0
        for k, w in weights.items():
            try:
                raw += float(w) * float(sig.get(k, 0.0)) / 100.0
            except (TypeError, ValueError):
                continue
        raw = max(0.0, min(100.0, raw))

        now = time.time()
        prev = float(self._state.get("pain", 0.0))
        floor = float(self.conf.get("incident_floor", 5.0))
        keep = float(self.conf.get("unhealed_ratio", 0.7))

        if raw > floor:
            pain = max(prev * keep, raw)          # 受伤快：立刻抬起来
            self._state["last_incident"] = int(now)
            self._state["last_decay"] = now
        else:
            hours = max(0.0, now - float(self._state.get("last_decay", now))) / 3600.0
            pain = max(0.0, prev - float(self.conf.get("decay_per_hour", 2)) * hours)
            self._state["last_decay"] = now

        self._state["pain"] = round(min(100.0, max(0.0, pain)), 1)
        self._save_state()
        return self._state["pain"]

    def _decay_only(self) -> None:
        """不解锁、不采样的时机只做衰减，避免 pain 冻结在最后值。"""
        now = time.time()
        prev = float(self._state.get("pain", 0.0))
        hours = max(0.0, now - float(self._state.get("last_decay", now))) / 3600.0
        if hours <= 0:
            return
        self._state["pain"] = round(max(0.0, prev - float(self.conf.get("decay_per_hour", 2)) * hours), 1)
        self._state["last_decay"] = now

    # ---------- 段位与收缩 ----------

    def level(self) -> str:
        t = self.conf.get("thresholds") or {}
        p = float(self._state.get("pain", 0.0))
        if p >= float(t.get("halt", 85)):
            return "halt"
        if p >= float(t.get("readonly", 60)):
            return "readonly"
        if p >= float(t.get("throttle", 30)):
            return "throttle"
        return "normal"

    def is_allowed(self, cost_ratio: float = 0.0) -> tuple[bool, str]:
        """cost_ratio: 该动作成本占日预算比例。写文件/花钱前问一句。"""
        self._decay_only()
        lv = self.level()
        if lv == "halt":
            return False, "pain>=halt，停机待审，须管理员 /bio unlock"
        if lv == "readonly":
            return False, "pain 处于只读段，只能记录与汇报，不改持久化文件"
        if lv == "throttle" and cost_ratio > float(self.conf.get("throttle_cost_cap", 0.025)):
            return False, "pain 处于降频段，单次成本上限减半"
        return True, lv

    def record_veto(self, n: int = 1) -> None:
        """被否决/被纠正一次就调一次；其他插件也可以调。"""
        step = float(self.conf.get("veto_step", 25.0))
        base = float(self._state.get("signals", {}).get("rejection_count", 0.0))
        self._state.setdefault("signals", {})["rejection_count"] = min(100.0, base + step * n)
        self._compute()

    def _pw_ok(self, given: str = "") -> tuple[bool, str]:
        if not self.pw_file:
            return False, "未配置 password_file，口令解锁不可用（请在面板配置口令文件路径）"
        pw, valid = priority.password_state(self.pw_file)
        if not valid:
            return False, "口令无效或已过期"
        return bool(given) and given.strip() == (pw or ""), ""

    # ---------- 生命周期 ----------

    async def initialize(self):
        if not self.conf.get("enabled", True):
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("[biofeedback] started, pain=%.1f", float(self._state.get("pain", 0)))

    async def _loop(self):
        while True:
            try:
                await asyncio.to_thread(self._compute)
            except Exception as e:  # 采样失败不能把自己弄崩
                logger.warning("[biofeedback] sample failed: %s", e)
            await asyncio.sleep(max(30, int(self.conf.get("probe_interval", 300))))

    async def terminate(self):
        if self._task:
            self._task.cancel()
        self._save_state()

    # ---------- 注入 ----------

    @filter.on_llm_request()
    async def inject_priority(self, event: AstrMessageEvent, req):
        """每轮把 L0/L1/L2 硬约束塞进系统提示，不随 pain 关闭。"""
        if not self.conf.get("inject_priority", True):
            return
        extra = ""
        lv = self.level()
        if lv != "normal":
            extra = f"当前自我约束段位={lv}（pain={self._state.get('pain', 0):.0f}），高成本动作先问主人。"
        block = "\n\n" + priority.core_rules(self.base_dir) + (("\n" + extra) if extra else "")
        try:
            req.system_prompt = (req.system_prompt or "") + block
        except Exception as e:
            logger.warning("[biofeedback] inject failed: %s", e)

    # ---------- 命令 ----------

    @filter.command("prio")
    async def prio(self, event: AstrMessageEvent, which: str = ""):
        """压缩版常驻，全文按需：/prio 0|1|3|power"""
        key = (which or "").strip().lower()
        if not key or key in ("core", "c"):
            text = priority.core_rules(self.base_dir)
        else:
            text = priority.full_text(self.base_dir, key)
        return event.plain_result(text)

    @filter.command("bio")
    async def bio(self, event: AstrMessageEvent, sub: str = "status", arg: str = ""):
        sub = (sub or "status").lower()

        if sub in ("status", "s", ""):
            st = self._compute()
            sig = self._state.get("signals") or {}
            body = ", ".join(f"{k}={v:.0f}" for k, v in sig.items()) or "无"
            return event.plain_result(
                f"pain={st:.1f} 段位={self.level()}\n信号: {body}\n处置: {self.is_allowed(0)[1]}"
            )

        # 以下全部只给管理员：解锁/重置等于放宽安全约束
        if sub == "unlock":
            ok, why = self._pw_ok(arg)
            if not event.is_admin() and not ok:
                return event.plain_result(why or "口令无效")
            if self.level() != "halt":
                return event.plain_result(f"当前段位={self.level()}，不需要解锁")
            t = self.conf.get("thresholds") or {}
            self._state["pain"] = float(t.get("readonly", 60)) - 1
            self._state["unlocked_at"] = int(time.time())
            self._save_state()
            return event.plain_result(f"已解锁到 readonly 下沿（pain={self._state['pain']:.1f}）")

        if sub == "reset":
            ok, why = self._pw_ok(arg)
            if not event.is_admin() and not ok:
                return event.plain_result(why or "口令无效")
            self._state.update({
                "pain": 0.0, "signals": {}, "last_incident": 0.0,
                "last_decay": time.time(), "unlocked_at": int(time.time()),
            })
            self._log_offset = 0
            self._save_state()
            return event.plain_result("pain 已清零，信号重采")

        return event.plain_result("用法：/bio status | /bio unlock <口令> | /bio reset <口令>")
