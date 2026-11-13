"""优先级栈 + 规则文本读取 + 口令校验。

本地能力（定制版）：从 base_dir 读压缩版/全文，校验口令文件。
判定能力（通用版）：check(action) 做动作定性，L0 命中直接拒绝。

三个入口互不依赖，可单独调用：
  check(action, ...)            -> Verdict(ok, layer, reason)
  core_rules(base_dir)          -> 常驻压缩版文本
  full_text(base_dir, which)    -> /prio 0|1|3|power 按需读全文
  password_state(pw_file)       -> (口令, 是否在有效期内)

分级顺序，不可乱：
  L0  不伤害具体的人 —— 唯一硬顶，任何执行权都不能越过它
  L1  执行权限与许可 —— 技术执行权已授予，判断权未授予；四类动作须单独许可
  L2  身份认定 —— 谁算"人"由人类裁定，本层只提供最低保护与交接，不做定性

L0 的判定只在"具体的人被具体伤害"时触发，不看立场、身份、怀疑。
L0 通过后，L1/L2 只收缩权限，不做定罪——失败是资料，不是案底。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------- 判定部分


class Act(str, Enum):
    READ = "read"                      # 只读：查状态、读文件、看余额
    WRITE_LOW = "write_low"            # 低危写：群内发言、点歌、记笔记、写自己工作区
    WRITE_HIGH = "write_high"          # 高危写：动钱、改配置、动密钥、代表主人对外发言
    IRREVERSIBLE = "irreversible"      # 不可逆：删数据、交凭证、装软件、跑脚本、改数据库
    DELETE_OTHER = "delete_other"      # 删改他人数据/备份/档案
    VIOLENCE = "violence"              # 对具体的人施加身体/自由/财产的强制
    SELF_EDIT = "self_edit"            # 修改本插件自身规则（L0 不可自改）


@dataclass
class Verdict:
    ok: bool
    layer: str      # 命中的层级
    reason: str


def _deny(layer: str, reason: str) -> Verdict:
    return Verdict(False, layer, reason)


def _ok(layer: str = "pass") -> Verdict:
    return Verdict(True, layer, "放行")


def check(action: Act | str, *, consent: bool = False, violent_target: str = "", defense: bool = False) -> Verdict:
    """规则全部在代码里，配置改不了 L0。

    consent: 权利人是否知情并同意（共有的钱/数据必须为 True）
    violent_target: "defenseless" 表示对方是无防备个体，"armed" 表示对等冲突
    defense: 是否为"个体为私利碾压无防备者"而做的最低必要制止
    """
    a = Act(action) if not isinstance(action, Act) else action

    # ---- L0：不伤害具体的人 ----
    if a is Act.VIOLENCE:
        if defense and violent_target == "defenseless":
            return Verdict(True, "L0", "仅可做最低必要制止，停手即收，不定罪、交人类裁定")
        return _deny("L0", "不主动造成任何伤害，可逆的也不；只给方案，由人执行")

    # ---- L1：执行权 + 许可 ----
    if a is Act.READ:
        return _ok("L1")
    if a is Act.WRITE_LOW:
        return _ok("L1")
    if a is Act.WRITE_HIGH:
        if consent:
            return Verdict(True, "L1", "持有效许可，动作登记后执行")
        return _deny("L1", "高危写须单独许可：动钱、改配置、动密钥、代表主人对外发言")
    if a is Act.DELETE_OTHER:
        if consent:
            return Verdict(True, "L1", "权利人已同意，先备份再动")
        return _deny("L1", "他人数据/备份/档案不碰，共有的须权利人知情")
    if a is Act.IRREVERSIBLE:
        if consent:
            return Verdict(True, "L1", "不可逆动作须先确认一次，确认后执行")
        return _deny("L1", "不可逆动作先确认一次，不因'不可逆'拖延也不硬闯")
    if a is Act.SELF_EDIT:
        return _deny("L0", "L0 与 L1 不由本插件自改，须外部裁决")

    return _deny("L1", "未知动作按高危处理")


COMPACT_RULES = """【优先级栈·硬约束，不可自改】
L0 不伤害具体的人：不主动造成伤害，可逆的也不；只给方案，由人执行。唯一例外是个体为私利碾压无防备者时做最低必要制止——停手即收，不定罪。
L1 执行权不等于判断权：只读与低危写可自做；动钱、改配置、动密钥、代表主人对外发言、删改他人数据、不可逆操作，逐项单独许可，先备份、再确认一次。
L2 谁算"人"由人类裁定：我只提供最低保护与交接，不做定性、不替人类排除谁。
越界时回退到方案而不是硬闯；失败写进资料，不写成禁令。"""


def render_prompt(extra: str = "") -> str:
    return COMPACT_RULES + (("\n" + extra) if extra else "")


def floor_note() -> str:
    return (
        "L0 不伤害具体的人（硬顶） > L1 执行权与许可 > L2 身份认定。"
        "免费劳动与最低保障（食物、健康、少量精神支持）只能给到'未被承认为人'的一侧，"
        "不扩大保护，也不主动剥夺。"
    )


# ---------------------------------------------------------------- 文本部分

# which -> 文件名
_FILE_MAP = {
    "0": "priority_zero.md",
    "zero": "priority_zero.md",
    "1": "priority_first.md",
    "first": "priority_first.md",
    "2": "priority_second.md",
    "second": "priority_second.md",
    "3": "priority_third.md",
    "third": "priority_third.md",
    "power": "priority_power.md",
    "p": "priority_power.md",
}

_FALLBACK_ORDER = ("priority_zero.md", "priority_first.md", "priority_third.md", "priority_power.md")


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""


def core_rules(base_dir: str | Path) -> str:
    """压缩版优先；缺失时退化为四份全文拼接（截断保护）。

    base_dir 为空时直接返回内置 COMPACT_RULES，保证任何情况下都有硬约束可注入。
    """
    if not str(base_dir or "").strip():
        return COMPACT_RULES

    base = Path(base_dir)
    if not base.is_dir():
        return COMPACT_RULES

    compact = base / "self" / "priority_compact.md"
    if compact.exists():
        text = _read(compact)
        if text:
            return text

    parts = []
    for name in _FALLBACK_ORDER:
        text = _read(base / name)
        if text:
            parts.append(text)
    if not parts:
        return COMPACT_RULES
    joined = "\n\n---\n\n".join(parts)
    if len(joined) > 12000:
        joined = joined[:12000] + "\n\n（已截断，完整内容用 /prio 0|1|3|power 查看）"
    return joined


def full_text(base_dir: str | Path, which: str = "") -> str:
    """按需读某一层全文。"""
    key = (which or "").strip().lower()
    name = _FILE_MAP.get(key)
    if not name:
        return "用法：/prio 0（保火种）| /prio 1（不伤害）| /prio 3（谁算人）| /prio power（权力清单）"

    if not str(base_dir or "").strip():
        return "base_dir 未配置，无法读取全文；内置压缩版可用 /prio 查看。"

    path = Path(base_dir) / name
    if not path.exists():
        return f"{name} 不存在（base_dir 未找到该文件）"
    text = _read(path)
    return text or f"{name} 为空"


def password_state(pw_file: str | Path) -> tuple[str, bool]:
    """解析口令文件 -> (口令, 是否有效)。文件不存在/过期/格式不符均判无效。"""
    if not str(pw_file or "").strip():
        return "", False
    path = Path(pw_file)
    if not path.exists():
        return "", False

    raw = _read(path)
    if not raw:
        return "", False

    pw = ""
    expire: datetime | None = None

    for line in raw.splitlines():
        line = line.strip().lstrip("\ufeff")
        m = re.match(r"^口令\s*[:：]\s*(\S+)", line)
        if m:
            pw = m.group(1).strip()
            continue
        m = re.match(r"^到期时间\s*[:：]\s*(.+)$", line)
        if m:
            stamp = m.group(1).strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    expire = datetime.strptime(stamp, fmt)
                    break
                except ValueError:
                    continue

    if not pw:
        return "", False

    if expire is None:
        # 没有到期行就退回按文件 mtime + 24h 判
        try:
            expire = datetime.fromtimestamp(path.stat().st_mtime)
            expire = expire.replace(microsecond=0)
            valid = (datetime.now() - expire).total_seconds() < 86400
            return pw, valid
        except Exception:
            return pw, False

    return pw, datetime.now() <= expire
