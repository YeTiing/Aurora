"""necessity CLI —— 离线命令行入口（路由层）。

INTEGRATION.md §1.3 把 CLI 定位为「离线工具」层：只承载**不依赖宿主循环**
的能力。因此这里的命令面被刻意收窄：

    necessity index build/callers/stats/impact    结构索引（LSP）
    necessity reduce analyze <diff>               能力 3：Diff Reducer（离线）
    necessity attribution report <trace-db>       能力 4：Failure Attribution（离线）
    necessity eval gate0 / summary               评测（Gate 0 / 汇总）

⚠️ **Guard 故意没有 CLI**（INTEGRATION.md §1.3）：
    Constraint Guard 必须拦截宿主循环里的**每一次工具调用**并扫描工作区，
    才能在越界发生前阻断。MCP/CLI 协议做不到「拦截别人的调用」——
    它们只能被调用，不能旁观。所以 Guard 只有 `adapter/` 一条集成路径，
    任何 CLI 子命令都是假的实现。此约束由 tests/test_cli.py 守护。

退出码约定（与既有 index 命令一致，全部命令共用）：
    0  成功（对归因/最小化而言 = 有结论且收敛）
    1  成功执行但结果为「否定/不完整」（未收敛、unknown、无冗余等）
    2  用法错误（参数矛盾、未知子命令、缺输入）
    3  缺依赖（pyright / pytest / git 等未安装）—— 给出安装指引而非 traceback
    4  存储错误（库缺失、schema 不匹配、SQLite 报错）
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ⚠️ **模块别名**：把 `cli.*` 与 `backend.necessity.cli.*` 指到同一批模块对象。
#
# 为什么必需：上面把 `backend/necessity` 加进 sys.path，于是同一份文件
# 能通过两条路径导入 —— `cli.reduce_cmd` 和 `backend.necessity.cli.reduce_cmd`
# 会成为**两个不同的模块对象**。
#
# 后果不是报错，是**测试的 monkeypatch 改不到真正在跑的代码**：
#     monkeypatch.setattr("cli.reduce_cmd.INJECTED_RUNNER", ...)
# 改的是 `cli.*` 那个副本，而 `main.py` 用完整包路径加载的是另一个副本 ——
# 注入的判定器根本没生效，于是走了真实沙箱路径并触发前提校验而失败。
# 实测：这条别名缺失导致 5 个 reduce CLI 测试回归。
#
# 修法是让两条路径指向同一对象，而不是强迫所有调用方改用一种写法 ——
# 后者会破坏既有测试与外部集成方。
import backend.necessity.cli as _cli_pkg  # noqa: E402

sys.modules.setdefault("cli", _cli_pkg)

DEFAULT_DB = str(ROOT / ".necessity" / "index.db")


def _open_store(db_path: str):
    from backend.necessity.index.store import Store

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return Store(db_path)


# ── 共用输出辅助 ─────────────────────────────────────────────────

def _fail(msg: str, code: int, hint: str = "") -> int:
    print(msg, file=sys.stderr)
    if hint:
        print(hint, file=sys.stderr)
    return code


# ── index 命令（既有，保持原样）──────────────────────────────────

def cmd_build(args) -> int:
    """建图：遍历仓库 → 采符号 → 查关系 → 写 SQLite。"""
    from backend.necessity.index.builder import build
    # LSP 传输层统一用 Aurora 的 backend/lsp（合并时删掉了 Necessity
    # 自带的那份 1273 行实现）。Aurora 版已修好 rootUri/workspaceFolders、
    # 补了 callHierarchy 与 documentSymbol。
    from backend.lsp.server_manager import LSPServerManager

    repo = str(Path(args.repo).resolve())
    store = _open_store(args.db)

    async def _run():
        manager = LSPServerManager()
        # initialize 是 async（它会 await 子进程启动 + LSP 握手）
        await manager.initialize(root_path=repo)
        try:
            return await build(manager, store, repo, concurrency=args.concurrency)
        finally:
            try:
                await manager.shutdown()
            except Exception:
                pass

    try:
        import asyncio
        stats = asyncio.run(_run())
    except Exception as e:
        print(f"build 失败: {type(e).__name__}: {e}", file=sys.stderr)
        print()
        # 「缺 pyright」是最常见原因，必须给出可操作的安装指引而不是 traceback
        print("最常见原因：pyright 未安装。安装方式：")
        print("  npm i -g pyright    或    pip install pyright")
        return 3

    import json
    print(json.dumps(stats.to_dict(), ensure_ascii=False, indent=2))
    if stats.files_failed:
        print(f"\n警告：{stats.files_failed} 个文件解析失败（已跳过，未中断）", file=sys.stderr)
    return 0 if stats.files_indexed else 1


def cmd_callers(args) -> int:
    """查调用方。INDEX.md 的 `callers <file>:<line>:<col>`。"""
    spec = args.symbol
    # 支持两种形式：`path:line:col` 或 `path::qualified_name`
    if "::" in spec:
        store = _open_store(args.db)
        rows = store.callers(spec)
        _print_callers(spec, rows)
        return 0 if rows else 1

    parts = spec.rsplit(":", 2)
    if len(parts) != 3:
        print("用法: necessity index callers <file>:<line>:<col>", file=sys.stderr)
        print("  或: necessity index callers <path>::<qualified_name>", file=sys.stderr)
        return 2
    file_part, line_s, col_s = parts
    try:
        line, col = int(line_s), int(col_s)
    except ValueError:
        print(f"行/列必须是整数: {line_s!r}:{col_s!r}", file=sys.stderr)
        return 2

    store = _open_store(args.db)
    from backend.necessity.index.lookup import find_symbol_at
    sym = find_symbol_at(store, args.workspace, file_part, line - 1, col - 1)
    if not sym:
        print(f"在该位置找不到符号: {file_part}:{line}:{col}", file=sys.stderr)
        print("提示：位置需要指向**符号名**（不是 def 关键字行）。")
        return 1
    rows = store.callers(sym["id"])
    _print_callers(sym["id"], rows)
    return 0 if rows else 1


def _print_callers(symbol_id: str, rows: list[dict]) -> None:
    print(f"符号: {symbol_id}")
    print(f"调用方: {len(rows)}")
    for r in rows:
        print(f"  {r.get('file')}:{int(r.get('line') or 0) + 1}  <- {r.get('src_id')}")


def cmd_stats(args) -> int:
    store = _open_store(args.db)
    import json
    print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
    return 0


def cmd_impact(args) -> int:
    """影响面查询 —— Phase 2 的接口先在 CLI 里可用（便于独立验证）。"""
    from backend.necessity.index.impact import analyze

    store = _open_store(args.db)
    imp = analyze(store, args.symbol, depth=args.depth)
    import json
    print(json.dumps(imp.to_dict(), ensure_ascii=False, indent=2))
    return 0


# ── 参数树 ───────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    """构造完整参数树。

    单独成函数是为了让测试能**内省**命令面 —— 尤其是断言 Guard 不存在
    （见模块 docstring 的硬约束），而不是靠跑起来猜。
    """
    p = argparse.ArgumentParser(prog="necessity", description="Necessity 离线命令行")
    top = p.add_subparsers(dest="group", required=True)

    # ── index（既有）────────────────────────────────────────────
    idx = top.add_parser("index", help="CodeGraph 结构索引（基于 LSP）")
    isub = idx.add_subparsers(dest="cmd", required=True)

    b = isub.add_parser("build", help="建图")
    b.add_argument("repo", help="仓库路径")
    b.add_argument("--db", default=DEFAULT_DB)
    b.add_argument("--concurrency", type=int, default=8)
    b.set_defaults(fn=cmd_build)

    c = isub.add_parser("callers", help="查某符号的调用方")
    c.add_argument("symbol", help="<file>:<line>:<col> 或 <path>::<qualified_name>")
    c.add_argument("--db", default=DEFAULT_DB)
    c.add_argument("--workspace", default=".", help="仓库根（用于解析相对路径）")
    c.set_defaults(fn=cmd_callers)

    s = isub.add_parser("stats", help="图统计")
    s.add_argument("--db", default=DEFAULT_DB)
    s.set_defaults(fn=cmd_stats)

    i = isub.add_parser("impact", help="影响面分析")
    i.add_argument("symbol", help="<path>::<qualified_name>")
    i.add_argument("--db", default=DEFAULT_DB)
    i.add_argument("--depth", type=int, default=2)
    i.set_defaults(fn=cmd_impact)

    # ── reduce / attribution / eval / compete（实现在同包子模块）──
    # ⚠️ 用**完整包路径**而非 `from cli.X`：后者依赖调用方把
    # `backend/necessity` 放进 sys.path（本模块顶部恰好这么做了），
    # 换个入口（如从仓库根 `python -m backend.necessity.cli.main`）就会断。
    # 完整路径在任何入口下都成立。
    from backend.necessity.cli.attribution_cmd import add_parser as add_attribution
    from backend.necessity.cli.compete_cmd import add_parser as add_compete
    from backend.necessity.cli.eval_cmd import add_parser as add_eval
    from backend.necessity.cli.reduce_cmd import add_parser as add_reduce

    add_reduce(top)
    add_attribution(top)
    add_eval(top)
    add_compete(top)
    # 注意：这里**不注册** guard —— 理由见模块 docstring（§1.3）。

    return p


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        # argparse 用法错误会 SystemExit(2)、--help 会 SystemExit(0)。
        # 转成返回值让 main() 对调用方始终是「返回 int」，便于直接测试；
        # 同时保留 2 = 用法错误的语义。
        return int(e.code or 0)

    try:
        return args.fn(args)
    except sqlite3.Error as e:
        print(f"数据库错误: {e}", file=sys.stderr)
        print("提示：若库不存在，先执行 `necessity index build <repo>`", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
