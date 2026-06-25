"""Aurora API — Connector management routes."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/connectors", tags=["connectors"])


def _get_registry():
    from backend.connectors.base import get_registry
    return get_registry()


# ---------------------------------------------------------------------------
# GET /connectors — list all registered connectors
# ---------------------------------------------------------------------------
@router.get("")
async def list_connectors():
    """Return all registered connectors with their connection status."""
    registry = _get_registry()
    return {
        "connectors": registry.list_all(),
        "count": registry.count,
    }


# ---------------------------------------------------------------------------
# POST /connectors/{connector_id}/auth — start OAuth flow
# ---------------------------------------------------------------------------
@router.post("/{connector_id}/auth")
async def start_oauth(connector_id: str, redirect_uri: str | None = None):
    """Return the OAuth authorization URL for a connector."""
    registry = _get_registry()
    connector = registry.get(connector_id)
    if connector is None:
        raise HTTPException(404, f"Connector '{connector_id}' not found")

    import uuid
    state = uuid.uuid4().hex

    # Override redirect_uri if provided
    if redirect_uri:
        connector.config.redirect_uri = redirect_uri

    auth_url = connector.get_oauth_url(state)
    return {
        "connector_id": connector_id,
        "auth_url": auth_url,
        "state": state,
    }


# ---------------------------------------------------------------------------
# GET /connectors/{connector_id}/callback — OAuth callback handler
# ---------------------------------------------------------------------------
@router.get("/{connector_id}/callback")
async def oauth_callback(connector_id: str, code: str, state: str = ""):
    """Handle the OAuth redirect callback."""
    registry = _get_registry()
    connector = registry.get(connector_id)
    if connector is None:
        raise HTTPException(404, f"Connector '{connector_id}' not found")

    success = await connector.handle_callback(code, state)
    return {
        "connector_id": connector_id,
        "connected": success,
        "status": "connected" if success else "failed",
    }


# ---------------------------------------------------------------------------
# GET /connectors/{connector_id}/status — check connection status
# ---------------------------------------------------------------------------
@router.get("/{connector_id}/status")
async def connector_status(connector_id: str):
    """Get the current connection status of a connector."""
    registry = _get_registry()
    connector = registry.get(connector_id)
    if connector is None:
        raise HTTPException(404, f"Connector '{connector_id}' not found")

    return connector.to_dict()


# ---------------------------------------------------------------------------
# DELETE /connectors/{connector_id}/disconnect — disconnect a connector
# ---------------------------------------------------------------------------
@router.delete("/{connector_id}/disconnect")
async def disconnect_connector(connector_id: str):
    """Disconnect and revoke tokens for a connector."""
    registry = _get_registry()
    connector = registry.get(connector_id)
    if connector is None:
        raise HTTPException(404, f"Connector '{connector_id}' not found")

    await connector.disconnect()
    return {
        "connector_id": connector_id,
        "connected": False,
        "status": "disconnected",
    }
