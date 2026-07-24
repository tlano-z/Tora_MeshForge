from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
from threading import Thread
import time
from typing import Sequence

from tora_meshforge.models import LogCallback, ProgressCallback
from tora_meshforge.utils.cancellation import CancelledError, CancellationToken, ProcessController


PROGRESS_PREFIX = "TMF_PROGRESS "


@dataclass(frozen=True, slots=True)
class ProcessResult:
    command: tuple[str, ...]
    return_code: int
    output: str
    elapsed_seconds: float


class ProcessExecutionError(RuntimeError):
    def __init__(self, message: str, result: ProcessResult):
        super().__init__(message)
        self.result = result


def _windows_process_options() -> tuple[int, subprocess.STARTUPINFO | None]:
    """Keep background Blender processes from creating or revealing a console."""
    if os.name != "nt":
        return 0, None
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return creationflags, startupinfo


def run_process(
    command: Sequence[str | Path],
    *,
    cwd: Path,
    timeout_seconds: int,
    cancellation: CancellationToken,
    controller: ProcessController,
    on_log: LogCallback | None = None,
    on_progress: ProgressCallback | None = None,
) -> ProcessResult:
    args = tuple(str(item) for item in command)
    creationflags, startupinfo = _windows_process_options()
    started = time.monotonic()
    process = subprocess.Popen(
        args,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        creationflags=creationflags,
        startupinfo=startupinfo,
    )
    controller.attach(process)
    output: list[str] = []

    lines: Queue[str | None] = Queue()

    def read_output() -> None:
        assert process.stdout is not None
        for value in process.stdout:
            lines.put(value)
        lines.put(None)

    reader = Thread(target=read_output, name="tora-meshforge-process-output", daemon=True)
    reader.start()

    def stop_process() -> None:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    try:
        while True:
            cancellation.raise_if_cancelled()
            if time.monotonic() - started > timeout_seconds:
                raise TimeoutError(f"Process timed out after {timeout_seconds} seconds.")
            try:
                line = lines.get(timeout=0.1)
            except Empty:
                if process.poll() is not None and not reader.is_alive():
                    break
                continue
            if line is None:
                break
            if line:
                clean = line.rstrip("\r\n")
                output.append(clean)
                if clean.startswith(PROGRESS_PREFIX):
                    try:
                        event = json.loads(clean[len(PROGRESS_PREFIX) :])
                    except json.JSONDecodeError:
                        event = {"stage": "unknown", "message": clean}
                    if on_progress:
                        on_progress(event)
                elif on_log:
                    on_log(clean)
    except (CancelledError, TimeoutError):
        stop_process()
        raise
    finally:
        controller.detach(process)
    return_code = process.wait()
    result = ProcessResult(args, return_code, "\n".join(output), time.monotonic() - started)
    cancellation.raise_if_cancelled()
    if return_code != 0:
        raise ProcessExecutionError(f"Process exited with code {return_code}.", result)
    return result
