import subprocess
import sys
from pathlib import Path
from threading import Timer

import pytest

from tora_meshforge.utils.cancellation import CancelledError, CancellationToken, ProcessController
from tora_meshforge.utils.subprocess_runner import (
    ProcessExecutionError,
    _windows_process_options,
    run_process,
)


def test_background_processes_do_not_create_windows_consoles() -> None:
    creationflags, startupinfo = _windows_process_options()
    if sys.platform == "win32":
        assert creationflags & subprocess.CREATE_NO_WINDOW
        assert creationflags & subprocess.CREATE_NEW_PROCESS_GROUP
        assert startupinfo is not None
        assert startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW
        assert startupinfo.wShowWindow == subprocess.SW_HIDE
    else:
        assert creationflags == 0
        assert startupinfo is None


def test_nonzero_exit_is_mapped_to_structured_error(tmp_path: Path) -> None:
    with pytest.raises(ProcessExecutionError) as captured:
        run_process(
            [sys.executable, "-c", "import sys; print('diagnostic'); sys.exit(7)"],
            cwd=tmp_path,
            timeout_seconds=10,
            cancellation=CancellationToken(),
            controller=ProcessController(),
        )
    assert captured.value.result.return_code == 7
    assert "diagnostic" in captured.value.result.output


def test_progress_event_is_decoded(tmp_path: Path) -> None:
    events = []
    result = run_process(
        [sys.executable, "-c", "print('TMF_PROGRESS {\"stage\": \"test\", \"progress\": 0.5}')"],
        cwd=tmp_path,
        timeout_seconds=10,
        cancellation=CancellationToken(),
        controller=ProcessController(),
        on_progress=events.append,
    )
    assert result.return_code == 0
    assert events == [{"stage": "test", "progress": 0.5}]


def test_timeout_works_when_child_produces_no_output(tmp_path: Path) -> None:
    with pytest.raises(TimeoutError):
        run_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            timeout_seconds=1,
            cancellation=CancellationToken(),
            controller=ProcessController(),
        )


def test_cancel_stops_a_silent_process_without_waiting_for_timeout(tmp_path: Path) -> None:
    cancellation = CancellationToken()
    timer = Timer(0.2, cancellation.cancel)
    timer.start()
    try:
        with pytest.raises(CancelledError):
            run_process(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=tmp_path,
                timeout_seconds=20,
                cancellation=cancellation,
                controller=ProcessController(),
            )
    finally:
        timer.cancel()
