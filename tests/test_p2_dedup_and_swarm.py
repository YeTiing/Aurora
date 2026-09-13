"""P2 收尾：去重、死代码接线、swarm 后端补齐。

覆盖清单条目：
  Q6  两个 TokenCounter / graph.py 内联 URL 探测重复
  B13 truncate_tool_output 是死代码（被 import 但从未调用）
  Q10 TMUX 枚举声明了却无实现无注册；REMOTE 声明了却完全不存在
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Q6: TokenCounter 去重 ─────────────────────────────────────────

def test_llm_client_tokencounter_delegates_to_canonical():
    """llm_client 的 TokenCounter 必须转发到 context 的唯一实现。

    此前是两份独立实现（两套 MODEL_ENCODING_MAP、两套统计口径），
    且只有 context 那份有离线兜底。
    """
    from backend.agent.llm_client import TokenCounter as Forwarder
    from backend.context.token_counter import TokenCounter as Canonical

    text = "def foo(): return 42"
    assert Forwarder.count(text, "gpt-4o") == Canonical("gpt-4o").count(text)

    msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    assert Forwarder.count_messages(msgs, "gpt-4o") == Canonical("gpt-4o").count_messages(msgs)


def test_tokencounter_encoding_map_is_shared():
    """两处的编码映射必须是同一份数据，不能各自维护。"""
    from backend.agent.llm_client import TokenCounter as Forwarder
    from backend.context.token_counter import MODEL_ENCODINGS

    assert Forwarder.MODEL_ENCODING_MAP == MODEL_ENCODINGS


def test_llm_client_count_tokens_still_works():
    """替换实现后，LLMClient 的公开计数入口不能坏。"""
    from backend.agent.llm_client import MockLLMClient

    llm = MockLLMClient()
    assert llm.count_tokens("hello world") > 0
    assert llm.count_tokens([{"role": "user", "content": "hello"}]) > 0


def test_llm_client_tokencounter_offline_safe():
    """转发后必须继承离线兜底能力（这是两份实现合并的主要收益之一）。"""
    from backend.agent.llm_client import TokenCounter

    # 不依赖网络：无论 tiktoken 是否可用，都必须返回一个合理正数
    n = TokenCounter.count("a" * 400, "gpt-4o")
    assert n > 0


# ── B13: truncate_tool_output 接线 ────────────────────────────────

def test_truncate_tool_output_keeps_head_and_tail():
    from backend.agent.nodes import truncate_tool_output

    out = truncate_tool_output("START" + "x" * 50000 + "END", max_len=8000)
    assert "START" in out, "头部信息必须保留（这是该函数相对硬切的价值）"
    assert "END" in out, "尾部信息必须保留"
    assert "truncated" in out
    assert len(out) < 20000, "结果不应接近原文长度"


def test_truncate_tool_output_passthrough_when_short():
    from backend.agent.nodes import truncate_tool_output

    short = "short output"
    assert truncate_tool_output(short, max_len=8000) == short


@pytest.mark.asyncio
async def test_executor_applies_truncation_to_large_tool_output():
    """executor 必须对超长工具输出走统一截断，而不是裸切。

    回归点：此前 truncate_tool_output 被 import 却从未调用，
    实际生效的是 output[:8000] 硬切，头尾信息全部丢失。
    """
    from backend.agent.nodes import executor_node
    from backend.agent.state import AgentState, ToolInvocation

    big = "HEAD_MARKER" + "z" * 30000 + "TAIL_MARKER"

    async def handler(name, args, ws):
        return {"success": True, "output": big, "error": None}

    st = AgentState()
    st.tool_invocations.append(ToolInvocation(id="i1", name="code_search", arguments={}))
    await executor_node(st, handler, ".")

    got = st.tool_results[0].output
    assert "HEAD_MARKER" in got
    assert "TAIL_MARKER" in got
    assert st.tool_results[0].truncated is True


# ── Q6: graph.py URL 探测去重 ─────────────────────────────────────

def test_run_with_stream_reuses_detect_url():
    """run_with_stream 不应再内联重写 URL 探测正则。"""
    import inspect
    from backend.agent import graph as graph_mod

    src = inspect.getsource(graph_mod.AgentGraph.run_with_stream)
    assert "self._detect_url(user_input)" in src, "应复用 _detect_url"
    # 内联版本会直接出现 re.search(r"https?://...")
    assert 're.search(r"https?://' not in src, "不应再内联重写正则"


def test_detect_url_behaviour_preserved():
    from backend.agent.graph import AgentGraph

    has, url = AgentGraph._detect_url("打开 https://example.com/docs")
    assert has is True and "example.com" in url

    has2, _ = AgentGraph._detect_url("帮我重构一下这个函数")
    assert has2 is False


# ── Q10: swarm 后端 ──────────────────────────────────────────────

def test_backend_kind_has_no_phantom_remote():
    """REMOTE 曾在枚举中声明但无任何实现与注册，按枚举取值会拿到 None。"""
    from backend.swarm import BackendKind

    assert not hasattr(BackendKind, "REMOTE")
    assert hasattr(BackendKind, "TMUX")


def test_tmux_backend_importable_and_declares_reconnection():
    from backend.swarm import TmuxBackend, BackendKind

    tb = TmuxBackend()
    assert tb.kind == BackendKind.TMUX
    # tmux 会话可脱离进程存活并重新附着 —— 这是它相对独立终端的核心价值
    assert tb.capabilities.reconnection is True
    assert tb.capabilities.independent_terminal is True


def test_tmux_backend_availability_reflects_host():
    """tmux 是否可用取决于宿主，两者都必须是被支持的结果，不能抛异常。"""
    from backend.swarm import TmuxBackend

    avail = TmuxBackend().is_available()
    assert isinstance(avail, bool)


@pytest.mark.asyncio
async def test_tmux_spawn_raises_clearly_when_unavailable(monkeypatch):
    """tmux 不可用时应抛出明确错误，而不是静默假成功。"""
    from backend.swarm import TmuxBackend, AgentContext

    tb = TmuxBackend()
    monkeypatch.setattr(TmuxBackend, "_tmux", staticmethod(lambda: ""))

    with pytest.raises(RuntimeError, match="tmux"):
        await tb.spawn(AgentContext(agent_id="a1", name="t", task="x"), None)


def test_registry_skips_unavailable_tmux():
    """注册表必须只注册可用的后端 —— 注册了却用不了等于没有。"""
    from backend.swarm import BackendKind, get_backend_registry, TmuxBackend

    reg = get_backend_registry()
    kinds = reg.available_backends()
    assert BackendKind.IN_PROCESS in kinds, "进程内后端必须始终可用"

    if TmuxBackend().is_available():
        assert BackendKind.TMUX in kinds
    else:
        assert BackendKind.TMUX not in kinds, "不可用的 tmux 不应被注册"


def test_registry_get_best_prefers_reconnectable_backend():
    """get_best(prefer_terminal=True) 应优先可重连的 tmux，而非普通终端。"""
    from backend.swarm import BackendKind, BackendRegistry, InProcessBackend, TmuxBackend

    reg = BackendRegistry()
    reg.register(BackendKind.TMUX, TmuxBackend())
    best = reg.get_best(prefer_terminal=True)
    assert best is not None
    assert best.kind == BackendKind.TMUX


def test_get_best_falls_back_to_in_process():
    """没有任何终端后端时，必须回落到进程内后端而不是返回 None。"""
    from backend.swarm import BackendKind, BackendRegistry

    reg = BackendRegistry()
    reg._backends = {BackendKind.IN_PROCESS: reg.get(BackendKind.IN_PROCESS)}
    best = reg.get_best(prefer_terminal=True)
    assert best is not None
    assert best.kind == BackendKind.IN_PROCESS
