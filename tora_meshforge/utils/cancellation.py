from __future__ import annotations

from dataclasses import dataclass, field
from subprocess import Popen
from threading import Event, Lock
from typing import IO


class CancelledError(RuntimeError):
    pass


@dataclass(slots=True)
class CancellationToken:
    _event: Event = field(default_factory=Event)

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise CancelledError("The operation was cancelled.")


class ProcessController:
    def __init__(self) -> None:
        self._lock = Lock()
        self._process: Popen[str] | None = None

    def attach(self, process: Popen[str]) -> None:
        with self._lock:
            self._process = process

    def detach(self, process: Popen[str]) -> None:
        with self._lock:
            if self._process is process:
                self._process = None

    def terminate(self) -> None:
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()

