from pathlib import Path

from tora_meshforge.utils.file_layout import JobLayout


def test_job_layout_is_isolated_and_copies_source(tmp_path: Path) -> None:
    model = tmp_path / "source model.fbx"
    model.write_bytes(b"fbx")
    layout = JobLayout.create(tmp_path / "work", model)
    layout.copy_source(model)
    assert layout.root.parent == (tmp_path / "work").resolve()
    assert layout.source_copy.name == "original.fbx"
    assert layout.source_copy.read_bytes() == b"fbx"
    assert layout.output_model == layout.output / "result.fbx"
    assert layout.process_report == layout.root / "report.json"
