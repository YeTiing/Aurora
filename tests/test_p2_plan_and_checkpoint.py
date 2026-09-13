"""计划状态机 + 真实检查点回滚。

这两个模块此前都是「报告成功但不产生实质效果」的假功能：

  P0-2  plan_update_handler 只拼接字符串返回，从未写 state.plan；且
        PlanStep.start() 全项目零调用，没有步骤会进入 in_progress，
        observer 永不标记 completed，主循环退出条件永不成立。
  P0-1  undo() 只移动栈指针不还原文件；save_workspace_state 的
        files_snapshot 直接等于 label 字符串，根本没存内容。
        而路由返回 undone=True —— 数据安全上的假象。
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ══ P0-2 计划状态机 ═══════════════════════════════════════════════

def _plan_steps():
    return [
        {"step": 1, "description": "第一步", "status": "pending",
         "tool": None, "estimated_turns": 1, "result": None},
        {"step": 2, "description": "第二步", "status": "pending",
         "tool": None, "estimated_turns": 1, "result": None},
    ]


@pytest.mark.asyncio
async def test_plan_update_writes_real_plan():
    """plan_update 必须真正写进计划，而不是只回一段文本。"""
    from backend.tools import plan_store
    from backend.tools.todo_write import plan_update_handler

    plan_store.set_plan("s-write", _plan_steps())
    out = await plan_update_handler({"step_id": 1, "status": "completed",
                                     "notes": "做完了", "session_id": "s-write"})

    stored = plan_store.get_plan("s-write")
    assert stored[0]["status"] == "completed", "状态没有真正落盘"
    assert stored[0]["result"] == "做完了"
    assert stored[1]["status"] == "pending", "不应影响其他步骤"
    assert "Step 1" in str(out) and "1/2" in str(out)


@pytest.mark.asyncio
async def test_plan_update_reports_failure_for_bad_step_id():
    """step_id 不存在时必须如实失败，不能伪造成功。"""
    from backend.tools import plan_store
    from backend.tools.todo_write import plan_update_handler

    plan_store.set_plan("s-bad", _plan_steps())
    result = await plan_update_handler({"step_id": 99, "status": "completed",
                                        "session_id": "s-bad"})

    assert result.success is False
    assert "not found" in (result.error or "")
    # 计划未被改动
    assert plan_store.get_plan("s-bad")[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_plan_update_inserts_new_steps():
    from backend.tools import plan_store
    from backend.tools.todo_write import plan_update_handler

    plan_store.set_plan("s-ins", _plan_steps())
    await plan_update_handler({
        "step_id": 1, "status": "completed",
        "new_steps": [{"description": "插入的步骤"}],
        "session_id": "s-ins",
    })

    stored = plan_store.get_plan("s-ins")
    assert len(stored) == 3
    descs = [s["description"] for s in stored]
    assert "插入的步骤" in descs
    # 新步骤插在原步骤之后
    assert descs.index("插入的步骤") == descs.index("第一步") + 1


@pytest.mark.asyncio
async def test_observer_completes_step_after_successful_tool():
    """observer 必须把有工具结果的步骤标记为 completed。

    回归点：此前只有 status == "in_progress" 才 complete，而没有代码设置
    in_progress，导致步骤永远停在 pending、主循环退不出来。
    """
    from backend.agent.nodes import observer_node
    from backend.agent.state import AgentState, PlanStep, ToolResult

    st = AgentState(session_id="s-obs")
    st.plan = [PlanStep(step=1, description="a"), PlanStep(step=2, description="b")]
    st.tool_results = [ToolResult(invocation_id="i", name="t", output="ok", success=True)]

    await observer_node(st)

    assert st.plan[0].status == "completed"
    assert st.plan[0].result == "ok"


@pytest.mark.asyncio
async def test_observer_marks_step_failed_on_tool_failure():
    """工具失败要标 failed，否则退出条件会虚报完成。"""
    from backend.agent.nodes import observer_node
    from backend.agent.state import AgentState, PlanStep, ToolResult

    st = AgentState(session_id="s-obs2")
    st.plan = [PlanStep(step=1, description="a")]
    st.tool_results = [ToolResult(invocation_id="i", name="t", output="",
                                 success=False, error="boom")]

    await observer_node(st)

    assert st.plan[0].status == "failed"
    assert "boom" in (st.plan[0].result or "")


@pytest.mark.asyncio
async def test_plan_reaches_terminal_state_so_loop_can_exit():
    """端到端：跑完所有步骤后退出条件必须成立。

    这是 P0-2 的核心影响 —— 修复前 all(...) 永不成立，只能靠 max_turns 兜底。
    """
    from backend.agent.nodes import observer_node
    from backend.agent.state import AgentState, PlanStep, ToolResult

    st = AgentState(session_id="s-exit")
    st.plan = [PlanStep(step=1, description="a"), PlanStep(step=2, description="b")]

    for i in range(2):
        st.tool_results.append(
            ToolResult(invocation_id=f"i{i}", name="t", output=f"ok{i}", success=True)
        )
        await observer_node(st)

    assert all(p.status in ("completed", "failed", "skipped") for p in st.plan), \
        "步骤未进入终态，主循环的退出条件不会成立"


def test_plan_store_sync_in_out_roundtrip():
    """AgentGraph 的 sync-out 应把工具改动合并回 state.plan（含新增步骤）。"""
    from backend.agent.graph import AgentGraph
    from backend.agent.state import AgentState, PlanStep
    from backend.tools import plan_store

    st = AgentState(session_id="s-merge")
    st.plan = [PlanStep(step=1, description="a"), PlanStep(step=2, description="b")]
    plan_store.set_plan("s-merge", [p.to_dict() for p in st.plan])

    # 模拟工具改动：完成 step1，并插入一个新步骤
    plan_store.update_step("s-merge", 1, "completed", notes="done",
                           new_steps=[{"description": "新插入"}])

    # 借用实例方法（不需要构造完整 graph）
    graph = AgentGraph.__new__(AgentGraph)
    graph._sync_plan_out(st)

    assert st.plan[0].status == "completed"
    assert st.plan[0].result == "done"
    assert any(p.description == "新插入" for p in st.plan), "新增步骤未合并进来"


# ══ P0-1 检查点真实回滚 ═══════════════════════════════════════════

def test_checkpoint_undo_actually_restores_file(tmp_path):
    """undo 必须真的把文件内容写回，而不只是移动栈指针。"""
    from backend.agent.checkpoint import CheckpointManager

    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "a.py"
    target.write_text("原始内容", encoding="utf-8")

    mgr = CheckpointManager(storage_dir=str(tmp_path / "ck"))
    mgr.save_workspace_state(label="t", paths=["a.py"], workspace=str(ws))

    target.write_text("被改坏", encoding="utf-8")
    mgr.undo()

    assert target.read_text(encoding="utf-8") == "原始内容", "文件未被还原"
    assert mgr.last_restore()["restored"] == 1


def test_checkpoint_undo_deletes_newly_created_file(tmp_path):
    """快照时不存在、之后新建的文件，回滚时应被删除。"""
    from backend.agent.checkpoint import CheckpointManager

    ws = tmp_path / "ws"
    ws.mkdir()

    mgr = CheckpointManager(storage_dir=str(tmp_path / "ck"))
    mgr.save_workspace_state(label="t", paths=["created.py"], workspace=str(ws))

    created = ws / "created.py"
    created.write_text("agent 新建的", encoding="utf-8")
    assert created.exists()

    mgr.undo()

    assert not created.exists(), "新建的文件未被删除"
    assert mgr.last_restore()["removed"] == 1


def test_checkpoint_redo_restores_pre_undo_state(tmp_path):
    """redo 应把 undo 撤掉的改动重新做回来。"""
    from backend.agent.checkpoint import CheckpointManager

    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "a.py"
    f.write_text("v1", encoding="utf-8")

    mgr = CheckpointManager(storage_dir=str(tmp_path / "ck"))
    mgr.save_workspace_state(label="t", paths=["a.py"], workspace=str(ws))

    f.write_text("v2", encoding="utf-8")
    mgr.undo()
    assert f.read_text(encoding="utf-8") == "v1"

    mgr.redo()
    assert f.read_text(encoding="utf-8") == "v2", "redo 未恢复撤销前的状态"


def test_checkpoint_undo_without_snapshot_reports_nothing_restored(tmp_path):
    """无文件内容的旧检查点：不能谎报已回滚。"""
    from backend.agent.checkpoint import CheckpointManager

    mgr = CheckpointManager(storage_dir=str(tmp_path / "ck"))
    # 直接构造一个只含 label 的旧式快照，模拟历史数据
    cid = "ws_legacy"
    mgr._workspace_states[cid] = {"label": "legacy", "timestamp": 0}
    mgr._undo_stack.append(cid)

    got = mgr.undo()

    assert got == cid
    assert mgr.last_restore() is None, "没有文件快照时必须报告未还原任何内容"


def test_candidate_paths_parses_apply_patch_diff():
    """apply_patch 的目标路径藏在 diff 里，必须能解析出来。"""
    from backend.agent.graph import AgentGraph
    from backend.agent.state import ToolInvocation

    patch = (
        "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+x\n"
        "--- a/existing.py\n+++ b/existing.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    inv = ToolInvocation(id="i", name="apply_patch", arguments={"patch": patch})
    paths = AgentGraph._candidate_paths(inv)

    assert "new.py" in paths
    assert "existing.py" in paths


def test_candidate_paths_reads_file_rw_arguments():
    from backend.agent.graph import AgentGraph
    from backend.agent.state import ToolInvocation

    inv = ToolInvocation(id="i", name="file_rw",
                         arguments={"operation": "write", "path": "x.py"})
    assert AgentGraph._candidate_paths(inv) == ["x.py"]

    inv2 = ToolInvocation(id="i2", name="file_rw",
                          arguments={"operation": "move", "path": "a.py", "destination": "b.py"})
    assert set(AgentGraph._candidate_paths(inv2)) == {"a.py", "b.py"}


def test_checkpoint_after_process_restart_uses_disk_snapshot(tmp_path):
    """内存态丢失（进程重启）后，仍能从磁盘快照还原。"""
    from backend.agent.checkpoint import CheckpointManager

    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "a.py"
    f.write_text("磁盘上的原始内容", encoding="utf-8")

    ck_dir = tmp_path / "ck"
    mgr1 = CheckpointManager(storage_dir=str(ck_dir))
    cid = mgr1.save_workspace_state(label="t", paths=["a.py"], workspace=str(ws))

    # 模拟重启：新实例，内存态为空
    f.write_text("被改坏", encoding="utf-8")
    mgr2 = CheckpointManager(storage_dir=str(ck_dir))
    mgr2._undo_stack.append(cid)
    mgr2._workspace_states.clear()

    mgr2.undo()

    assert f.read_text(encoding="utf-8") == "磁盘上的原始内容"
