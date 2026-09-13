"""模块规模合规检查 —— 替代「靠人记得 300 行上限」。

为什么需要脚本：项目硬性要求单文件 ≤300 行，但手工检查会漏
（本轮就漏了 probe.py 直到提交时才被 grep 发现）。把它变成可执行的检查。

豁免规则写在代码里而不是靠记忆：
  probe/probe.py 是一次性探明脚本（INDEX.md §Phase 0 要求如此），
  不是产品模块，故豁免 —— 豁免理由已写在其文件头。
"""
from __future__ import annotations

import sys
from pathlib import Path

# Windows 控制台默认 GBK，中文/符号会崩 —— 统一转 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

MAX_LINES = 300

# 明确豁免的文件（相对 backend/necessity/ 的路径）
EXEMPT = {
    "probe/probe.py",   # Phase 0 一次性探明脚本，见其文件头说明
}


def main(root: str = "") -> int:
    base = Path(root) if root else Path(__file__).resolve().parent
    bad = []
    for f in base.rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        rel = f.relative_to(base).as_posix()
        if rel in EXEMPT:
            continue
        n = len(f.read_text(encoding="utf-8", errors="replace").splitlines())
        if n > MAX_LINES:
            bad.append((rel, n))

    if bad:
        print(f"✗ {len(bad)} 个文件超过 {MAX_LINES} 行：")
        for rel, n in sorted(bad, key=lambda x: -x[1]):
            print(f"    {n:5d}  {rel}")
        return 1
    print(f"✓ 全部文件 ≤{MAX_LINES} 行（豁免 {len(EXEMPT)} 个）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
