# -*- coding: utf-8 -*-
"""Aurora LSP 传输层测试 —— 参数构造 / URI / 方法名 / 降级。

⚠️ 本文件由 Necessity 的同名测试改写而来。原版测的是 Necessity 自带的
LSP 实现（含独立的 `rpc` / `uri` 模块与可注入 IO 的 `attach_io`），
合并后 LSP 统一用 `backend/lsp`，设计不同（分帧内联在 client.py、
无 IO 注入点）。**被测契约不同，所以改写；但所有防护性断言都保留** ——
它们守护的是真实缺陷，不是实现细节。

保留的缺陷防护（全部实测确认过）：
  ① rootUri / workspaceFolders 为 None/[] → documentSymbol 返回 0 符号且不报错
  ② includeDeclaration=True → 定义点被当引用，建图产生自环
  ③ processId=None → server 无法感知父进程死亡
  ④ prepareCallHierarchy 方法名写错 → -32601
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from backend.lsp.config import LspServerConfig, resolve_executable
from backend.lsp.server_instance import LSPServerInstance


# ── URI 工具（Aurora 用实例方法 _path_to_uri；这里给等价模块级实现，
#      供不需要起 server 的纯函数断言使用）─────────────────────────

def path_to_uri(p) -> str:
    return Path(p).resolve().as_uri()


def uri_to_path(u: str) -> str:
    parsed = urlparse(u)
    raw = unquote(parsed.path)
    if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":  # Windows /C:/x
        raw = raw[1:]
    return raw


def normalize_uri(u: str) -> str:
    """归一化成可比形式：小写盘符 + 统一斜杠。

    pyright 回传 `file:///c%3A/...`（小写盘符 + 百分号编码），
    而 Path.as_uri() 给 `file:///C:/...`。不归一化就永远比不相等 ——
    图里的节点身份会因此分裂成两份。
    """
    return uri_to_path(u).replace(chr(92), "/").lower()


def _inst() -> LSPServerInstance:
    return LSPServerInstance("pyright", LspServerConfig(command="pyright-langserver"))


# ── 缺陷①：rootUri / workspaceFolders / processId ───────────────

def test_init_params_have_valid_root_uri(tmp_path):
    """rootUri 必须非 None 且等于仓库根的 file:// URI。

    最关键的断言：rootUri=None 时 pyright 找不到项目根，
    documentSymbol 返回 0 个符号**且不报错**，跨文件解析全部失效。
    """
    root = tmp_path.resolve()
    params = _inst()._build_init_params(root)

    assert params["rootUri"] is not None, "rootUri 为 None → 跨文件解析静默失效"
    assert params["rootUri"] != ""
    assert params["rootUri"].startswith("file://")
    assert params["rootUri"] == root.as_uri()


def test_init_params_workspace_folders_non_empty(tmp_path):
    root = tmp_path.resolve()
    params = _inst()._build_init_params(root)

    folders = params["workspaceFolders"]
    assert isinstance(folders, list) and len(folders) == 1, "workspaceFolders 不能为空"
    assert folders[0]["uri"] == params["rootUri"]
    assert folders[0]["name"] == root.name


def test_init_params_process_id_is_current_pid(tmp_path):
    """processId 必须是当前 pid —— None 时 server 感知不到父进程死亡。"""
    params = _inst()._build_init_params(tmp_path.resolve())
    assert params["processId"] == os.getpid()


def test_init_params_declares_document_symbol_hierarchy(tmp_path):
    """必须声明 hierarchicalDocumentSymbolSupport —— 采符号要用层级结构。"""
    params = _inst()._build_init_params(tmp_path.resolve())
    ds = params["capabilities"]["textDocument"]["documentSymbol"]
    assert ds.get("hierarchicalDocumentSymbolSupport") is True


def test_init_params_are_json_serializable(tmp_path):
    """参数要能 JSON 序列化 —— rootUri 若是 Path 对象会在发送时炸。"""
    import json

    json.dumps(_inst()._build_init_params(tmp_path.resolve()))


def test_init_params_without_root_degrade_to_none():
    """无 root 时降级为 None（而非崩溃）—— start() 会打 warning 提示。"""
    params = _inst()._build_init_params(None)
    assert params["rootUri"] is None
    assert params["workspaceFolders"] == []
    # 与环境无关的字段不应一起退化
    assert params["processId"] == os.getpid()


# ── 缺陷②：includeDeclaration 默认 False ────────────────────────

def test_get_references_defaults_include_declaration_false():
    """默认必须 False —— True 会把定义点当引用返回，建图产生自环。"""
    import inspect

    from backend.lsp.server_manager import LSPServerManager

    sig = inspect.signature(LSPServerManager.get_references)
    assert sig.parameters["include_declaration"].default is False, (
        "includeDeclaration 必须默认 False（原先硬编码 True，调用方无法关闭）"
    )


# ── 缺陷④：callHierarchy 方法名 ─────────────────────────────────

def test_prepare_call_hierarchy_uses_text_document_prefix():
    """方法名必须是 `textDocument/prepareCallHierarchy`。

    实测：写成 `callHierarchy/prepareCallHierarchy` 返回
    `-32601 Unhandled method`（pyright 1.1.414）。
    只有 incoming/outgoing 用 `callHierarchy/` 前缀。
    """
    import inspect

    from backend.lsp.server_manager import LSPServerManager

    src = inspect.getsource(LSPServerManager.prepare_call_hierarchy)
    # 只看**实际发出的请求**，不能被 docstring 干扰 ——
    # 该方法的注释里确实提到了错误名（用于说明「不要这么写」）。
    call_lines = [ln for ln in src.splitlines() if "send_request(" in ln]
    assert call_lines, "未找到 send_request 调用"
    assert any("textDocument/prepareCallHierarchy" in ln for ln in call_lines), (
        f"实际请求没有用正确方法名: {call_lines}"
    )
    assert not any(ln.strip().startswith('callHierarchy/') for ln in call_lines)


def test_call_hierarchy_methods_exist():
    from backend.lsp.server_manager import LSPServerManager

    for name in ("prepare_call_hierarchy", "incoming_calls", "outgoing_calls"):
        assert hasattr(LSPServerManager, name), f"缺 {name}"


def test_document_symbols_method_exists():
    """合并前 Aurora **根本没有**这个方法 —— Necessity 的符号索引需要它。"""
    from backend.lsp.server_manager import LSPServerManager

    assert hasattr(LSPServerManager, "get_document_symbols")


# ── resolve_executable：Windows .CMD shim ───────────────────────

def test_resolve_executable_returns_absolute_path_when_found():
    """Windows 上 create_subprocess_exec 不套用 PATHEXT，
    裸命令名会 WinError 2。必须解析成带扩展名的绝对路径。"""
    import shutil

    got = resolve_executable("pyright-langserver")
    if shutil.which("pyright-langserver"):
        assert Path(got).is_absolute(), f"未解析成绝对路径: {got}"
    else:
        assert got == "pyright-langserver"


def test_resolve_executable_passthrough_for_unknown():
    assert resolve_executable("no-such-lsp-binary-xyz") == "no-such-lsp-binary-xyz"


# ── URI 归一化：pyright 的百分号编码盘符 ────────────────────────

def test_path_uri_round_trip_with_spaces_and_unicode(tmp_path):
    d = tmp_path / "有 空格 and space"
    d.mkdir()
    uri = path_to_uri(d)
    assert uri.startswith("file://")
    assert " " not in uri, "空格必须被编码（否则是非法 URI）"
    assert Path(uri_to_path(uri)).resolve() == d.resolve()


def test_normalize_uri_matches_pyright_form():
    """pyright 回 `file:///c%3A/...`，Path.as_uri() 给 `file:///C:/...`。
    归一化后必须相等 —— 否则图节点身份会分裂成两份。"""
    from_pyright = "file:///c%3A/codex_Projects/Aurora/backend/tools/base.py"
    from_pathlib = path_to_uri("C:/codex_Projects/Aurora/backend/tools/base.py")
    assert normalize_uri(from_pyright) == normalize_uri(from_pathlib)


def test_normalize_uri_is_case_insensitive():
    assert normalize_uri("file:///C:/Foo/Bar.py") == normalize_uri("file:///c%3A/foo/bar.py")


# ── 无 server 时返回 None（不抛）────────────────────────────────

@pytest.mark.asyncio
async def test_query_returns_none_when_no_server(tmp_path):
    """没有可用 server 时必须返回 None，不能抛异常。

    INTEGRATION.md §8.2：任何降级都不得让 Agent 无法工作 ——
    LSP 是增强能力，缺失时应静默降级而非中断任务。
    """
    from backend.lsp.server_manager import LSPServerManager

    m = LSPServerManager()  # 未 initialize，无 server
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")

    assert await m.get_document_symbols(str(f)) is None
    assert await m.get_references(str(f), 0, 0) is None
    assert await m.prepare_call_hierarchy(str(f), 0, 0) is None
    assert await m.incoming_calls({"uri": "file:///x", "range": {}, "selectionRange": {}}) is None


# ── 配置 ────────────────────────────────────────────────────────

def test_config_available_servers_lookup():
    from backend.lsp.config import find_available_servers, get_builtin_configs

    assert isinstance(get_builtin_configs(), dict)
    assert isinstance(find_available_servers(), dict)


def test_pyright_config_present():
    from backend.lsp.config import get_builtin_configs

    cfgs = get_builtin_configs()
    assert "pyright" in cfgs
    assert ".py" in cfgs["pyright"].extension_to_language
