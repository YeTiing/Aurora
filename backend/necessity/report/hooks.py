"""A1 的钩子装配 —— 让证据包在任务结束时自动产出（规范 §3.4）。

## 为什么不新增挂载点

规范 §3.4 明确要求：「**不新增挂载点**，复用 `mount.on_task_end()`」。
`mount.py` 已把「异常放行 / 超时统计 / 未启用零开销」收敛在一处，
再开一个挂载点等于把那套契约重写一遍（且必然漏掉某条）。

`CompositeHooks.on_task_end` 会自动转发给每个能力并把结果合并进同一个
dict（见其注释「合并而非覆盖」），所以本类只需实现 `on_task_end`。

## 只记事实、产出一次

规范 §3.6 的两段式决定了这里**只能**做同步段：
`build_bundle()` 立即交付 `partial`，必要性由 `enrich_bundle()` 在
空闲期补。**绝不在钩子里跑 reduce** —— 它要跑上百次测试（§6.4）。

## 失败不得影响任务

与所有能力一致：本方法的任何异常都会被 `CompositeHooks` 与 `mount._call`
捕获并放行。但**内容失败**（某个来源采不到）不是异常，而是
`collection_errors` 里的一条记录 —— 那是报告的一部分，必须让用户看到。
"""
from __future__ import annotations

import logging
from pathlib import Path

from backend.necessity.hooks import TaskResult

from .bundle import build_bundle
from .render import write_report
from .schema import EvidenceBundle

logger = logging.getLogger("necessity.report.hooks")

# 报告落盘目录（相对工作区）。与 `store` 的 `.necessity/` 同层，
# 但独立子目录 —— 报告是产出物，不是索引状态。
BUNDLE_DIRNAME = "bundles"


class ReportHooks:
    """A1 可验证补丁 —— 任务结束时产出证据包。"""

    name = "report"

    def __init__(self, *, workspace: str = ".", write: bool = True,
                 scanner=None, store=None) -> None:
        self.workspace = str(workspace or ".")
        self.write = bool(write)
        self.scanner = scanner
        self.store = store
        self.last_bundle: EvidenceBundle | None = None

    # ── 钩子 ─────────────────────────────────────────────────────

    def on_task_end(self, result: TaskResult) -> dict:
        """产出证据包并（可选）落盘。

        返回的是**指标 dict**（供 `mount.on_task_end` 合并），
        而完整证据包另存 `self.last_bundle` 并写盘。
        这样既能喂给评测侧（读几个数字），也不把整份报告塞进指标出口。
        """
        fresh = self._scan_changes(result)
        bundle = build_bundle(
            result,
            changes=fresh,
            store=self.store,
            symbols=self._touched_symbols(fresh),
            test_results=self._test_results(),
            requirements=self._requirements(),
            scanner=self.scanner,
        )
        self.last_bundle = bundle

        if self.write:
            try:
                d = Path(self.workspace) / ".necessity" / BUNDLE_DIRNAME
                jp, mp = write_report(bundle, str(d))
                logger.info("evidence bundle 已落盘: %s", jp)
            except Exception as e:
                # 落盘失败不影响任务，但要记进报告（否则用户以为没有报告）
                bundle.collection_errors.append(
                    f"报告落盘失败：{type(e).__name__}: {e}")

        return {
            "bundle_status": bundle.status,
            "bundle_pending": list(bundle.pending),
            "bundle_fields_filled": bundle.filled_ratio,
            "bundle_unverified_count": len(bundle.unverified),
            "bundle_collection_errors": len(bundle.collection_errors),
        }

    # ── 各来源（拿不到就返回空，由 bundle 记为「未采集」）─────────

    def _scan_changes(self, result: TaskResult):
        """取工作区改动。

        `scan_workspace` 是 Guard 的权威数据源，但它在这个钩子里可能
        尚未被调用过。所以这里**不主动扫**（那是 Guard 的职责），
        而是看有没有已缓存的变更；没有就返回 None 让 bundle 如实记为未采集。
        """
        cached = getattr(self, "_changes_cache", None)
        return cached

    def set_changes(self, changes) -> None:
        """由外部（Guard 的 scan_workspace 结果 / 评测 runner）注入改动。"""
        self._changes_cache = changes

    def _touched_symbols(self, changes):
        """从改动文件反查被触及的符号。没有索引库时返回 None。"""
        if self.store is None or not changes:
            return None
        out = []
        for c in changes:
            p = str(getattr(c, "path", ""))
            if not p:
                continue
            try:
                rows = self.store.query_symbols(self.workspace, p)
                out.extend(rows or [])
            except Exception as e:
                logger.debug("符号反查失败 %s: %s", p, e)
        return out or None

    def _test_results(self):
        """验证证据：调用方可注入；默认取不到。

        ⚠️ 规范 §3.5 标注 `verification` 需要「补采集点」（trace 的
        `test_run` 事件）。**没有就是没有** —— 这里不造假，
        由 bundle 的 `unverified` 显式声明「无法判定覆盖」。
        """
        cached = getattr(self, "_tests_cache", None)
        return cached

    def set_test_results(self, results) -> None:
        """由评测侧（`eval/verify.py` 的结果）注入验收测试证据。"""
        self._tests_cache = results

    def _requirements(self):
        """需求/验收项清单。没有就返回 None（bundle 会如实记录）。"""
        return getattr(self, "_requirements_cache", None)

    def set_requirements(self, requirements) -> None:
        self._requirements_cache = requirements


def build_report_hooks(cfg: dict | None = None) -> ReportHooks:
    """`load_capabilities` 约定的工厂（`build_<name>_hooks`）。"""
    cfg = cfg or {}
    return ReportHooks(
        workspace=str(cfg.get("workspace", ".") or "."),
        write=bool(cfg.get("write", True)),
    )


__all__ = ["BUNDLE_DIRNAME", "ReportHooks", "build_report_hooks"]
