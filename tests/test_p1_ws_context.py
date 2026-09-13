"""B7 / Q12 回归测试。

B7：RAG + skills 上下文注入此前只存在于 REST 端点，桌面端实际走的
    /ws/desktop（Electron ipcMain "agent:chat"）完全没有注入，导致产品形态下
    技能与 RAG 永不生效。这里同时锁定 WS 两条链路与 REST 旧格式。
Q12：get_scanner 只缓存第一个 workspace，后续调用者被静默忽略、扫描错目录。
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

from backend.agent.state import AgentState
from backend.api import deps
from backend.api import app as fastapi_app
from backend.api.models import ChatRequest
from backend.api.routes import chat as chat_routes


class CaptureGraph:
    """记录 graph.run 收到的 user_input（即注入后的完整提示词）。"""

    def __init__(self):
        self.inputs = []

    async def run(self, user_input, session_id="", workspace=".", **kwargs):
        self.inputs.append(user_input)
        state = AgentState(session_id=session_id, workspace=workspace)
        state.final_response = "ok"
        return state


def _install_fake_deps(monkeypatch, *, skills_ctx="", rag_ctx="", rag_count=1):
    """按 test_backend_closure.py 的方式直接替换 deps 缓存。"""
    calls = {"skills_match": [], "rag_search": 0}

    class FakeSkills:
        def match(self, message):
            calls["skills_match"].append(message)
            return ["skill"] if skills_ctx else []

        def inject(self, triggered):
            return skills_ctx

    class FakeVectorStore:
        def count(self):
            return rag_count

    class FakeRag:
        vector_store = FakeVectorStore()

        def search(self, message, top_k=5, llm_client=None):
            calls["rag_search"] += 1
            return [{"content": "rag", "metadata": {}}]

        def format_context(self, chunks):
            return rag_ctx

    graph = CaptureGraph()
    # 必须同时设置 _skills/_graph/_rag：_build_full_prompt 读 _deps._skills/_rag，
    # WS 分支读 _deps._graph，而 ensure_all() 依赖它们非 None 才不会真正构造。
    monkeypatch.setattr(deps, "_graph", graph)
    monkeypatch.setattr(deps, "_skills", FakeSkills())
    monkeypatch.setattr(deps, "_rag", FakeRag())
    monkeypatch.setattr(deps, "_llm", object())
    return graph, calls


@pytest.fixture
def client():
    with TestClient(fastapi_app) as c:
        yield c


# ── B7: WS 注入 ──────────────────────────────────────────────

def test_ws_desktop_injects_skills_and_rag(monkeypatch, client):
    graph, calls = _install_fake_deps(
        monkeypatch, skills_ctx="SKILL_CTX\n", rag_ctx="RAG_CTX\n", rag_count=1
    )

    with client.websocket_connect("/ws/desktop") as ws:
        ws.send_json({"type": "chat", "message": "hello", "sessionId": "s-desk"})
        done = None
        for _ in range(3):  # user_message / agent_message / done
            payload = ws.receive_json()
            if payload.get("type") == "done":
                done = payload
                break

    assert done is not None and done["response"] == "ok"
    assert graph.inputs == ["SKILL_CTX\nRAG_CTX\nUser: hello"]
    assert calls["rag_search"] == 1


def test_ws_session_injects_skills_and_rag(monkeypatch, client):
    graph, calls = _install_fake_deps(
        monkeypatch, skills_ctx="SKILL_CTX\n", rag_ctx="RAG_CTX\n", rag_count=1
    )

    with client.websocket_connect("/ws/s-session") as ws:
        ws.send_json({"type": "chat", "message": "hello"})
        done = None
        for _ in range(3):
            payload = ws.receive_json()
            if payload.get("type") == "done":
                done = payload
                break

    assert done is not None and done["response"] == "ok"
    assert graph.inputs == ["SKILL_CTX\nRAG_CTX\nUser: hello"]


def test_ws_skips_rag_when_vector_store_empty(monkeypatch, client):
    """B7-neg：count()==0 时不得检索、不得注入 RAG（沿用原有守卫）。"""
    graph, calls = _install_fake_deps(
        monkeypatch, skills_ctx="", rag_ctx="RAG_CTX\n", rag_count=0
    )

    with client.websocket_connect("/ws/desktop") as ws:
        ws.send_json({"type": "chat", "message": "hello", "sessionId": "s-empty"})
        for _ in range(3):
            if ws.receive_json().get("type") == "done":
                break

    assert graph.inputs == ["hello"]
    assert calls["rag_search"] == 0


# ── B7: REST 旧格式不变 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_rest_chat_format_unchanged(monkeypatch):
    graph, _ = _install_fake_deps(
        monkeypatch, skills_ctx="SKILL_CTX\n", rag_ctx="RAG_CTX\n", rag_count=1
    )

    resp = await chat_routes.chat(ChatRequest(message="hello", session_id="s-rest"))

    assert resp.response == "ok"
    assert graph.inputs == ["SKILL_CTX\nRAG_CTX\nUser: hello"]


@pytest.mark.asyncio
async def test_rest_chat_no_context_returns_bare_message(monkeypatch):
    graph, _ = _install_fake_deps(monkeypatch, skills_ctx="", rag_ctx="", rag_count=0)

    await chat_routes.chat(ChatRequest(message="hello", session_id="s-rest2"))

    assert graph.inputs == ["hello"]


@pytest.mark.asyncio
async def test_rest_chat_stream_format_unchanged(monkeypatch):
    graph, _ = _install_fake_deps(
        monkeypatch, skills_ctx="SKILL_CTX\n", rag_ctx="RAG_CTX\n", rag_count=1
    )

    async def run_with_stream(user_input, **kwargs):
        graph.inputs.append(user_input)
        yield {"type": "done", "response": "ok"}

    monkeypatch.setattr(graph, "run_with_stream", run_with_stream, raising=False)

    stream = await chat_routes.chat_stream(ChatRequest(message="hello", session_id="s-stream"))
    async for _ in stream.body_iterator:
        pass

    assert graph.inputs == ["SKILL_CTX\nRAG_CTX\nUser: hello"]


# ── Q12: scanner 按 workspace 缓存 ─────────────────────────────

def test_scanner_per_workspace_and_reuse(tmp_path):
    from backend.security_scanner import get_scanner, reset_scanners

    reset_scanners()
    ws1 = tmp_path / "one"; ws1.mkdir()
    ws2 = tmp_path / "two"; ws2.mkdir()

    s1 = get_scanner(str(ws1))
    s2 = get_scanner(str(ws2))

    assert s1 is not s2
    assert s1.workspace == ws1.resolve()
    assert s2.workspace == ws2.resolve()
    # 同一 workspace 重复调用必须返回同一对象（热路径不允许重复构造）
    assert get_scanner(str(ws1)) is s1
    reset_scanners()


def test_scanner_default_workspace(tmp_path, monkeypatch):
    from backend.security_scanner import get_scanner, reset_scanners

    reset_scanners()
    monkeypatch.chdir(tmp_path)
    assert get_scanner().workspace == tmp_path.resolve()
    reset_scanners()


@pytest.mark.skipif(sys.platform != "win32", reason="大小写不敏感路径等价仅在 Windows 上成立")
def test_scanner_windows_case_insensitive_equivalence(tmp_path):
    from backend.security_scanner import get_scanner, reset_scanners

    reset_scanners()
    ws = tmp_path / "CaseDir"; ws.mkdir()

    s1 = get_scanner(str(ws))
    # 换大小写 + 换分隔符，语义上仍是同一目录，不得产生第二个实例
    variant = str(ws).upper().replace("\\", "/")
    s2 = get_scanner(variant)

    assert s1 is s2
    reset_scanners()


def test_scanner_cache_is_bounded(tmp_path):
    from backend import security_scanner as mod

    mod.reset_scanners()
    for i in range(mod._SCANNER_CACHE_MAX + 4):
        d = tmp_path / f"ws{i}"; d.mkdir()
        mod.get_scanner(str(d))

    assert len(mod._scanners) <= mod._SCANNER_CACHE_MAX
    mod.reset_scanners()
