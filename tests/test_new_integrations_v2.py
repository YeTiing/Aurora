# -*- coding: utf-8 -*-
"""Tests for newly integrated modules: LSP, AutoDream, QualityGate, ProviderProxy, Swarm, CU Gates."""
import sys, os, pytest, json, time, asyncio

# ── LSP Config Tests ───────────────────────────────────────────

def test_lsp_config_import():
    from backend.lsp import LSPConfig, get_builtin_configs, get_config_for_file
    configs = get_builtin_configs()
    assert "pyright" in configs
    assert "typescript" in configs
    assert "rust-analyzer" in configs
    assert configs["pyright"].command == "pyright-langserver"

def test_lsp_config_file_routing():
    from backend.lsp.config import get_config_for_file
    cfg = get_config_for_file("test.py")
    assert cfg is not None
    assert ".py" in cfg.extension_to_language

    cfg2 = get_config_for_file("test.rs")
    assert cfg2 is not None
    assert ".rs" in cfg2.extension_to_language

    cfg3 = get_config_for_file("test.xyz")
    assert cfg3 is None

def test_lsp_config_find_available():
    from backend.lsp.config import find_available_servers
    available = find_available_servers()
    assert isinstance(available, dict)

# ── LSP Client Tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_lsp_client_create():
    from backend.lsp.client import create_lsp_client
    client = create_lsp_client("test-server")
    assert client.server_name == "test-server"
    assert not client.is_initialized

@pytest.mark.asyncio
async def test_lsp_client_notification_handler():
    from backend.lsp.client import create_lsp_client
    client = create_lsp_client("test")
    received = []
    client.on_notification("test/event", lambda p: received.append(p))
    assert len(received) == 0  # handler registered but not yet fired

# ── LSP Server Instance Tests ──────────────────────────────────

def test_server_instance_create():
    from backend.lsp.server_instance import LSPServerInstance, LspServerConfig
    config = LspServerConfig(
        command="echo", args=["hello"],
        extension_to_language={".txt": "text"}
    )
    instance = LSPServerInstance("test", config)
    assert instance.name == "test"
    assert instance.state.value == "stopped"

# ── LSP Manager Tests ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_server_manager_create():
    from backend.lsp.server_manager import LSPServerManager
    mgr = LSPServerManager(auto_start=False)
    assert mgr.state == "not-started"

# ── Diagnostic Registry Tests ──────────────────────────────────

