#!/usr/bin/env python3
"""Audit test case quality for a skill's ground_truth.yaml.

Runs the semantic grader in diagnostic mode to identify:
  - Assertions too vague (would pass for wrong output)
  - Assertions too strict (exact substring misses valid variants)
  - Missing coverage (response covers content no assertion checks)
  - Suggested new assertions

Usage:
    uv run python .test/scripts/audit_evals.py databricks-metric-views
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import setup_path

repo_root = setup_path()

logger = logging.getLogger(__name__)


_AUDIT_PROMPT = """\
You are an evaluation quality auditor. Analyze the test case assertions below \
and identify issues.

## Test Case
Prompt: {prompt}

## Reference Response
{reference}

## Current Assertions
{assertions_block}

## Instructions
For each assertion, assess:
1. **Vague**: Would this pass for a clearly wrong response? (e.g., checking filename but not content)
2. **Strict**: Is this an exact substring that might miss valid reformulations?
3. **Suggestion**: How to improve it, or "OK" if fine.

Also identify any important content in the reference response that NO assertion covers.

Return a JSON object:
{{
  "assertion_reviews": [
    {{"index": 0, "text": "the assertion", "vague": false, "strict": true, "suggestion": "Use semantic assertion instead"}},
  ],
  "missing_coverage": [
    "The response explains X but no assertion checks for it"
  ]
}}
"""


def main():
    parser = argparse.ArgumentParser(
        description="Audit test case quality for a skill",
        epilog=__doc__,
    )
    parser.add_argument("skill_name", help="Name of the skill to audit")
    parser.add_argument(
        "--judge-model", default=None,
        help="Model for audit LLM calls",
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    judge_model = args.judge_model or os.environ.get("GEPA_JUDGE_LM", "databricks/databricks-claude-sonnet-4-6")

    import yaml
    gt_path = Path(".test/skills") / args.skill_name / "ground_truth.yaml"
    if not gt_path.exists():
        print(f"Error: {gt_path} not found", file=sys.stderr)
        return 1

    with open(gt_path) as f:
        gt_data = yaml.safe_load(f) or {}

    test_cases = gt_data.get("test_cases", [])
    if not test_cases:
        print("No test cases found")
        return 1

    from skill_test.optimize.judges import completion_with_fallback

    print(f"Auditing {len(test_cases)} test cases for: {args.skill_name}\n")

    all_reviews = []
    for tc in test_cases:
        task_id = tc.get("id", "unknown")
        prompt = tc.get("inputs", {}).get("prompt", "")
        reference = tc.get("outputs", {}).get("response", "(no reference)")
        expectations = tc.get("expectations", {})

        assertions_list = []
        for f in expectations.get("expected_facts", []):
            assertions_list.append(f"[FACT] {f}")
        for p in expectations.get("expected_patterns", []):
            if isinstance(p, str):
                assertions_list.append(f"[PATTERN] {p}")
            else:
                assertions_list.append(f"[PATTERN] {p.get('description', p.get('pattern', ''))}")
        for g in expectations.get("guidelines", []):
            assertions_list.append(f"[GUIDELINE] {g}")
        for a in expectations.get("assertions", []):
            assertions_list.append(f"[ASSERTION] {a}")

        if not assertions_list:
            print(f"  {task_id}: no assertions to audit")
            continue

        assertions_block = "\n".join(f"{i}. {a}" for i, a in enumerate(assertions_list))

        audit_prompt = _AUDIT_PROMPT.format(
            prompt=prompt[:500],
            reference=reference[:2000],
            assertions_block=assertions_block,
        )

        print(f"  Auditing: {task_id}...")

        try:
            resp = completion_with_fallback(
                model=judge_model,
                messages=[{"role": "user", "content": audit_prompt}],
                temperature=0,
            )
            raw = resp.choices[0].message.content or "{}"
            raw = raw.strip()
            if raw.startswith("```"):
                import re
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)

            review = json.loads(raw)
            review["task_id"] = task_id
            all_reviews.append(review)

            # Print issues
            for ar in review.get("assertion_reviews", []):
                issues = []
                if ar.get("vague"):
                    issues.append("VAGUE")
                if ar.get("strict"):
                    issues.append("STRICT")
                if issues:
                    print(f"    [{', '.join(issues)}] {ar.get('text', '')[:60]}")
                    print(f"      Suggestion: {ar.get('suggestion', '')}")

            for mc in review.get("missing_coverage", []):
                print(f"    [MISSING] {mc}")

        except Exception as e:
            print(f"    Error auditing {task_id}: {e}")
            all_reviews.append({"task_id": task_id, "error": str(e)})

    # Save results
    output_dir = Path(".test/skills") / args.skill_name
    output_path = output_dir / "eval_audit.json"
    output_path.write_text(json.dumps(all_reviews, indent=2), encoding="utf-8")
    print(f"\nSaved audit results: {output_path}")

    # Summary
    total_vague = sum(
        1 for r in all_reviews
        for ar in r.get("assertion_reviews", [])
        if ar.get("vague")
    )
    total_strict = sum(
        1 for r in all_reviews
        for ar in r.get("assertion_reviews", [])
        if ar.get("strict")
    )
    total_missing = sum(len(r.get("missing_coverage", [])) for r in all_reviews)

    print(f"\nSummary: {total_vague} vague, {total_strict} strict, {total_missing} missing coverage")
    return 0


if __name__ == "__main__":
    sys.exit(main())
