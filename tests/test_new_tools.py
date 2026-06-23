# Tests for new tools: todo_write, code_exec, plan_update
import sys, pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from tools.todo_write import TODO_SPEC, todo_handler, PLAN_UPDATE_SPEC, plan_update_handler, get_current_todos
from tools.code_exec import CODE_EXEC_SPEC, code_exec_handler, _validate_python, _validate_js
from tools.base import ToolRegistry, tool_registry


class TestTodoWrite:
    @pytest.mark.asyncio
    async def test_create_todos(self):
        result = await todo_handler({
            "todos": [
                {"id": "1", "content": "Analyze requirements", "status": "in_progress", "priority": "high"},
                {"id": "2", "content": "Implement feature", "status": "pending", "priority": "medium"},
                {"id": "3", "content": "Write tests", "status": "pending", "priority": "medium"},
            ]
        }, workspace="/tmp/test")
        output = str(result)
        assert "Analyze requirements" in output
        assert "Implement feature" in output
        assert "0%" in output  # nothing completed yet, in_progress != completed

    @pytest.mark.asyncio
    async def test_complete_todos(self):
        await todo_handler({
            "todos": [
                {"id": "1", "content": "Done task", "status": "completed"},
                {"id": "2", "content": "Pending task", "status": "pending"},
            ]
        }, workspace="/tmp/test2")
        todos = get_current_todos("/tmp/test2")
        assert len(todos) == 2

    @pytest.mark.asyncio
    async def test_max_todos(self):
        many = [{"id": str(i), "content": f"Task {i}", "status": "pending"} for i in range(25)]
        result = await todo_handler({"todos": many}, workspace="/tmp/test3")
        todos = get_current_todos("/tmp/test3")
        assert len(todos) == 20

    def test_spec(self):
        assert TODO_SPEC.name == "todo_write"
        assert TODO_SPEC.category == "task_management"


class TestPlanUpdate:
    @pytest.mark.asyncio
    async def test_update_status(self):
        result = await plan_update_handler({"step_id": 1, "status": "completed", "notes": "Done"})
        output = str(result)
        assert "Step 1" in output
        assert "completed" in output

    @pytest.mark.asyncio
    async def test_with_new_steps(self):
        result = await plan_update_handler({
            "step_id": 2, "status": "in_progress",
            "new_steps": [{"description": "Add auth"}, {"description": "Add DB"}]
        })
        assert "2 new steps" in str(result)

    def test_spec(self):
        assert PLAN_UPDATE_SPEC.name == "plan_update"


class TestCodeExec:
    @pytest.mark.asyncio
    async def test_python_simple(self):
        result = await code_exec_handler({"language": "python", "code": "print(1 + 1)"})
        assert "2" in str(result)

    @pytest.mark.asyncio
    async def test_python_loop(self):
        result = await code_exec_handler({"language": "python", "code": "for i in range(3): print(i)"})
        assert "0" in str(result)

    @pytest.mark.asyncio
    async def test_python_error(self):
        result = await code_exec_handler({"language": "python", "code": "1/0"})
        assert "Error" in str(result)

    @pytest.mark.asyncio
    async def test_python_empty(self):
        result = await code_exec_handler({"language": "python", "code": ""})
        assert "(empty code snippet)" in str(result)

    @pytest.mark.asyncio
    async def test_python_safety_import(self):
        result = await code_exec_handler({"language": "python", "code": "import os\nprint(os.getcwd())"})
        assert "Blocked" in str(result) or "restricted" in str(result)

    @pytest.mark.asyncio
    async def test_python_safety_exec(self):
        result = await code_exec_handler({"language": "python", "code": "eval('1+1')"})
        assert "Blocked" in str(result) or "restricted" in str(result)

    def test_validate_python_safe(self):
        assert _validate_python("x = 1 + 1\nprint(x)") is None
        assert _validate_python("list(range(10))") is None

    def test_validate_python_blocked(self):
        assert _validate_python("import os\nos.system('ls')") is not None
        assert _validate_python("eval('1+1')") is not None

    def test_validate_js_blocked(self):
        assert _validate_js("require('child_process')") is not None

    def test_spec(self):
        assert CODE_EXEC_SPEC.name == "code_exec"
        assert CODE_EXEC_SPEC.category == "execution"


class TestToolRegistryNew:
    def test_new_tools_registered(self):
        tools = [t.name for t in tool_registry.list_tools()]
        assert "todo_write" in tools
        assert "plan_update" in tools
        assert "code_exec" in tools

    def test_tool_count(self):
        tools = tool_registry.list_tools()
        assert len(tools) >= 9  # 6 original + 3 new