def test_diagnostic_registry():
    from backend.lsp.diagnostic_registry import DiagnosticRegistry, get_registry
    registry = DiagnosticRegistry()
    registry.register("test-server", [{"uri": "file:///test.py", "diagnostics": [
        {"message": "test error", "severity": 1, "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 5}}}
    ]}])
    pending = registry.check_for_diagnostics()
    assert len(pending) == 1
    assert pending[0].server_name == "test-server"

    # Second check should be empty
    pending2 = registry.check_for_diagnostics()
    assert len(pending2) == 0

def test_diagnostic_dedup():
    from backend.lsp.diagnostic_registry import DiagnosticRegistry
    registry = DiagnosticRegistry()
    diag = {"message": "err", "severity": "Error", "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}}}
    registry.register("s1", [{"uri": "/f.py", "diagnostics": [diag]}])
    attachments = registry.get_attachments()
    assert len(attachments) > 0

def test_passive_feedback_format():
    from backend.lsp.passive_feedback import format_diagnostics, map_severity
    assert map_severity(1) == "Error"
    assert map_severity(2) == "Warning"
    assert map_severity(None) == "Error"

    params = {
        "uri": "file:///test.py",
        "diagnostics": [{
            "message": "Undefined variable", "severity": 1,
            "range": {"start": {"line": 10, "character": 5}, "end": {"line": 10, "character": 10}},
            "source": "pyright",
        }]
    }
    result = format_diagnostics(params)
    assert len(result) == 1
    assert result[0]["message"] == "Undefined variable"
    assert result[0]["severity"] == "Error"
    assert result[0]["line"] == 10

# ── AutoDream Tests ────────────────────────────────────────────

def test_autodream_config():
    from backend.auto_dream import AutoDreamConfig
    cfg = AutoDreamConfig.from_env()
    assert cfg.min_hours >= 0
    assert cfg.min_sessions >= 0

def test_consolidation_lock(tmp_path):
    from backend.auto_dream import ConsolidationLock
    lock = ConsolidationLock(str(tmp_path))
    mtime = lock.try_acquire(stale_seconds=3600)
    assert mtime is not None
    assert mtime >= 0

def test_autodream_create():
    from backend.auto_dream import create_auto_dream
    dream = create_auto_dream()
    assert dream is not None
    assert isinstance(dream.is_enabled(), bool)

def test_autodream_gate_checks():
    from backend.auto_dream import create_auto_dream, AutoDreamConfig
    dream = create_auto_dream()
    passes, reason = dream.all_gates_pass()
    assert isinstance(passes, bool)
    assert isinstance(reason, str)

def test_consolidation_prompt():
    from backend.auto_dream import build_consolidation_prompt
    prompt = build_consolidation_prompt("/mem", "/transcripts", ["s1", "s2", "s3"])
    assert "Phase 1" in prompt
    assert "Phase 4" in prompt
    assert "s1" in prompt
    assert "/mem" in prompt

# ── Quality Gate Tests ─────────────────────────────────────────

def test_quality_gate_config():
    from backend.quality_gate import QualityGateConfig
    cfg = QualityGateConfig.from_args()
    assert cfg.min_pass_rate == 100.0
    assert cfg.baseline_file == ".aurora/quality_baseline.json"

def test_baseline_save_load(tmp_path):
    from backend.quality_gate import BaselineComparator, TestResult, CoverageResult, QualityGateConfig
    baseline_file = str(tmp_path / "baseline.json")
    config = QualityGateConfig(baseline_file=baseline_file)
    comparator = BaselineComparator(config)

    test_result = TestResult(total=10, passed=10)
    coverage = CoverageResult(pct=85.0, covered_lines=100, total_lines=117)
    comparator.save_baseline(test_result, coverage)
    assert os.path.exists(baseline_file)

    loaded = comparator.load_baseline()
    assert loaded is not None
    assert loaded.test_pass_rate == 100.0
    assert loaded.coverage_pct == 85.0

def test_baseline_compare():
    from backend.quality_gate import BaselineComparator, TestResult, CoverageResult, QualityGateConfig
    config = QualityGateConfig()
    comparator = BaselineComparator(config)
    test = TestResult(total=10, passed=9, failed=1)
    coverage = CoverageResult(pct=80.0)
    delta = comparator.compare(test, coverage)
    assert isinstance(delta, dict)
    assert "status" in delta

def test_quality_report():
    from backend.quality_gate import QualityReport, TestResult, CoverageResult
    report = QualityReport(
        passed=True,
        test_result=TestResult(total=5, passed=5),
        gates=[{"name": "Tests", "passed": True, "detail": "5/5"}],
    )
    assert report.passed
    assert len(report.gates) == 1

# ── Provider Proxy Tests ───────────────────────────────────────

def test_tool_translation_anthropic_to_openai():
    from backend.provider_proxy import anthropic_tool_to_openai, translate_tools, ProviderFormat
    anth_tool = {"name": "read_file", "description": "Read a file", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}
    oai_tool = anthropic_tool_to_openai(anth_tool)
    assert oai_tool["type"] == "function"
    assert oai_tool["function"]["name"] == "read_file"
    assert "parameters" in oai_tool["function"]

def test_tool_translation_batch():
    from backend.provider_proxy import translate_tools, ProviderFormat
    tools = [{"name": "t1", "description": "d1", "input_schema": {"type": "object"}}]
    result = translate_tools(tools, ProviderFormat.OPENAI_CHAT, ProviderFormat.ANTHROPIC)
    assert len(result) == 1
    assert result[0]["type"] == "function"

def test_message_translation_anth_to_oai():
    from backend.provider_proxy import anthropic_to_openai_chat
    messages = [{"role": "user", "content": "Hello"}]
    result = anthropic_to_openai_chat(messages, "You are helpful")
    assert len(result) == 2
    assert result[0]["role"] == "system"
    assert result[1]["role"] == "user"

def test_message_translation_oai_to_anth():
    from backend.provider_proxy import openai_chat_to_anthropic
    messages = [
        {"role": "system", "content": "Be helpful"},
        {"role": "user", "content": "Hi"},
    ]
    msgs, system = openai_chat_to_anthropic(messages)
    assert "Be helpful" in system
    assert len(msgs) == 1

def test_stream_translation():
    from backend.provider_proxy import translate_stream_chunk, ProviderFormat
    chunk = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}}
    result = translate_stream_chunk(chunk, ProviderFormat.OPENAI_CHAT, "claude-3")
    assert result is not None
    assert result["object"] == "chat.completion.chunk"

