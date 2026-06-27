import json

import pytest

from backend.api import deps
from backend.api.routes import chat as chat_routes
from backend.api.routes import settings as settings_routes
from backend.api.models import SettingsUpdate
from backend.agent.state import AgentState


@pytest.mark.asyncio
async def test_settings_update_resets_chat_route_cached_dependencies(monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(settings_routes.Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)

    chat_routes._cfg = object()
    chat_routes._llm = object()
    chat_routes._graph = object()
    chat_routes._rag = object()
    chat_routes._skills = object()
    chat_routes._plugins = object()

    class FakeConfig:
        def get(self, key, default=None):
            return default

        @property
        def model_context_window(self):
            return 128000

    monkeypatch.setattr(settings_routes, "_get_cfg", lambda: FakeConfig())
    monkeypatch.setattr(deps, "reset_deps", lambda: None)

    response = await settings_routes.update_settings(SettingsUpdate(provider="deepseek", model="deepseek-chat"))

    assert response["ok"] is True
    assert chat_routes._cfg is None
    assert chat_routes._llm is None
    assert chat_routes._graph is None
    assert chat_routes._rag is None
    assert chat_routes._skills is None
    assert chat_routes._plugins is None
