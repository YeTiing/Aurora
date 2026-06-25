"""Aurora API - Chronicle routes."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/chronicle", tags=["chronicle"])


@router.get("/state")
async def chronicle_state():
    try:
        from backend.chronicle import get_chronicle
        c = get_chronicle()
        return {"state": c.get_state(), "config": c.get_config()}
    except ImportError:
        return {"state": "disabled", "config": {}}

@router.post("/start")
async def chronicle_start():
    try:
        from backend.chronicle import get_chronicle
        c = get_chronicle()
        c.start()
        return {"state": c.get_state()}
    except ImportError:
        raise HTTPException(501, "Chronicle not available")

@router.post("/stop")
async def chronicle_stop():
    try:
        from backend.chronicle import get_chronicle
        c = get_chronicle()
        c.stop()
        return {"state": c.get_state()}
    except ImportError:
        raise HTTPException(501, "Chronicle not available")

@router.post("/pause")
async def chronicle_pause():
    try:
        from backend.chronicle import get_chronicle
        c = get_chronicle()
        c.pause()
        return {"state": c.get_state()}
    except ImportError:
        raise HTTPException(501, "Chronicle not available")

@router.post("/config")
async def chronicle_config(body: dict):
    try:
        from backend.chronicle import get_chronicle
        c = get_chronicle()
        c.set_config(body)
        return {"state": c.get_state(), "config": c.get_config()}
    except ImportError:
        raise HTTPException(501, "Chronicle not available")
