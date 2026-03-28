#!/usr/bin/env python3
"""Pre-focus SKILL.md before GEPA optimization.

Augments a skill's SKILL.md with user-specified focus areas so the optimizer
seed already reflects the user's priorities.
"""

from __future__ import annotations


def apply_focus(
    skill_name: str,
    focus_areas: list[str],
    gen_model: str,
) -> None:
    """Augment SKILL.md with focus-area instructions using an LLM.

    Loads the SKILL.md, sends it with the focus areas to the LLM for
    augmentation, and writes the modified content back in place.
    """
    import os
    from urllib.parse import urlparse

    import litellm
    from skill_test.optimize.utils import find_skill_md
    from skill_test.optimize.judges import _to_litellm_model
    from skill_test.agent.executor import _get_agent_env

    # Load agent env (ANTHROPIC_BASE_URL, DATABRICKS_TOKEN, etc.) same as runner.py
    _agent_env = _get_agent_env()
    for _k, _v in _agent_env.items():
        if _k.startswith(("DATABRICKS_", "MLFLOW_", "ANTHROPIC_")):
            os.environ.setdefault(_k, _v)

    # Auto-derive AI Gateway URL from ANTHROPIC_BASE_URL if not set
    if not os.environ.get("DATABRICKS_AI_GATEWAY_URL"):
        _anthropic_base = os.environ.get("ANTHROPIC_BASE_URL", "")
        if "ai-gateway.cloud.databricks.com" in _anthropic_base:
            _parsed = urlparse(_anthropic_base)
            _gw = f"{_parsed.scheme}://{_parsed.netloc}/mlflow/v1"
            os.environ["DATABRICKS_AI_GATEWAY_URL"] = _gw

    skill_path = find_skill_md(skill_name)
    if skill_path is None:
        print(f"  Warning: Could not find SKILL.md for '{skill_name}', skipping focus.")
        return

    original_content = skill_path.read_text()
    focus_list = "\n".join(f"- {area}" for area in focus_areas)

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert technical writer for Databricks skill documents. "
                "Your task is to augment an existing SKILL.md document to emphasize "
                "specific focus areas requested by the user.\n\n"
                "Rules:\n"
                "- PRESERVE all existing content — do not remove sections, APIs, or examples\n"
                "- ADD or STRENGTHEN content related to the focus areas\n"
                "- You may add new sections, examples, or tips that address the focus areas\n"
                "- You may add emphasis (bold, callouts) to existing content relevant to the focus areas\n"
                "- Keep the same markdown structure and frontmatter\n"
                "- Be concise — add targeted content, not verbose filler\n"
                "- Return the COMPLETE modified SKILL.md (not a diff)"
            ),
        },
        {
            "role": "user",
            "content": (
                f"## Current SKILL.md for '{skill_name}':\n\n"
                f"{original_content}\n\n"
                f"## Focus areas to emphasize:\n{focus_list}\n\n"
                "Return the complete, augmented SKILL.md."
            ),
        },
    ]

    print(f"  Applying focus areas to {skill_name} SKILL.md...")
    for area in focus_areas:
        print(f"    - {area}")

    try:
        litellm_model, base_url, api_key = _to_litellm_model(gen_model)
        call_kwargs: dict = dict(
            model=litellm_model,
            messages=messages,
            temperature=0.7,
        )
        if base_url:
            call_kwargs["base_url"] = base_url
        if api_key:
            call_kwargs["api_key"] = api_key
        resp = litellm.completion(**call_kwargs)
        augmented_content = resp.choices[0].message.content or ""
    except Exception as e:
        print(f"  Error during focus LLM call: {e}")
        print("  Skipping focus — original SKILL.md unchanged.")
        return

    # Strip markdown fences that LLMs commonly wrap output in
    augmented_content = augmented_content.strip()
    if augmented_content.startswith("```"):
        lines = augmented_content.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        augmented_content = "\n".join(lines)

    if not augmented_content or len(augmented_content) < 50:
        print("  Warning: LLM returned empty or trivially short content. Skipping.")
        return

    skill_path.write_text(augmented_content)

    orig_lines = original_content.splitlines()
    new_lines = augmented_content.splitlines()
    delta = len(new_lines) - len(orig_lines)
    sign = "+" if delta >= 0 else ""
    print(f"  Focus applied: {skill_path}")
    print(f"    Lines: {len(orig_lines)} -> {len(new_lines)} ({sign}{delta})")
    print(f"    Chars: {len(original_content):,} -> {len(augmented_content):,}")
