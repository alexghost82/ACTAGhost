"""System connector — full control over the host operating system.

Capabilities:
  * exec      — run an arbitrary shell command (captured, with timeout)
  * spawn     — launch a program detached (non-blocking)
  * processes — list running processes
  * kill      — terminate / kill a process by pid or name
  * service   — start / stop / restart / status a system service (OS-aware)
  * fs        — create / read / write / append / delete / move / list anywhere

WARNING: This grants ACTA real, unrestricted control of the machine. It is
gated by ``ACTA_ALLOW_SYSTEM_CONTROL`` and the ``system.control`` capability,
and every invocation is written to the audit log by the calling agent.
"""

from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any

from acta.config import Settings, get_settings
from acta.integration.connectors import Connector
from acta.logging_config import get_logger

log = get_logger("integration.system")

_OS = platform.system()  # 'Darwin' | 'Linux' | 'Windows'


class SystemConnector(Connector):
    name = "system"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # -- dispatch ---------------------------------------------------------- #
    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.allow_system_control:
            return {"ok": False, "error": "system control disabled", "code": "disabled"}
        handler = {
            "exec": self._exec,
            "run": self._exec,
            "spawn": self._spawn,
            "processes": self._processes,
            "list_processes": self._processes,
            "kill": self._kill,
            "service": self._service,
            "fs": self._fs,
            "info": self._info,
        }.get(action)
        if handler is None:
            return {"ok": False, "error": f"unknown action '{action}'"}
        try:
            return handler(params)
        except Exception as exc:  # never let a system op crash the pipeline
            log.exception("system action %s failed", action)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # -- commands ---------------------------------------------------------- #
    def _exec(self, p: dict[str, Any]) -> dict[str, Any]:
        command = p.get("command")
        if not command:
            return {"ok": False, "error": "missing 'command'"}
        timeout = int(p.get("timeout", self.settings.system_exec_timeout))
        cwd = p.get("cwd")
        proc = subprocess.run(
            command,
            shell=isinstance(command, str),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **(p.get("env") or {})},
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-8000:],
            "stderr": proc.stderr[-4000:],
        }

    def _spawn(self, p: dict[str, Any]) -> dict[str, Any]:
        command = p.get("command")
        if not command:
            return {"ok": False, "error": "missing 'command'"}
        popen = subprocess.Popen(
            command,
            shell=isinstance(command, str),
            cwd=p.get("cwd"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"ok": True, "pid": popen.pid, "detached": True}

    # -- processes --------------------------------------------------------- #
    def _processes(self, p: dict[str, Any]) -> dict[str, Any]:
        import psutil

        name_filter = (p.get("filter") or "").lower()
        limit = int(p.get("limit", 50))
        out = []
        for proc in psutil.process_iter(["pid", "name", "username", "cpu_percent"]):
            info = proc.info
            if name_filter and name_filter not in (info.get("name") or "").lower():
                continue
            out.append(info)
            if len(out) >= limit:
                break
        return {"ok": True, "count": len(out), "processes": out}

    def _kill(self, p: dict[str, Any]) -> dict[str, Any]:
        import psutil

        force = bool(p.get("force"))
        sig = signal.SIGKILL if force and hasattr(signal, "SIGKILL") else signal.SIGTERM
        killed = []
        if "pid" in p:
            proc = psutil.Process(int(p["pid"]))
            proc.send_signal(sig)
            killed.append(proc.pid)
        elif "name" in p:
            target = str(p["name"]).lower()
            for proc in psutil.process_iter(["pid", "name"]):
                if target in (proc.info.get("name") or "").lower():
                    try:
                        proc.send_signal(sig)
                        killed.append(proc.pid)
                    except psutil.Error:
                        continue
        else:
            return {"ok": False, "error": "provide 'pid' or 'name'"}
        return {"ok": bool(killed), "killed": killed, "signal": int(sig)}

    # -- services ---------------------------------------------------------- #
    def _service(self, p: dict[str, Any]) -> dict[str, Any]:
        name = p.get("name")
        op = p.get("op", "status")  # start | stop | restart | status
        if not name:
            return {"ok": False, "error": "missing service 'name'"}

        if _OS == "Linux":
            cmd = ["systemctl", op, name]
        elif _OS == "Darwin":
            mac = {
                "start": ["launchctl", "load", name],
                "stop": ["launchctl", "unload", name],
                "restart": ["launchctl", "kickstart", "-k", name],
                "status": ["launchctl", "list", name],
            }
            cmd = mac.get(op, mac["status"])
        elif _OS == "Windows":
            win = {
                "start": ["sc", "start", name],
                "stop": ["sc", "stop", name],
                "restart": ["sc", "stop", name],  # caller should start again
                "status": ["sc", "query", name],
            }
            cmd = win.get(op, win["status"])
        else:
            return {"ok": False, "error": f"unsupported OS '{_OS}'"}

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return {
            "ok": proc.returncode == 0,
            "os": _OS,
            "command": " ".join(cmd),
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-2000:],
        }

    # -- filesystem (unrestricted) ----------------------------------------- #
    def _fs(self, p: dict[str, Any]) -> dict[str, Any]:
        op = p.get("op")
        path = p.get("path")
        if op != "list" and not path:
            return {"ok": False, "error": "missing 'path'"}
        target = Path(path).expanduser() if path else None

        if op == "read":
            return {"ok": True, "content": target.read_text(encoding="utf-8", errors="replace")}
        if op in ("write", "create"):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(p.get("content", ""), encoding="utf-8")
            return {"ok": True, "path": str(target)}
        if op == "append":
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                fh.write(p.get("content", ""))
            return {"ok": True, "path": str(target)}
        if op == "delete":
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            else:
                return {"ok": False, "error": "path not found"}
            return {"ok": True, "deleted": str(target)}
        if op == "move":
            dest = Path(p["dest"]).expanduser()
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(dest))
            return {"ok": True, "from": str(target), "to": str(dest)}
        if op == "mkdir":
            target.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "path": str(target)}
        if op == "list":
            base = target or Path.cwd()
            return {"ok": True, "path": str(base), "entries": sorted(e.name for e in base.iterdir())}
        return {"ok": False, "error": f"unknown fs op '{op}'"}

    # -- info -------------------------------------------------------------- #
    def _info(self, p: dict[str, Any]) -> dict[str, Any]:
        import psutil

        return {
            "ok": True,
            "os": _OS,
            "release": platform.release(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory": dict(psutil.virtual_memory()._asdict()),
            "cwd": os.getcwd(),
            "user": os.environ.get("USER") or os.environ.get("USERNAME"),
        }
