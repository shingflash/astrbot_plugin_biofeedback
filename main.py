"""Biofeedback pain guard.

外部测量 -> 0-100 痛值 -> 四段权限收缩。pain 由脚本写入，不由 agent 自述。
设计要点：
  * 只收缩权限，不做"别再试"的判决；失败是资料不是案底。
  * 衰减慢于累积（受伤快、养伤慢）。
  * 痛值 > halt 时必须由管理员解锁，agent 自己不能解锁。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import time
from pathlib import Path

from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger

try:  # 作为包加载
    from . import priority
except ImportError:  # 单文件方式加载
    import priority  # type: ignore

LEVELS = ("normal", "throttle", "readonly", "halt")


@register(
    "astrbot_plugin_biofeedback",
    "shingflash",
    "外部信号折算痛值，按四段收缩权限",
    "0.1.0",
    "https://github.com/shingflash/astrbot_plugin_biofeedback",
)
class BiofeedbackStar(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.conf = config or {}
        self.data_dir: Path = StarTools.get_data_dir("astrbot_plugin_biofeedback")
        self.state_path = self.data_dir / "state.json"
        self._state = self._load_state()
        self._task: asyncio.Task | None = None
        self._log_offset = 0

    # ---------- 状态 ----------

    def _load_state(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            st = {}
        st.setdefault("pain", 0.0)
        st.setdefault("signals", {})
        st.setdefault("last_incident", 0)
        st.setdefault("last_decay", time.time())
        st.setdefault("unlocked_at", 0)
        return st

    def _save(self) -> None:
        self._state["updated_at"] = int(time.time())
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    # ---------- 信号 ----------

    def _port_ok(self, target: str) -> bool:
        host, _, port = target.rpartition(":")
        try:
            with socket.create_connection((host or "127.0.0.1", int(port)), timeout=3):
                return True
        except Exception:
            return False

    def _log_errors(self) -> float:
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
            with open(path, encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}

    def _collect(self) -> dict:
        sig: dict[str, float] = {}

        ports = [p for p in (self.conf.get("probe_ports") or []) if str(p).strip()]
        if ports:
            down = [p for p in ports if not self._port_ok(str(p))]
            sig["process_health"] = 0.0 if not down else min(100.0, 60.0 * len(down))
        else:
            sig["process_health"] = 0.0

        sig["log_errors"] = self._log_errors()
        for k, v in self._extern().items():
            try:
                sig[k] = max(0.0, min(100.0, float(v)))
            except (TypeError, ValueError):
                continue
        for k in ("balance_burn", "rejection_count", "thermal_runtime"):
            sig.setdefault(k, 0.0)
        return sig

    def _compute(self) -> float:
        weights: dict = self.conf.get("weights") or {}
        sig = self._collect()
        self._state["signals"] = sig
        raw = 0.0
        for k, w in weights.items():
            try:
                raw += float(w) * float(sig.get(k, 0.0)) / 100.0
            except (TypeError, ValueError):
                continue
        raw = max(0.0, min(100.0, raw))

        now = time.time()
        prev = float(self._state.get("pain", 0.0))
        pain = max(prev * 0.7, raw) if raw > 5 else prev  # 受伤快
        if raw <= 5:  # 养伤慢
            hours = (now - float(self._state.get("last_decay", now))) / 3600.0
            pain = max(0.0, pain - float(self.conf.get("decay_per_hour", 2)) * hours)
            self._state["last_decay"] = now
        if raw > 5:
            self._state["last_incident"] = int(now)
            self._state["last_decay"] = now
        self._state["pain"] = round(min(100.0, pain), 1)
        self._save()
        return self._state["pain"]

    # ---------- 对外接口 ----------

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
        lv = self.level()
        if lv == "halt":
            return False, "pain>=halt，停机待审，须管理员 /bio unlock"
        if lv == "readonly":
            return False, "pain 处于只读段，只能记录与汇报"
        if lv == "throttle" and cost_ratio > 0.025:
            return False, "pain 处于降频段，单次成本上限减半"
        return True, lv

    def record_veto(self, n: int = 1) -> None:
        """被否决/被纠正一次就调一次；其他插件也可以调。"""
        cur = float(self._state.get("signals", {}).get("rejection_count", 0.0))
        self._state.setdefault("signals", {})["rejection_count"] = min(100.0, cur + 25.0 * n)
        self._compute()

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
            await asyncio.sleep(int(self.conf.get("probe_interval", 300)))

    async def terminate(self):
        if self._task:
            self._task.cancel()

    # ---------- 优先级注入（防别的模型长歪） ----------

    @filter.on_llm_request()
    async def inject_priority(self, event: AstrMessageEvent, req):
        """每轮把 L0/L1/L2 硬约束塞进系统提示，短句、可缓存，不随 pain 关闭。"""
        if not self.conf.get("inject_priority", True):
            return
        extra = ""
        lv = self.level()
        if lv != "normal":
            extra = f"当前自我约束段位={lv}（pain={self._state.get('pain', 0):.0f}），高成本动作先问主人。"
        block = "\n\n" + priority.render_prompt(extra)
        try:
            req.system_prompt = (req.system_prompt or "") + block
        except Exception as e:
            logger.warning("[biofeedback] inject failed: %s", e)

    # ---------- 命令 ----------

    @filter.command("prio")
    async def prio(self, event: AstrMessageEvent):
        """查看优先级栈 + 当前段位"""
        event.set_result(event.plain_result(
            priority.render_prompt() + f"\n\npain={self._state.get('pain', 0):.1f} 段位={self.level()}"
        ))

    @filter.command("bio")
    async def bio(self, event: AstrMessageEvent, sub: str = ""):
        """bio status | bio unlock | bio reset | bio note <文本>"""
        sub = (sub or "status").strip().lower()
        args = event.message_str.split()
        arg = args[-1] if len(args) > 1 else ""

        if sub == "status":
            p = self._state.get("pain", 0.0)
            sig = self._state.get("signals", {}) or {}
            body = ", ".join(f"{k}={v:.0f}" for k, v in sig.items()) or "无"
            event.set_result(event.plain_result(
                f"pain={p:.1f} 段位={self.level()}\n信号: {body}"
            ))
            return

        # 以下全部只给管理员：解锁/重置等于放宽安全约束
        if not event.is_admin():
            event.set_result(event.plain_result("权限不足：只有管理员能动 pain"))
            return

        if sub == "unlock":
            lv = self.level()
            if lv != "halt":
                event.set_result(event.plain_result(f"当前段位={lv}，不需要解锁"))
                return
            self._state["pain"] = float((self.conf.get("thresholds") or {}).get("readonly", 60)) - 1
            self._state["unlocked_at"] = int(time.time())
            self._save()
            event.set_result(event.plain_result(f"已解锁，pain 降到 {self._state['pain']:.1f}"))
        elif sub == "reset":
            self._state["pain"] = 0.0
            self._state["signals"] = {}
            self._save()
            event.set_result(event.plain_result("pain 已清零，信号重采"))
        else:
            event.set_result(event.plain_result("用法：bio status / unlock / reset"))
