"""Aurora API — remote control routes."""
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


class ExecRequest(BaseModel):
    command: str
    timeout: float = Field(default=30.0, ge=1.0, le=300.0, description="Timeout in seconds (1-300)")


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


@router.post("/ssh/{host}/connect")
async def connect_ssh(host: str):
    ok = await remote_control.connect_ssh(host)
    return {"host": host, "connected": ok}


@router.post("/ssh/{host}/disconnect")
async def disconnect_ssh(host: str):
    ok = await remote_control.disconnect_ssh(host)
    return {"host": host, "disconnected": ok}


@router.post("/ssh/{host}/exec")
async def exec_ssh(host: str, req: ExecRequest):
    result = await remote_control.run_ssh_command(host, req.command, req.timeout)
    return {"host": host, "command": req.command, **result}


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


@router.post("/wsl/{name}/connect")
async def connect_wsl(name: str):
    ok = await remote_control.connect_wsl(name)
    if not ok:
        raise HTTPException(400, f"Failed to connect to WSL: {name}")
    return {"distribution": name, "connected": True}


@router.post("/wsl/{name}/exec")
async def exec_wsl(name: str, req: ExecRequest):
    result = await remote_control.run_wsl_command(name, req.command, req.timeout)
    return {"distribution": name, "command": req.command, **result}
