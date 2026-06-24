from fastapi.testclient import TestClient

from backend.api import app
from backend.shared_object import SharedObjectRepository, SharedObjectUpdate


def test_shared_object_set_get_snapshot_and_subscribe():
    events = []
    repo = SharedObjectRepository()
    repo.subscribe("host_config", events.append)

    update = repo.set("host_config", {"theme": "aurora", "remote_enabled": True}, source="desktop")

    assert update.key == "host_config"
    assert update.value == {"theme": "aurora", "remote_enabled": True}
    assert repo.get("host_config") == {"theme": "aurora", "remote_enabled": True}
    assert repo.get_snapshot() == {"host_config": {"theme": "aurora", "remote_enabled": True}}
    assert events == [update]


def test_shared_object_updates_are_immutable_snapshots():
    repo = SharedObjectRepository()
    original = {"connections": [{"host": "wsl", "status": "offline"}]}

    repo.set("remote_wsl_connections", original)
    original["connections"][0]["status"] = "online"

    assert repo.get("remote_wsl_connections") == {"connections": [{"host": "wsl", "status": "offline"}]}


def test_shared_object_unsubscribe_stops_notifications():
    events = []
    repo = SharedObjectRepository()
    repo.subscribe("codex_runtimes_config", events.append)
    repo.unsubscribe("codex_runtimes_config", events.append)

    repo.set("codex_runtimes_config", {"python": "python", "node": "node"})

    assert events == []


def test_shared_object_update_serializes_for_api():
    update = SharedObjectUpdate(key="pending_worktrees", value=["abc"], source="test", version=2)

    assert update.to_dict() == {
        "key": "pending_worktrees",
        "value": ["abc"],
        "source": "test",
        "version": 2,
        "timestamp": update.timestamp,
    }


def test_shared_object_api_roundtrip():
    client = TestClient(app)

    response = client.post(
        "/shared-objects/remote_control_connections",
        json={"value": [{"host": "local", "status": "online"}], "source": "desktop"},
    )
    assert response.status_code == 200
    assert response.json()["update"]["key"] == "remote_control_connections"

    get_response = client.get("/shared-objects/remote_control_connections")
    assert get_response.status_code == 200
    assert get_response.json()["value"] == [{"host": "local", "status": "online"}]

    snapshot = client.get("/shared-objects").json()["objects"]
    assert snapshot["remote_control_connections"] == [{"host": "local", "status": "online"}]


def test_shared_object_api_preserves_null_values():
    client = TestClient(app)

    response = client.post("/shared-objects/local_remote_control_enabled", json={"value": None})
    assert response.status_code == 200

    get_response = client.get("/shared-objects/local_remote_control_enabled")
    assert get_response.status_code == 200
    assert get_response.json() == {"key": "local_remote_control_enabled", "value": None}


def test_shared_object_api_requires_value_field():
    client = TestClient(app)

    response = client.post("/shared-objects/host_config", json={"source": "desktop"})

    assert response.status_code == 422
