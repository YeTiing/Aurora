import pytest
import asyncio
from pathlib import Path

from backend.api.routes.files import _mask_config_secrets, _resolve_workspace_path, search_files
from backend.api.routes.chat import _resolve_workspace_path as resolve_list_path


def test_config_mask_redacts_nested_secret_values():
    cfg = {
        "llm": {"api_key": "sk-secret", "apiKey": "camel", "model": "gpt-4o"},
        "provider": {"refresh_token": "tok", "access_key": "ak", "nested": {"password": "pw", "credential": "cred"}},
        "auth": {"authorization": "Bearer token", "privateKey": "pk"},
        "plain": "visible",
    }

    masked = _mask_config_secrets(cfg)

    assert masked["llm"]["api_key"] == "***"
    assert masked["llm"]["apiKey"] == "***"
    assert masked["llm"]["model"] == "gpt-4o"
    assert masked["provider"]["refresh_token"] == "***"
    assert masked["provider"]["access_key"] == "***"
    assert masked["provider"]["nested"]["password"] == "***"
    assert masked["provider"]["nested"]["credential"] == "***"
    assert masked["auth"]["authorization"] == "***"
    assert masked["auth"]["privateKey"] == "***"
    assert masked["plain"] == "visible"


def test_file_routes_reject_paths_outside_workspace():
    with pytest.raises(Exception):
        _resolve_workspace_path("../outside.txt")


def test_file_list_route_rejects_paths_outside_workspace():
    with pytest.raises(Exception):
        resolve_list_path("../outside")


def test_search_route_rejects_paths_outside_workspace():
    with pytest.raises(Exception):
        asyncio.run(search_files({"path": "../outside", "query": "x"}))
