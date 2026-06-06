from __future__ import annotations

from codeagent.config.cli_mapping import task_config_from_run_options


def test_run_options_accept_repeated_requirements_materials(tmp_path) -> None:
    project = tmp_path / "workspace"
    project.mkdir()
    prd = tmp_path / "input" / "PRD.md"
    acceptance = tmp_path / "input" / "acceptance_criteria.md"
    prd.parent.mkdir()
    prd.write_text("# PRD\n", encoding="utf-8")
    acceptance.write_text("# Acceptance\n", encoding="utf-8")

    config = task_config_from_run_options(
        project=project,
        stages="implement,test",
        output_dir=tmp_path / "runs",
        test_cmd="python -m pytest -q",
        requirements=[prd, acceptance],
        model_name="google/gemini-3.5-flash",
        auto_approve=True,
    )

    assert [material.path for material in config.input_materials] == [
        prd.resolve(),
        acceptance.resolve(),
    ]
    assert [material.material_type for material in config.input_materials] == [
        "requirements",
        "requirements",
    ]
    assert config.test_command.command == "python -m pytest -q"
    assert config.model.model_name == "google/gemini-3.5-flash"
    assert config.permissions.approval_mode == "auto"
