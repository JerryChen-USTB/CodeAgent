from __future__ import annotations

import json
import os
from pathlib import Path

from codeagent import filesystem as fs
from codeagent.config.schema import Stage, TaskConfig
from codeagent.runtime.run_context import create_run_context


def _task_config(project_path) -> TaskConfig:
    return TaskConfig.model_validate(
        {
            "task_id": "demo",
            "stages": ["implement", "test", "debug", "repair"],
            "project_path": project_path,
        }
    )


def _long_readable_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    return Path("\\\\?\\" + str(path.resolve()))


def test_create_run_context_writes_required_tree(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    output_root = tmp_path / "runs"

    context = create_run_context(_task_config(project), output_root=output_root)

    assert context.run_dir.exists()
    for filename in [
        "metadata.json",
        "task_config.yaml",
        "checkpoints.sqlite",
        "transcript.jsonl",
        "decision_trace.jsonl",
        "artifacts_index.json",
        "final_report.md",
    ]:
        assert (context.run_dir / filename).exists()
    for dirname in ["implementation", "testing", "debugging", "repair", "benchmark"]:
        assert (context.run_dir / dirname).is_dir()
    artifact_index = json.loads(
        (context.run_dir / "artifacts_index.json").read_text(encoding="utf-8")
    )
    assert artifact_index == {"run_id": context.run_id, "artifacts": []}


def test_create_run_context_supports_long_windows_output_root(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    output_root = tmp_path / "runs"
    while len(str(output_root / "placeholder_run" / "artifacts_index.json")) < 285:
        output_root = output_root / "deep_segment_for_windows_path_limit"

    context = create_run_context(_task_config(project), output_root=output_root)

    readable_run_dir = _long_readable_path(context.run_dir)
    assert (readable_run_dir / "metadata.json").exists()
    assert (readable_run_dir / "task_config.yaml").exists()
    assert (readable_run_dir / "artifacts_index.json").exists()
    assert (readable_run_dir / "final_report.md").exists()


def test_create_run_context_closes_checkpoint_connection(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    context = create_run_context(_task_config(project), output_root=tmp_path / "runs")

    checkpoint_path = context.run_dir / "checkpoints.sqlite"
    fs.unlink(checkpoint_path)
    assert not fs.exists(checkpoint_path)


def test_run_id_is_unique_and_existing_run_is_not_overwritten(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    output_root = tmp_path / "runs"

    first = create_run_context(_task_config(project), output_root=output_root)
    marker = first.run_dir / "marker.txt"
    marker.write_text("keep me", encoding="utf-8")
    second = create_run_context(_task_config(project), output_root=output_root)

    assert first.run_id != second.run_id
    assert first.run_dir != second.run_dir
    assert marker.read_text(encoding="utf-8") == "keep me"


def test_metadata_records_env_name_not_secret_value(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = _task_config(project)
    config.model.api_key_env = "OPENROUTER_API_KEY"

    context = create_run_context(config, output_root=tmp_path / "runs")
    metadata = json.loads((context.run_dir / "metadata.json").read_text(encoding="utf-8"))

    assert metadata["model"]["api_key_env"] == "OPENROUTER_API_KEY"
    assert "api_key" not in metadata["model"]
    assert "secret" not in json.dumps(metadata).lower()


def test_metadata_ignores_unknown_secret_like_config_fields(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = TaskConfig.model_validate(
        {
            "stages": ["implement"],
            "project_path": project,
            "api_key": "do-not-write-this",
            "secret_token": "do-not-write-this-either",
        }
    )

    context = create_run_context(config, output_root=tmp_path / "runs")
    metadata_text = (context.run_dir / "metadata.json").read_text(encoding="utf-8")
    task_config_text = (context.run_dir / "task_config.yaml").read_text(encoding="utf-8")

    assert "do-not-write" not in metadata_text
    assert "do-not-write" not in task_config_text


def test_task_config_is_serialized_with_canonical_stages(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    context = create_run_context(_task_config(project), output_root=tmp_path / "runs")
    serialized = (context.run_dir / "task_config.yaml").read_text(encoding="utf-8")

    assert "stages:" in serialized
    assert "- implement" in serialized
    assert "- test" in serialized
    assert "- debug" in serialized
    assert "- repair" in serialized
    assert "OPENROUTER_API_KEY" in serialized


def test_stage_directories_map_to_selected_stages(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = TaskConfig.model_validate(
        {"task_id": "demo", "stages": ["testing", "debugging"], "project_path": project}
    )

    context = create_run_context(config, output_root=tmp_path / "runs")

    assert context.stage_dirs[Stage.TEST] == context.run_dir / "testing"
    assert context.stage_dirs[Stage.DEBUG] == context.run_dir / "debugging"
