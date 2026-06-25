"""Aurora API - Chronicle routes."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/chronicle", tags=["chronicle"])

@router.get("/state")
async def chronicle_state():
    from backend.chronicle import chronicle
    return chronicle.get_state()

@router.post("/start")
async def chronicle_start():
    from backend.chronicle import chronicle
    result = await chronicle.start()
    if result.get("status") == "error":
        raise HTTPException(500, result.get("message", "Start failed"))
    return result

@router.post("/stop")
async def chronicle_stop():
    from backend.chronicle import chronicle
    return await chronicle.stop()

@router.post("/pause")
async def chronicle_pause():
    from backend.chronicle import chronicle
    return await chronicle.pause()

@router.post("/config")
async def chronicle_config(body: dict):
    from backend.chronicle import chronicle
    return {"config": chronicle.set_config(body), "state": chronicle.get_state()}
