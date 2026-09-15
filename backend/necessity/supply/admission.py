"""A4 的准入钩子 —— 把准入门接进真实的扩展加载路径（规范 §6.2）。

## 为什么需要这一层

规范 §6.2 把 A4 的定位写成「**入口门**：只有通过准入的扩展才能进入
`skills/` / `plugins/` / `mcp_hub` 的加载器」。

在此之前 `supply/` 是一个**纯离线模块** —— 测试全过、接口正确，
但没有任何东西调用它。那是「写了但没生效」：报告会说「已扫描」，
而实际加载路径上没有任何扫描发生。

## 三条安全约束（改宿主启动路径必须有）

1. **默认不阻断**。`mode="observe"`（默认）只记录，不拦任何扩展。
   规范 §6.8 的门禁说检出率 <90% 时「默认关闭，仅作参考提示」——
   而当前实测检出率是 100%/0%，但语料是自造的（§6.11 警告过），
   所以**在真实语料上验证之前不应该拦人**。
2. **失败必须放行**。与 `mount` 的契约一致：准入是质量组件，
   它炸了不该让 Aurora 起不来。异常一律放行 + 记录。
3. **可回退**。`mode="off"` 完全跳过；`enabled` 开关在配置里。
   不改加载器的既有逻辑，只在「发现一个扩展」时插一个判定点。

## 三档模式

    off      完全不介入（连扫描都不做）
    observe  扫描 + 记录 + 计数，但**永不阻断**（收集真实语料用）
    enforce  按 decision 阻断 reject / ask 的扩展
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from .gate import AdmissionReport, admit

logger = logging.getLogger("necessity.supply.admission")

MODE_OFF = "off"
MODE_OBSERVE = "observe"
MODE_ENFORCE = "enforce"
_MODES = (MODE_OFF, MODE_OBSERVE, MODE_ENFORCE)

# 准入日志（observe 模式的主要产出：真实扩展的判定分布）
DEFAULT_LOG = ".necessity/admission_log.jsonl"


def _mode_from_env() -> str:
    m = os.environ.get("AURORA_SUPPLY_ADMISSION", "").strip().lower()
    return m if m in _MODES else MODE_OBSERVE


@dataclass
class AdmissionResult:
    """一次准入判定的结论。"""

    extension_id: str
    allowed: bool = True          # observe 模式恒为 True
    report: AdmissionReport | None = None
    reason: str = ""

    @property
    def decision(self) -> str:
        return getattr(self.report, "decision", "allow") if self.report else "allow"


@dataclass
class AdmissionHook:
    """扩展加载路径上的准入判定点。

    刻意做成**纯函数式**（不持有加载器状态）：加载器只需在发现扩展时问一句
    「这个能不能进」，不需要知道准入内部怎么算。
    """

    mode: str = field(default_factory=_mode_from_env)
    log_path: str = DEFAULT_LOG
    scanned: int = 0
    blocked: int = 0
    rejections: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.mode not in _MODES:
            logger.warning("未知的准入模式 %r，退回 observe", self.mode)
            self.mode = MODE_OBSERVE

    # ── 判定 ─────────────────────────────────────────────────────

    def check(self, extension_id: str, path: str | Path,
              description: str = "") -> AdmissionResult:
        """判定一个扩展能否进入加载器。

        `observe` 模式下 `allowed` 恒为 True —— 这样在真实扩展上跑一段时间
        收集判定分布，而不会拦住任何人的工作。等拿到真实数据再决定是否
        enforce（规范 §0.3 的「阶段 A 观测 → 阶段 B 标定」）。
        """
        if self.mode == MODE_OFF:
            return AdmissionResult(extension_id=extension_id, allowed=True,
                                   reason="准入未启用")

        try:
            rep = admit(extension_id, str(path), description)
        except Exception as e:
            # 契约 2：准入自身故障必须放行 —— 它不该让 Aurora 起不来
            logger.warning("准入扫描失败，放行 %s: %s", extension_id, e)
            self._write_log(extension_id, "error", str(e))
            return AdmissionResult(extension_id=extension_id, allowed=True,
                                   reason=f"扫描失败已放行：{type(e).__name__}: {e}")

        self.scanned += 1
        decision = getattr(rep, "decision", "allow")
        self._write_log(extension_id, decision,
                        f"level={getattr(rep, 'level', '?')}")

        if self.mode == MODE_OBSERVE:
            return AdmissionResult(extension_id, True, rep,
                                   f"observe 模式：判定 {decision}，不阻断")

        # ⚠️ 「扫不到」不等于「有风险」。若判定唯一依据是 `scan_error`
        # （路径不存在 / 读不了），必须**放行**：那是「未知」，不是「危险」。
        # 实测踩过：enforce 模式下不存在的路径被判 high → ask → 拒绝，
        # 而加载器会因为一个不存在的目录崩掉 —— 纯粹把未知当成了危险。
        if self._is_unreadable(rep):
            self._write_log(extension_id, "unreadable_allowed",
                            "无法读取，按未知处理并放行")
            return AdmissionResult(
                extension_id, True, rep,
                "无法读取该扩展（不是判定为危险），已放行 —— 未知 ≠ 危险")

        # enforce
        if decision in ("reject", "ask"):
            self.blocked += 1
            self.rejections.append(f"{extension_id}: {decision}")
            return AdmissionResult(extension_id, False, rep,
                                   f"准入拒绝（{decision}）")
        return AdmissionResult(extension_id, True, rep, f"准入通过（{decision}）")

    @staticmethod
    def _is_unreadable(rep: AdmissionReport | None) -> bool:
        """判定是否「根本没读到内容」。

        只看 `scan_error` / `unreadable_file` 这两类规则 ——
        它们是「扫描没成功」的信号，与「扫到问题」是两回事。
        """
        if rep is None:
            return True
        rules = {str(getattr(f, "rule_id", "")) for f in (rep.findings or [])}
        return bool(rules) and rules <= {"scan_error", "unreadable_file"}

    # ── 记录 ─────────────────────────────────────────────────────

    def _write_log(self, extension_id: str, decision: str, note: str) -> None:
        """记一行判定结果。

        落盘失败只记 warning —— 观测数据的丢失不该影响扩展加载。
        """
        try:
            p = Path(self.log_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            rec = {"extension_id": extension_id, "decision": decision,
                   "mode": self.mode, "note": note}
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug("准入日志写入失败: %s", e)

    def stats(self) -> dict:
        return {"mode": self.mode, "scanned": self.scanned,
                "blocked": self.blocked, "rejections": list(self.rejections)}


# ── 全局单例（加载器用）──────────────────────────────────────────

_hook: AdmissionHook | None = None


def get_hook() -> AdmissionHook:
    """取全局准入钩子。首次调用时按环境变量建。"""
    global _hook
    if _hook is None:
        _hook = AdmissionHook()
    return _hook


def set_hook(hook: AdmissionHook | None) -> None:
    """注入（测试用）。"""
    global _hook
    _hook = hook


def reset() -> None:
    set_hook(None)


def check_extension(extension_id: str, path, description: str = "") -> AdmissionResult:
    """加载器调用的一行入口。

    集成方式（见 `backend/skills/__init__.py::_scan` 与
    `backend/plugins/__init__.py::discover`）：发现一个扩展时调它，
    拿到 `allowed=False` 就跳过该扩展并记日志。
    """
    try:
        return get_hook().check(extension_id, path, description)
    except Exception as e:
        # 双保险：即使 get_hook 本身出问题也必须放行
        logger.warning("准入钩子异常，放行 %s: %s", extension_id, e)
        return AdmissionResult(extension_id=extension_id, allowed=True,
                               reason=f"钩子异常已放行：{e}")


__all__ = [
    "AdmissionHook", "AdmissionResult", "DEFAULT_LOG", "MODE_ENFORCE",
    "MODE_OBSERVE", "MODE_OFF", "check_extension", "get_hook", "reset",
    "set_hook",
]
