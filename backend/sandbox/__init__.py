# Docker 沙箱 — 命令隔离 + 白名单 + 资源限制 + 本地降级
from __future__ import annotations
import asyncio, os, shlex, tempfile, json
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class SandboxConfig:
    image: str = "aurora-sandbox:latest"
    network: bool = False
    whitelist: list[str] = field(default_factory=lambda: ["python","python3","node","npm","npx","cargo","go","git","rg","grep","find","ls","cat","mkdir","rm","cp","mv","curl","wget","pip","pip3","tsc","make","cmake"])
    timeout_sec: int = 30
    memory: str = "512m"
    cpus: float = 1.0
    mount_workspace: bool = True

class DockerSandbox:
    def __init__(self, config: SandboxConfig | None = None):
        self.config = config or SandboxConfig()
        self._ready = False
        self._name = f"aurora-sandbox-{os.getpid()}"

    async def _ensure(self):
        if self._ready: return
        try:
            proc = await asyncio.create_subprocess_exec("docker","info",stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            await proc.communicate()
            if proc.returncode != 0: self._ready = False; return
        except FileNotFoundError: self._ready = False; return

        await self._dc(["rm","-f",self._name])
        net = "none" if not self.config.network else "bridge"
        ok = await self._dc(["run","-d","--name",self._name,"--network",net,"--memory",self.config.memory,"--cpus",str(self.config.cpus),"--rm",self.config.image,"tail","-f","/dev/null"])
        self._ready = ok

    async def _dc(self, args: list[str]) -> bool:
        proc = await asyncio.create_subprocess_exec("docker",*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
        return proc.returncode == 0

    async def execute(self, command: str, timeout: int | None = None, cwd: str = "/workspace") -> dict:
        await self._ensure()
        timeout = timeout or self.config.timeout_sec
        cmd = command.strip()
        first = cmd.split()[0] if cmd else ""
        if first not in self.config.whitelist:
            return {"success":False,"stdout":"","stderr":f"Not whitelisted: {first}","exit_code":-1,"sandboxed":False}
        if not self._ready:
            return await self._run_local(cmd, timeout)

        try:
            proc = await asyncio.create_subprocess_exec("docker","exec","-w",cwd,self._name,"sh","-c",cmd,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return {"success":proc.returncode==0,"stdout":stdout.decode(errors="replace")[:16000],"stderr":stderr.decode(errors="replace")[:4000],"exit_code":proc.returncode,"sandboxed":True}
        except asyncio.TimeoutError:
            return {"success":False,"stdout":"","stderr":f"Timeout ({timeout}s)","exit_code":-1,"sandboxed":True}
        except Exception as e:
            return {"success":False,"stdout":"","stderr":str(e)[:500],"exit_code":-1,"sandboxed":True}

    async def _run_local(self, cmd: str, timeout: int) -> dict:
        try:
            proc = await asyncio.create_subprocess_shell(cmd,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            stdout,stderr = await asyncio.wait_for(proc.communicate(),timeout=timeout)
            return {"success":proc.returncode==0,"stdout":stdout.decode(errors="replace")[:16000],"stderr":stderr.decode(errors="replace")[:4000],"exit_code":proc.returncode,"sandboxed":False}
        except asyncio.TimeoutError:
            return {"success":False,"stdout":"","stderr":f"Timeout ({timeout}s)","exit_code":-1,"sandboxed":False}

    async def cleanup(self):
        if self._ready: await self._dc(["rm","-f",self._name]); self._ready = False

sandbox = DockerSandbox()