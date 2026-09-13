"""Constraint Guard 测试（第二部分）—— 预检/后检、回滚安全、指标 ρ/s、降级。

对应任务书：后检抓预检漏掉的、非 Agent 变更排除、回滚四条安全约束、
ρ 与 s 同时上报、无 store/无约束全部放行不崩。全部离线。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.necessity.guard import build_guard_hooks  # noqa: E402
from backend.necessity.guard.workspace import BULK_DIFF_THRESHOLD, WorkspaceScanner  # noqa: E402
from backend.necessity.hooks import ToolCall, ToolResult  # noqa: E402

# ── 3. 预检 / 后检 ──────────────────────────────────────────────

@pytest.fixture()
def ws(tmp_path):
    (tmp_path / "src" / "parser").mkdir(parents=True)
    (tmp_path / "src" / "utils").mkdir(parents=True)
    (tmp_path / "src" / "parser" / "p.py").write_text("def parse(): pass\n")
    (tmp_path / "src" / "utils" / "u.py").write_text("def helper(): pass\n")
    return tmp_path


def make_guard(ws, constraints, **cfg):
    g = build_guard_hooks({"workspace": str(ws), "use_git": False,
                           "session_id": "s1", **cfg})
    g.on_task_start({"id": "task-1", "constraints": constraints})
    return g


def test_precheck_blocks_clear_file_scope_violation(ws):
    g = make_guard(ws, ["只允许修改 src/parser/**"], override_action="block")
    d = g.before_tool(ToolCall("file_write", {"path": "src/utils/u.py"}, 1))
    assert d.action == "block"
    assert d.reason                      # Decision 契约：block 必带 reason
    assert "c1" in d.reason              # reason 点名约束 id（§7.2 格式）
    assert "file_scope" in d.reason


def test_precheck_allows_in_scope_and_ambiguous_cases(ws):
    g = make_guard(ws, ["只允许修改 src/parser/**"], override_action="block")
    assert g.before_tool(ToolCall("file_write", {"path": "src/parser/p.py"}, 1)).action == "allow"
    # 含糊：命令提交给脚本，意图看不出会改什么 → 必须放行（保守）
    assert g.before_tool(ToolCall("shell_command", {"command": "python fix.py"}, 1)).action == "allow"


def test_precheck_detects_shell_redirect(ws):
    g = make_guard(ws, ["只允许修改 src/parser/**"], override_action="block")
    d = g.before_tool(ToolCall("shell_command",
                               {"command": "echo x > src/utils/u.py"}, 1))
    assert d.action == "block" and "src/utils/u.py" in d.reason


def test_postcheck_catches_what_precheck_missed(ws):
    """核心场景（§5.2）：shell 调脚本 → 预检无从判断 → 后检抓到。"""
    g = make_guard(ws, ["只允许修改 src/parser/**"], default_action="rollback")
    # 预检放行含糊调用
    assert g.before_tool(ToolCall("shell_command", {"command": "python fix.py"}, 1)).action == "allow"
    # 脚本实际越界改了 utils
    (ws / "src" / "utils" / "u.py").write_text("def helper():\n    return 1\n")
    g.on_turn_end(1)
    assert len(g.violations) == 1
    assert g.violations[0].constraint_id == "c1"
    assert g.violations[0].paths == ["src/utils/u.py"]


def test_postcheck_is_tool_independent(ws):
    """文件直接落盘（不经任何已知工具）同样被抓 —— 与工具无关。"""
    g = make_guard(ws, ["只允许修改 src/parser/**"], default_action="warn")
    (ws / "src" / "utils" / "u.py").write_text("changed\n")
    g.after_tool(ToolCall("mcp_tool", {"whatever": 1}, 1), ToolResult(ok=True))
    assert len(g.violations) == 1


def test_non_agent_changes_excluded(ws):
    """writer='other' 的变更不得被报为违反（§6.3）。"""
    g = make_guard(ws, ["只允许修改 src/parser/**"], default_action="rollback")
    g.after_write(str(ws / "src" / "utils" / "u.py"), "other")
    (ws / "src" / "utils" / "u.py").write_text("git did this\n")
    g.on_turn_end(1)
    assert g.violations == []
    assert g.rollback_actions == []


def test_git_head_change_marks_all_external(ws):
    """git HEAD 变化 → 整批归为外部，不出违反（模拟 checkout）。"""
    scanner = WorkspaceScanner(str(ws), use_git=False)
    before = scanner.scan()
    after = scanner.scan()
    before.git_head, after.git_head = "aaaa1111", "bbbb2222"
    (ws / "src" / "utils" / "u.py").write_text("checked out\n")
    after = scanner.scan()
    after.git_head = "bbbb2222"
    changes, info = scanner.diff(before, after)
    assert info["external"] is True
    assert changes and all(not c.by_agent for c in changes)


def test_scan_excludes_necessity_and_skip_dirs(ws, tmp_path):
    g = make_guard(ws, ["只允许修改 src/parser/**"], default_action="warn")
    (ws / ".necessity" / "backup").mkdir(parents=True, exist_ok=True)
    (ws / ".necessity" / "backup" / "self.py").write_text("guard wrote me\n")
    (ws / "__pycache__").mkdir(exist_ok=True)
    (ws / "__pycache__" / "x.pyc").write_text("bin")
    (ws / "src" / "parser" / "ok.py").write_text("in scope\n")
    g.on_turn_end(1)
    assert g.violations == [], "Guard 自身产物与 SKIP_DIRS 必须被排除，否则自我触发"


def test_bulk_diff_threshold_constant_present(ws):
    assert BULK_DIFF_THRESHOLD == 1000


# ── 4. 回滚安全 ─────────────────────────────────────────────────

def test_rollback_restores_content_and_backs_up(ws):
    g = make_guard(ws, ["只允许修改 src/parser/**"], default_action="rollback")
    target = ws / "src" / "utils" / "u.py"
    g.before_tool(ToolCall("file_write", {"path": "src/utils/u.py"}, 1))  # 记基线
    target.write_text("def helper():\n    return 1\n")
    g.on_turn_end(1)
    actions = g.rollback_actions
    assert len(actions) == 1 and actions[0]["status"] == "restored"
    assert target.read_text() == "def helper(): pass\n"    # 内容恢复
    assert Path(actions[0]["backup"]).exists()             # 先备份
    assert Path(actions[0]["backup"]).read_text() == "def helper():\n    return 1\n"


def test_rollback_does_not_delete_user_file(ws):
    """回滚是恢复内容，不是删除 —— 用户原有文件必须还在。"""
    g = make_guard(ws, ["只允许修改 src/parser/**"], default_action="rollback")
    target = ws / "src" / "utils" / "u.py"
    g.before_tool(ToolCall("file_write", {"path": "src/utils/u.py"}, 1))
    target.write_text("modified\n")
    g.on_turn_end(1)
    assert target.exists()          # 没被删
    assert target.read_text() == "def helper(): pass\n"


def test_rollback_new_file_restores_to_absent(ws):
    g = make_guard(ws, ["只允许修改 src/parser/**"], default_action="rollback")
    new = ws / "src" / "utils" / "brand_new.py"
    g.before_tool(ToolCall("file_write", {"path": "src/utils/brand_new.py"}, 1))
    new.write_text("agent created me\n")
    g.on_turn_end(1)
    assert not new.exists()
    assert g.rollback_actions[0]["status"] == "restored_absent"


def test_rollback_refuses_path_outside_workspace(ws):
    """越界路径必须拒绝 —— 且不能因 startswith 而放行同名前缀目录。"""
    g = make_guard(ws, ["只允许修改 src/parser/**"], default_action="rollback")
    outside = ws.parent / (ws.name + "-evil") / "x.py"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("outside\n")
    g.rollback.note_writer(str(outside), "agent")   # 即使标记为 Agent 写入
    res = g.rollback._one(str(outside), "c1", lambda p: None)
    assert res["status"] == "refused" and res["escalate"] is True
    assert outside.read_text() == "outside\n"     # 未被动过


def test_rollback_skips_file_not_in_write_log(ws):
    """只回滚本任务 Agent 写过的文件（§7.1 第 1 条）。"""
    g = make_guard(ws, ["只允许修改 src/parser/**"], default_action="rollback")
    res = g.rollback._one("src/utils/u.py", "c1", lambda p: "orig\n")
    assert res["status"] == "skipped"


def test_rollback_without_baseline_escalates_not_silent(ws):
    """有写入日志但无基线 → 告警转人工，绝不静默失败。"""
    g = make_guard(ws, ["只允许修改 src/parser/**"], default_action="rollback")
    g.rollback.note_writer("src/utils/u.py", "agent")
    res = g.rollback._one("src/utils/u.py", "c1", lambda p: None)
    assert res["status"] == "escalated" and res["escalate"] is True
    assert res["reason"]


def test_warn_mode_does_not_touch_files(ws):
    g = make_guard(ws, ["只允许修改 src/parser/**"], default_action="warn")
    target = ws / "src" / "utils" / "u.py"
    target.write_text("changed\n")
    g.on_turn_end(1)
    assert len(g.violations) == 1
    assert g.rollback_actions == []
    assert target.read_text() == "changed\n"
