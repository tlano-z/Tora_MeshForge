from __future__ import annotations

from typing import Any


DEFAULT_LANGUAGE = "ja"
SUPPORTED_LANGUAGES = ("ja", "en")


_TEXT: dict[str, dict[str, str]] = {
    "ja": {
        "workflow_name_quality_search": "品質探索",
        "workflow_name_direct_retopology": "単一ターゲット生成",
        "workflow_tooltip_quality_search": (
            "元モデルを検査し、指定した複数の三角形数で生成・評価して、比較用の3候補を提示します。"
        ),
        "workflow_tooltip_direct_retopology": (
            "元モデルを検査し、指定した1つの三角形数でUV・Base Color・Shape Normalを含む成果物を生成します。"
        ),
        "review_none": "—",
        "review_inspection": "元三角形数 / 警告 / 対象外機能",
        "review_static_fbx_round_trip": "形状 / 階層 / マテリアル / テクスチャ",
        "review_fast_optimize": "輪郭 / 細部 / 既存UV・マテリアル",
        "review_runtime_rebuild": "UV / Base Color / 境界 / 細部",
        "review_surface_retopology": "3種プレビュー / UV / Normal / Runtime",
        "review_triangle_sweep": "SOURCE比較 / 3候補 / UV / Normal",
        "monitor_not_started": "未実行",
        "monitor_manual": "個別操作",
        "monitor_inspection": "元モデル検査",
        "monitor_sweep": "候補生成・評価",
        "monitor_single": "生成・評価",
        "monitor_manual_process": "個別処理",
        "monitor_review": "確認可能",
        "status_waiting": "待機",
        "status_running": "実行中",
        "status_done": "完了",
        "status_failed": "失敗",
        "status_cancelled": "取消",
        "duration_approx": "目安 {duration}",
        "duration_range": "{minimum}～{maximum}",
        "duration_elapsed_remaining": "経過 {elapsed} / 残り目安 {remaining}",
        "duration_elapsed_over": "経過 {elapsed} / 当初目安を超過",
        "duration_finished": "終了 {elapsed} / 当初目安 {duration}",
        "duration_not_running": "未実行",
        "long_phase_uv": "UV探索中（数分かかることがあります）",
        "invalid_input_title": "入力モデルを確認してください",
        "invalid_input": "存在するモデルファイルを選択してください。",
        "invalid_output_title": "出力先を確認してください",
        "invalid_output": "出力FBXの保存先を選択してください。",
        "invalid_sweep_output": "出力FBX名を選択してください。その名前からSweep出力フォルダを作成します。",
        "invalid_sweep_title": "Sweep候補を確認してください",
        "workflow_inspection_phase": "元モデル検査",
        "workflow_processing_phase": "生成・評価",
        "workflow_completed": "{workflow} 完了",
        "operation_failed_title": "処理に失敗しました",
        "operation_cancelled": "処理をキャンセルしました。ログを確認してください。",
        "cancelling": "キャンセルしています…",
    },
    "en": {
        "workflow_name_quality_search": "Quality Sweep",
        "workflow_name_direct_retopology": "Single Target Build",
        "workflow_tooltip_quality_search": (
            "Inspects the source, builds and evaluates all triangle candidates, and presents three comparison outputs."
        ),
        "workflow_tooltip_direct_retopology": (
            "Inspects the source and builds one target with UVs, Base Color, and Shape Normal."
        ),
        "review_none": "—",
        "review_inspection": "Source triangles / warnings / unsupported features",
        "review_static_fbx_round_trip": "Geometry / hierarchy / material / texture",
        "review_fast_optimize": "Silhouette / details / retained UVs and materials",
        "review_runtime_rebuild": "UV / Base Color / boundaries / details",
        "review_surface_retopology": "Three previews / UV / Normal / Runtime",
        "review_triangle_sweep": "SOURCE comparison / three candidates / UV / Normal",
        "monitor_not_started": "Not started",
        "monitor_manual": "Manual operation",
        "monitor_inspection": "Inspect source",
        "monitor_sweep": "Build and evaluate candidates",
        "monitor_single": "Build and evaluate",
        "monitor_manual_process": "Manual process",
        "monitor_review": "Ready to review",
        "status_waiting": "Waiting",
        "status_running": "Running",
        "status_done": "Done",
        "status_failed": "Failed",
        "status_cancelled": "Cancelled",
        "duration_approx": "Approx. {duration}",
        "duration_range": "{minimum}–{maximum}",
        "duration_elapsed_remaining": "Elapsed {elapsed} / approx. remaining {remaining}",
        "duration_elapsed_over": "Elapsed {elapsed} / over initial estimate",
        "duration_finished": "Finished in {elapsed} / initial estimate {duration}",
        "duration_not_running": "Not running",
        "long_phase_uv": "UV search (this phase can take several minutes)",
        "invalid_input_title": "Check the input model",
        "invalid_input": "Select an existing model file.",
        "invalid_output_title": "Check the output path",
        "invalid_output": "Select an output FBX path.",
        "invalid_sweep_output": "Select an output FBX name; its stem will be used for the Sweep directory.",
        "invalid_sweep_title": "Check the Sweep candidates",
        "workflow_inspection_phase": "Inspect source",
        "workflow_processing_phase": "Build and evaluate",
        "workflow_completed": "{workflow} complete",
        "operation_failed_title": "Operation failed",
        "operation_cancelled": "Operation cancelled. Review the log for details.",
        "cancelling": "Cancelling…",
    },
}


REVIEW_TEXT_KEYS = {
    "inspection": "review_inspection",
    "static_fbx_round_trip": "review_static_fbx_round_trip",
    "fast_optimize": "review_fast_optimize",
    "runtime_rebuild": "review_runtime_rebuild",
    "surface_retopology": "review_surface_retopology",
    "triangle_sweep": "review_triangle_sweep",
}


def normalize_language(language: str | None) -> str:
    """Return a supported guidance language, defaulting to Japanese."""
    return language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def text(language: str | None, key: str, **values: Any) -> str:
    """Return localized user-facing text."""
    selected = normalize_language(language)
    template = _TEXT[selected].get(key, _TEXT["en"].get(key, key))
    return template.format(**values)


def workflow_name(language: str | None, workflow: str) -> str:
    """Return the compact name of one of the two normal workflows."""
    return text(language, f"workflow_name_{workflow}")


def workflow_tooltip(language: str | None, workflow: str) -> str:
    """Return optional detail without adding prose to the main layout."""
    return text(language, f"workflow_tooltip_{workflow}")


def review_checklist(language: str | None, operation: str | None) -> str:
    """Return a compact list of artifacts to review after an operation."""
    if operation is None:
        return text(language, "review_none")
    key = REVIEW_TEXT_KEYS.get(operation)
    return text(language, key) if key else text(language, "review_none")
