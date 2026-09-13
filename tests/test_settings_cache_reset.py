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

    deps._cfg = object()
    deps._llm = object()
    deps._graph = object()
    deps._rag = object()
    deps._skills = object()
    deps._plugins = object()

    class FakeConfig:
        def get(self, key, default=None):
            return default

        @property
        def model_context_window(self):
            return 128000

    monkeypatch.setattr(settings_routes, "_get_cfg", lambda: FakeConfig())

    response = await settings_routes.update_settings(SettingsUpdate(provider="deepseek", model="deepseek-chat"))

    assert response["ok"] is True
    assert deps._cfg is None
    assert deps._llm is None
    assert deps._graph is None
    assert deps._rag is None
    assert deps._skills is None
    assert deps._plugins is None
