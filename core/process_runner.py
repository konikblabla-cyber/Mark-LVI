"""Subprocess runner with bounded output capture and hard time limits."""
from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_OUTPUT_LIMIT = 1_000_000
_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class BoundedProcessResult:
    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool = False
    output_limited: bool = False


def _kill_process_tree(process: subprocess.Popen) -> None:
    """Stop the process and descendants where the platform supports it."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=3, check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def run_bounded_process(args, *, cwd: Path, timeout: float,
                        env=None, output_limit: int = DEFAULT_OUTPUT_LIMIT,
                        shell: bool = False) -> BoundedProcessResult:
    """Run with bounded output and lifetime; shell is reserved for validated commands."""
    if not args:
        raise ValueError("A process command is required.")
    if shell and not isinstance(args, str):
        raise ValueError("Shell execution requires one validated command string.")
    if not shell and isinstance(args, str):
        raise ValueError("Commands must be argument lists unless shell execution is explicit.")
    if timeout <= 0:
        raise ValueError("Process timeout must be positive.")
    if output_limit < 1:
        raise ValueError("Output limit must be positive.")

    kwargs = {
        "args": args,
        "cwd": str(cwd),
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": bool(shell),
        "bufsize": 0,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(**kwargs)
    events: queue.Queue = queue.Queue(maxsize=16)
    closed: set[str] = set()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    captured = 0
    timed_out = False
    output_limited = False

    def drain(name: str, pipe) -> None:
        try:
            while True:
                chunk = pipe.read(_CHUNK_SIZE)
                if not chunk:
                    break
                events.put((name, chunk))
        finally:
            events.put((name, None))
            pipe.close()

    readers = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + timeout
    cleanup_deadline = None
    while len(closed) < 2:
        now = time.monotonic()
        if cleanup_deadline is None and now >= deadline:
            timed_out = True
            _kill_process_tree(process)
            cleanup_deadline = time.monotonic() + 1.5
        elif cleanup_deadline is not None and now >= cleanup_deadline:
            break

        try:
            name, chunk = events.get(timeout=0.05)
        except queue.Empty:
            continue

        if chunk is None:
            closed.add(name)
            continue

        remaining = output_limit - captured
        if remaining > 0:
            accepted = chunk[:remaining]
            buffers[name].extend(accepted)
            captured += len(accepted)
        if len(chunk) > max(remaining, 0) and not output_limited:
            output_limited = True
            _kill_process_tree(process)
            cleanup_deadline = time.monotonic() + 1.5

    if process.poll() is None:
        _kill_process_tree(process)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=1)
        except Exception:
            pass

    stdout = buffers["stdout"].decode("utf-8", errors="replace")
    stderr = buffers["stderr"].decode("utf-8", errors="replace")
    return BoundedProcessResult(
        stdout=stdout,
        stderr=stderr,
        returncode=process.poll(),
        timed_out=timed_out,
        output_limited=output_limited,
    )
