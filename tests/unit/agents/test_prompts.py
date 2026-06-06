from __future__ import annotations

from codeagent.agents.prompts import PromptRegistry


def test_default_prompt_registry_contains_required_agent_roles() -> None:
    registry = PromptRegistry.default()

    assert set(registry.names()) == {
        "planner",
        "coder",
        "test_designer",
        "test_writer",
        "debugger",
        "repairer",
        "verifier",
        "benchmark_runner",
    }


def test_agent_prompts_contain_safety_schema_and_audit_rules() -> None:
    registry = PromptRegistry.default()

    for name in registry.names():
        prompt = registry.get(name)
        system = prompt.system.lower()
        assert prompt.allowed_tools
        assert prompt.output_schema
        assert "Inputs:" in prompt.system
        assert "Allowed tools:" in prompt.system
        assert "Output schema:" in prompt.system
        assert "Verification:" in prompt.system
        assert "Failure behavior:" in prompt.system
        assert "patch-first" in system
        assert "hidden" in system
        assert "oracle" in system
        assert "secret" in system
        assert "schema" in system
        assert "do not claim tests pass" in system
        assert "audit" in system
        assert "simplified chinese" in system
        assert "简体中文" in prompt.system
        assert "Preserve code identifiers" in prompt.system


def test_prompt_registry_renders_role_context_without_missing_tokens() -> None:
    registry = PromptRegistry.default()

    rendered = registry.render(
        "coder",
        stage="implement",
        task_summary="Implement the calculator API",
    )

    assert "Coder" in rendered
    assert "implement" in rendered
    assert "Implement the calculator API" in rendered
    assert "{" not in rendered
    assert "}" not in rendered
