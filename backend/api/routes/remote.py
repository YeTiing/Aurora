"""Aurora API 鈥?remote control routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from backend.remote_control import remote_control

router = APIRouter()


# ---- Request Models ----

class EnrollRequest(BaseModel):
    url: str
    account_id: str = ""
    server_id: str = ""
    environment_id: str = ""
    server_name: str = ""


class SSHRequest(BaseModel):
    host: str
    port: int = 22
    username: str = ""
    auth_method: str = "key"
    name: str = ""


class WSLRequest(BaseModel):
    distribution: str
    name: str = ""


# ---- Enrollment Endpoints ----

@router.get("/enrollments")
async def list_enrollments():
    return {"enrollments": [e.to_dict() for e in remote_control.list_enrollments()]}


@router.post("/enroll")
async def enroll_server(req: EnrollRequest):
    enrollment = remote_control.enroll_server(
        url=req.url,
        account_id=req.account_id,
        server_id=req.server_id,
        environment_id=req.environment_id,
        server_name=req.server_name,
    )
    return {"enrollment": enrollment.to_dict()}


@router.delete("/enroll/{server_id}")
async def disenroll_server(server_id: str):
    ok = remote_control.disenroll(server_id)
    if not ok:
        raise HTTPException(404, f"Enrollment not found: {server_id}")
    return {"disenrolled": server_id}


# ---- SSH Endpoints ----

@router.get("/ssh")
async def list_ssh():
    return {"connections": [c.to_dict() for c in remote_control.list_ssh_connections()]}


@router.post("/ssh")
async def add_ssh(req: SSHRequest):
    conn = remote_control.add_ssh_connection(
        host=req.host,
        port=req.port,
        username=req.username,
        auth_method=req.auth_method,
        name=req.name,
    )
    return {"connection": conn.to_dict()}


@router.delete("/ssh/{host}")
async def remove_ssh(host: str):
    ok = remote_control.remove_ssh_connection(host)
    if not ok:
        raise HTTPException(404, f"SSH connection not found: {host}")
    return {"removed": host}


# ---- WSL Endpoints ----

@router.get("/wsl")
async def list_wsl():
    return {"connections": [c.to_dict() for c in remote_control.list_wsl_connections()]}


@router.post("/wsl")
async def add_wsl(req: WSLRequest):
    conn = remote_control.add_wsl_connection(
        distribution=req.distribution,
        name=req.name,
    )
    return {"connection": conn.to_dict()}


@router.delete("/wsl/{name}")
async def remove_wsl(name: str):
    ok = remote_control.remove_wsl_connection(name)
    if not ok:
        raise HTTPException(404, f"WSL connection not found: {name}")
    return {"removed": name}
