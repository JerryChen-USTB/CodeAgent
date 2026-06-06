from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


ACTIVE_MODEL_DOCS = [
    ROOT / "README.md",
    ROOT / "docs" / "codex" / "prompt.md",
    ROOT / "docs" / "codex" / "implement.md",
    ROOT / "docs" / "design" / "README_设计文档包索引.md",
    ROOT / "docs" / "design" / "01_系统架构设计.md",
    ROOT / "docs" / "design" / "05_核心类与接口设计.md",
    ROOT / "docs" / "design" / "08_关键技术选型与配置设计.md",
    ROOT / "docs" / "design" / "09_运行产物与可复现设计.md",
    ROOT / "docs" / "design" / "10_Benchmark与扩展预留设计.md",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_active_docs_use_gemini_flash_default_model() -> None:
    for path in ACTIVE_MODEL_DOCS:
        text = _read(path)
        assert "anthropic/claude-opus-4.8" not in text, path
        assert "anthropic/claude-sonnet-4.6" not in text, path
        assert "google/gemini-3.5-flash" in text, path


def test_active_docs_do_not_reintroduce_local_secret_file_as_key_source() -> None:
    prompt = _read(ROOT / "docs" / "codex" / "prompt.md")
    plans = _read(ROOT / "docs" / "codex" / "plans.md")

    assert "可以读取该文件获取 API Key" not in prompt
    assert "安全读取根目录 `Software Engineering Project.txt`" not in prompt
    assert "local secret file held only in memory" not in plans
    assert "OPENROUTER_API_KEY" in prompt


def test_readme_stage_subcommand_examples_match_cli_contract() -> None:
    readme = _read(ROOT / "README.md")

    forbidden = [
        "python -m codeagent implement --config",
        "python -m codeagent test --config",
        "python -m codeagent debug --config",
        "python -m codeagent repair --config",
    ]
    for snippet in forbidden:
        assert snippet not in readme

    assert "python -m codeagent implement --project ./repo --requirements requirements.md" in readme
    assert 'python -m codeagent test --project ./repo --test-cmd "pytest -q"' in readme
    assert 'python -m codeagent debug --project ./repo --test-cmd "pytest -q"' in readme
    assert 'python -m codeagent repair --project ./repo --test-cmd "pytest -q"' in readme


def test_personal_ledger_csv_export_visible_spec_is_not_order_contradictory() -> None:
    prd = _read(
        ROOT
        / "benchmark"
        / "selfbuilt"
        / "cases"
        / "02_personal_ledger"
        / "input"
        / "PRD.md"
    )
    acceptance = _read(
        ROOT
        / "benchmark"
        / "selfbuilt"
        / "cases"
        / "02_personal_ledger"
        / "input"
        / "acceptance_criteria.md"
    )

    assert "导出顺序与 `list` 相同" not in prd
    assert "行顺序与 list 一致" not in acceptance
    assert "不要求 CSV 导入导出" in prd
    assert "CSV" not in acceptance
