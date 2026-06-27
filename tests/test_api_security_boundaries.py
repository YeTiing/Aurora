import pytest
import asyncio
from pathlib import Path
from fastapi import HTTPException

from backend.api.path_security import resolve_allowed_path, allowed_roots
from backend.api.routes.files import _mask_config_secrets, _resolve_workspace_path, search_files
from backend.api.routes.chat import _resolve_workspace_path as resolve_list_path


class DummyConfig:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


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


@pytest.mark.parametrize("key", [
    "permissions.allowed_roots",
    "sandbox.allowed_roots",
    "workspace.allowed_roots",
    "allowed_roots",
])
def test_path_security_accepts_all_allowed_root_keys(tmp_path, key):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "allowed.txt"
    target.write_text("ok")
    cfg = DummyConfig({key: [str(workspace)]})

    assert resolve_allowed_path("allowed.txt", str(workspace), cfg) == target.resolve()


def test_path_security_supports_nested_dict_config(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cfg = {"permissions": {"allowed_roots": [str(workspace)]}}

    assert allowed_roots(str(workspace), cfg) == [workspace.resolve()]


def test_path_security_allows_user_configured_root_absolute_path(tmp_path):
    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    workspace.mkdir()
    external.mkdir()
    target = external / "allowed.txt"
    target.write_text("ok")
    cfg = DummyConfig({"permissions.allowed_roots": [str(external)]})

    resolved = resolve_allowed_path(str(target), str(external), cfg)

    assert resolved == target.resolve()


def test_path_security_allows_configured_absolute_path_even_when_workspace_differs(tmp_path):
    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    workspace.mkdir()
    external.mkdir()
    target = external / "allowed.txt"
    target.write_text("ok")
    cfg = DummyConfig({"permissions.allowed_roots": [str(external)]})

    resolved = resolve_allowed_path(str(target), str(workspace), cfg)

    assert resolved == target.resolve()


def test_path_security_rejects_workspace_outside_configured_roots(tmp_path):
    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    workspace.mkdir()
    external.mkdir()

    with pytest.raises(HTTPException) as exc:
        resolve_allowed_path("blocked.txt", str(workspace), DummyConfig({"permissions.allowed_roots": [str(external)]}))

    assert exc.value.status_code == 403


def test_path_security_rejects_unconfigured_absolute_path(tmp_path):
    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    workspace.mkdir()
    external.mkdir()

    with pytest.raises(HTTPException) as exc:
        resolve_allowed_path(str(external / "blocked.txt"), str(workspace), DummyConfig({}))

    assert exc.value.status_code == 403


def test_file_routes_reject_paths_outside_workspace():
    with pytest.raises(HTTPException) as exc:
        _resolve_workspace_path("../outside.txt")

    assert exc.value.status_code == 403


def test_file_list_route_rejects_paths_outside_workspace():
    with pytest.raises(HTTPException) as exc:
        resolve_list_path("../outside")

    assert exc.value.status_code == 403


def test_search_route_rejects_paths_outside_workspace():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(search_files({"path": "../outside", "query": "x"}))

    assert exc.value.status_code == 403
