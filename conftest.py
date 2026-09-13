"""pytest 全局夹具。

隔离原因：backend/auth.py 的 DEFAULT_DATA_DIR 在模块导入时求值为
~/.aurora，测试会直接创建/删除开发者真实的 tokens.enc；且缺少隔离时
测试无法在受限沙箱或干净 CI 中运行。
"""
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_aurora_home(tmp_path, monkeypatch):
    """把 AURORA_HOME 指向临时目录，杜绝测试触碰真实用户目录。"""
    home = tmp_path / "aurora_home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AURORA_HOME", str(home))
    yield home


@pytest.fixture(autouse=True)
def _clean_auth_env(monkeypatch):
    """清理认证相关环境变量，避免宿主机配置干扰测试。"""
    for key in ("AURORA_REQUIRE_AUTH", "AURORA_AUTH_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture(autouse=True)
def _stub_llm_api_key(request, monkeypatch):
    """为需要构造 LLMClient 的测试提供占位 API Key。

    conftest 隔离 AURORA_HOME 后，用户级配置不再可见，deps.get_llm() 会因
    "No API Key configured" 抛 503，导致断言 403 的路径安全测试失败 ——
    说明这些测试此前隐式依赖开发者本机的 ~/.aurora/ 配置。

    只对确实需要它的测试模块生效：test_config 断言 llm_api_key 为空，
    全局注入会破坏该断言。
    """
    needs_key = {"test_backend_closure", "test_api_security_boundaries", "test_p1_ws_context"}
    if request.module.__name__.split(".")[-1] in needs_key:
        monkeypatch.setenv("AURORA_LLM_API_KEY", "test-placeholder-key")
    yield


def pytest_configure(config):
    """确保项目根在 sys.path 上，测试可从任意目录运行。"""
    root = Path(__file__).resolve().parent
    if str(root) not in os.sys.path:
        os.sys.path.insert(0, str(root))