def test_billing_info():
    from backend.provider_proxy import BillingInfo
    info = BillingInfo.from_anthropic_headers({
        "request-id": "req_123",
        "anthropic-ratelimit-input-tokens": "100",
        "anthropic-ratelimit-output-tokens": "50",
    })
    assert info.request_id == "req_123"
    assert info.input_tokens == 100

def test_prompt_cache_key():
    from backend.provider_proxy import compute_prompt_cache_key
    key = compute_prompt_cache_key([{"role": "user", "content": "hi"}])
    assert len(key) == 16

# ── Swarm Tests ────────────────────────────────────────────────

def test_inprocess_backend():
    from backend.swarm import InProcessBackend, AgentContext
    backend = InProcessBackend()
    assert backend.kind.value == "in_process"
    assert backend.capabilities.permission_sync

@pytest.mark.asyncio
async def test_inprocess_spawn():
    from backend.swarm import InProcessBackend, AgentContext
    backend = InProcessBackend()

    async def dummy_runner(ctx):
        return "done"

    ctx = AgentContext(agent_id="test-1", name="Tester", task="test")
    result = await backend.spawn(ctx, dummy_runner)
    assert result["agent_id"] == "test-1"
    await backend.shutdown()

def test_terminal_backend():
    from backend.swarm import TerminalBackend
    backend = TerminalBackend()
    assert backend.kind.value == "terminal"
    assert backend.capabilities.independent_terminal

def test_backend_registry():
    from backend.swarm import get_backend_registry
    registry = get_backend_registry()
    backends = registry.available_backends()
    assert "in_process" in backends

def test_layout():
    from backend.swarm.layout import TeammateLayout
    layout = TeammateLayout()
    layout.add_cell("agent-1", "Agent 1", 0, 0, 80, 24)
    cell = layout.get_cell("agent-1")
    assert cell is not None
    assert cell.name == "Agent 1"
    d = layout.to_dict()
    assert "cells" in d

def test_permission_sync():
    from backend.swarm.permission_sync import PermissionSync
    sync = PermissionSync()
    pending = sync.get_pending()
    assert len(pending) == 0

def test_reconnection():
    from backend.swarm.reconnect import ReconnectionManager
    mgr = ReconnectionManager()
    mgr.register("agent-1", "in_process")
    stale = mgr.check_timeout(timeout_sec=99999)
    assert len(stale) == 0

# ── Computer Use Gates Tests ───────────────────────────────────

def test_cu_gates_defaults():
    from backend.computer_use.gates import CuGates, get_gates
    gates = get_gates()
    assert gates.enabled is True
    assert gates.clipboard_guard is True
    assert gates.coordinate_mode == "pixels"

def test_cu_permission_enabled():
    from backend.computer_use.gates import CuPermission, CuGates
    perm = CuPermission(CuGates(enabled=True))
    ok, _ = perm.can_screenshot()
    assert ok

def test_cu_permission_disabled():
    from backend.computer_use.gates import CuPermission, CuGates
    perm = CuPermission(CuGates(enabled=False))
    ok, reason = perm.can_screenshot()
    assert not ok
    assert "disabled" in reason.lower()

def test_cu_forbidden_combo():
    from backend.computer_use.gates import is_combo_forbidden, CuPermission, CuGates
    assert is_combo_forbidden(["win", "l"])
    assert is_combo_forbidden(["ctrl", "alt", "del"])
    assert not is_combo_forbidden(["ctrl", "c"])

    perm = CuPermission(CuGates(enabled=True))
    ok, reason = perm.can_keyboard_combo(["win", "l"])
    assert not ok
    ok2, _ = perm.can_keyboard_combo(["ctrl", "c"])
    assert ok2

def test_cu_forbidden_app():
    from backend.computer_use.gates import is_app_forbidden
    assert is_app_forbidden("cmd.exe")
    assert is_app_forbidden("regedit.exe")
    assert not is_app_forbidden("chrome.exe")

def test_cu_gates_update():
    from backend.computer_use.gates import update_gates, get_gates, reset_gates
    update_gates(pixel_validation=True, screenshot_quality=80)
    gates = get_gates()
    assert gates.pixel_validation is True
    assert gates.screenshot_quality == 80
    reset_gates()