"""Regression guards for project-neutral prompts and current business names."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _prompt_surfaces():
    return "\n".join(
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in (
            "app/api/stage_methods.py",
            "app/services/agent_template_builder.py",
            "skills/openwebui-system-prompt.md",
            "skills/openwebui-system-prompt.en.md",
            "skills/openapi.yaml",
            "harnoq_mcp/server.py",
        )
    )


def test_prompts_do_not_carry_the_current_test_project_examples():
    prompts = _prompt_surfaces().casefold()
    for leaked_example in (
        "photos -> assets",
        "asset ledger",
        "a media system by asset identity",
        "too many images",
        "照片可以合理地叫",
        "照片 → 资产",
    ):
        assert leaked_example.casefold() not in prompts


def test_retired_item_prefix_is_absent_from_prompt_and_ui_surfaces():
    surfaces = _prompt_surfaces() + "\n" + "\n".join(
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in (
            "app/templates/delivery_items.html",
            "app/templates/project_detail.html",
            "app/static/app.js",
            "app/static/style.css",
        )
    )
    retired_prefix = "DE" + "LIV"
    assert f"{retired_prefix}-*" not in surfaces
    assert f"-{retired_prefix}-" not in surfaces
    assert "deliv-" not in surfaces
