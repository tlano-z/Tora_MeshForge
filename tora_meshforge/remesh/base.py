from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class RemesherBackend(ABC):
    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Describe options exposed to GUI and CLI consumers."""

