from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import uuid


@dataclass(frozen=True, slots=True)
class JobLayout:
    root: Path
    source: Path
    inspect: Path
    logs: Path
    output: Path
    source_copy: Path
    report: Path
    event_log: Path
    output_model: Path
    process_report: Path

    @classmethod
    def create(cls, work_directory: Path, input_path: Path) -> "JobLayout":
        work_directory = work_directory.expanduser().resolve()
        root = work_directory / str(uuid.uuid4())
        source = root / "source"
        inspect = root / "inspect"
        logs = root / "logs"
        output = root / "output"
        source.mkdir(parents=True, exist_ok=False)
        inspect.mkdir(parents=True, exist_ok=False)
        logs.mkdir(parents=True, exist_ok=False)
        output.mkdir(parents=True, exist_ok=False)
        suffix = input_path.suffix.lower()
        safe_name = "original" + suffix
        return cls(
            root,
            source,
            inspect,
            logs,
            output,
            source / safe_name,
            inspect / "inspection.json",
            logs / "events.jsonl",
            output / "result.fbx",
            root / "report.json",
        )

    def copy_source(self, input_path: Path) -> None:
        shutil.copy2(input_path, self.source_copy)
