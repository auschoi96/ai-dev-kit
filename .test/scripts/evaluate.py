#!/usr/bin/env python3
"""Standalone skill evaluation (Step 1 of the evaluate → optimize workflow).

Runs test cases WITH and WITHOUT a skill using the real Claude Code agent,
grades assertions using the semantic grader, and generates an HTML report
for human review.

Usage:
    # Evaluate a skill
    uv run python .test/scripts/evaluate.py databricks-metric-views

    # Multiple runs for variance analysis
    uv run python .test/scripts/evaluate.py databricks-metric-views --runs 3

    # Custom agent model and timeout
    uv run python .test/scripts/evaluate.py databricks-metric-views --agent-model claude-sonnet-4-20250514 --agent-timeout 120

    # After reviewing the HTML report, export feedback.json and use it:
    uv run python .test/scripts/optimize.py databricks-metric-views --feedback .test/skills/databricks-metric-views/feedback.json --preset quick
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


def _load_skill_and_tests(skill_name: str) -> tuple[str, list[dict], dict]:
    """Load SKILL.md content, ground_truth test cases, and manifest config."""
    import yaml

    skills_dir = Path("databricks-skills") / skill_name
    skill_path = skills_dir / "SKILL.md"
    if not skill_path.exists():
        raise FileNotFoundError(f"SKILL.md not found: {skill_path}")

    skill_md = skill_path.read_text(encoding="utf-8")

    gt_path = Path(".test/skills") / skill_name / "ground_truth.yaml"
    if not gt_path.exists():
        raise FileNotFoundError(f"ground_truth.yaml not found: {gt_path}")

    with open(gt_path) as f:
        gt_data = yaml.safe_load(f) or {}

    test_cases = gt_data.get("test_cases", [])
    if not test_cases:
        raise ValueError(f"No test cases found in {gt_path}")

    manifest_path = Path(".test/skills") / skill_name / "manifest.yaml"
    manifest = {}
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = yaml.safe_load(f) or {}

    return skill_md, test_cases, manifest


def _ensure_agent_env():
    """Load agent auth env vars into os.environ for the grader API call."""
    import os
    if os.environ.get("ANTHROPIC_BASE_URL"):
        return  # Already loaded
    try:
        from skill_test.agent.executor import _get_agent_env
        env = _get_agent_env()
        for k, v in env.items():
            if k not in os.environ:
                os.environ[k] = v
    except Exception as e:
        logger.warning("Could not load agent env: %s", e)


def _run_evaluation(
    skill_name: str,
    skill_md: str,
    test_cases: list[dict],
    judge_model: str | None,
    agent_model: str | None = None,
    agent_timeout: int = 120,
) -> list[dict]:
    """Run evaluation using the real Claude Code agent on all test cases."""
    from skill_test.agent.executor import run_agent_sync_wrapper
    from skill_test.optimize.semantic_grader import grade_with_without, compute_score

    # Ensure agent auth env vars are available for the grader's Anthropic API call
    _ensure_agent_env()

    results = []

    for tc in test_cases:
        task_id = tc.get("id", tc.get("inputs", {}).get("prompt", "")[:40])
        prompt = tc.get("inputs", {}).get("prompt", "")
        expectations = tc.get("expectations", {})
        reference = tc.get("outputs", {}).get("response", "")

        if not prompt:
            continue

        print(f"  Evaluating: {task_id}...")

        # Run agent WITH skill
        agent_kwargs = {}
        if agent_model:
            agent_kwargs["model"] = agent_model
        if agent_timeout:
            agent_kwargs["timeout_seconds"] = agent_timeout

        print(f"    Running agent WITH skill...")
        with_result = run_agent_sync_wrapper(prompt=prompt, skill_md=skill_md, **agent_kwargs)
        with_response = with_result.response_text

        # Run agent WITHOUT skill
        print(f"    Running agent WITHOUT skill...")
        without_result = run_agent_sync_wrapper(prompt=prompt, skill_md=None, **agent_kwargs)
        without_response = without_result.response_text

        # Build transcript from agent events for grading
        with_transcript = [
            e.__dict__ if hasattr(e, "__dict__") else e
            for e in (with_result.events or [])
        ]

        # Grade with semantic grader (agent-based)
        with_results, without_results, diagnostics = grade_with_without(
            with_response, without_response, expectations,
            judge_model=judge_model,
            with_transcript=with_transcript,
            agent_model=agent_model,
        )

        _, scores = compute_score(diagnostics)

        # Build result for HTML report
        assertion_list = []
        min_len = min(len(with_results), len(without_results))
        for i, r in enumerate(with_results):
            classification = ""
            if i < min_len:
                wr = without_results[i]
                if r.passed and not wr.passed:
                    classification = "POSITIVE"
                elif not r.passed and wr.passed:
                    classification = "REGRESSION"
                elif not r.passed and not wr.passed:
                    classification = "NEEDS_SKILL"
                else:
                    classification = "NEUTRAL"

            assertion_list.append({
                "text": r.text,
                "passed": r.passed,
                "evidence": r.evidence,
                "method": r.method,
                "classification": classification,
            })

        results.append({
            "task_id": task_id,
            "prompt": prompt,
            "with_response": with_response,
            "without_response": without_response,
            "reference": reference,
            "assertions": assertion_list,
            "scores": scores,
        })

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate a skill (Step 1: evaluate → review → optimize)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("skill_name", help="Name of the skill to evaluate")
    parser.add_argument(
        "--judge-model", default=None,
        help="LLM model for semantic grading (default: GEPA_JUDGE_LM env or databricks/databricks-claude-sonnet-4-6)",
    )
    parser.add_argument(
        "--runs", type=int, default=1,
        help="Number of evaluation runs for variance analysis (default: 1)",
    )
    parser.add_argument(
        "--agent-model", default=None,
        help="Claude model for agent execution (default: uses claude-agent-sdk default)",
    )
    parser.add_argument(
        "--agent-timeout", type=int, default=120,
        help="Timeout in seconds for each agent run (default: 120)",
    )
    parser.add_argument(
        "--no-html", action="store_true",
        help="Skip HTML report generation",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    judge_model = args.judge_model or os.environ.get("GEPA_JUDGE_LM", None)

    print(f"\nEvaluating skill: {args.skill_name}")
    print(f"  Agent model: {args.agent_model or '(default)'}")
    print(f"  Agent timeout: {args.agent_timeout}s")
    print(f"  Judge model: {judge_model or '(default)'}")
    print(f"  Runs: {args.runs}")
    print()

    try:
        skill_md, test_cases, manifest = _load_skill_and_tests(args.skill_name)
        print(f"Loaded {len(test_cases)} test cases")
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Run evaluation
    all_run_results = []
    for run_num in range(args.runs):
        if args.runs > 1:
            print(f"\n--- Run {run_num + 1}/{args.runs} ---")

        results = _run_evaluation(
            args.skill_name, skill_md, test_cases, judge_model,
            agent_model=args.agent_model,
            agent_timeout=args.agent_timeout,
        )
        all_run_results.append(results)

    # Use first run for report (or aggregate if multiple)
    results = all_run_results[0]

    # Compute aggregate scores
    all_scores = [r["scores"] for r in results]
    aggregate = {}
    if all_scores:
        for key in all_scores[0]:
            vals = [s[key] for s in all_scores if key in s]
            aggregate[key] = sum(vals) / len(vals) if vals else 0.0

    # If multiple runs, compute variance
    if args.runs > 1:
        import statistics
        per_task_finals = []
        for task_idx in range(len(test_cases)):
            task_scores = [run[task_idx]["scores"]["final"] for run in all_run_results if task_idx < len(run)]
            if len(task_scores) > 1:
                per_task_finals.append({
                    "task_id": results[task_idx]["task_id"],
                    "mean": statistics.mean(task_scores),
                    "stddev": statistics.stdev(task_scores),
                })
        if per_task_finals:
            print("\nVariance analysis (per-task final scores):")
            for pf in per_task_finals:
                print(f"  {pf['task_id']}: {pf['mean']:.3f} +/- {pf['stddev']:.3f}")

    # Save evaluation.json
    output_dir = Path(".test/skills") / args.skill_name
    output_dir.mkdir(parents=True, exist_ok=True)

    eval_path = output_dir / "evaluation.json"
    eval_data = {
        "skill_name": args.skill_name,
        "agent_model": args.agent_model,
        "agent_timeout": args.agent_timeout,
        "judge_model": judge_model,
        "runs": args.runs,
        "aggregate_scores": aggregate,
        "task_results": results,
    }
    eval_path.write_text(json.dumps(eval_data, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved evaluation results: {eval_path}")

    # Generate HTML report
    if not args.no_html:
        from skill_test.optimize.html_report import generate_report
        report_path = output_dir / "report.html"
        generate_report(args.skill_name, results, report_path, aggregate)
        print(f"Generated HTML report: {report_path}")
        print("\nReview the report, then save feedback and run:")
        print(f"  uv run python .test/scripts/optimize.py {args.skill_name} --feedback {output_dir}/feedback.json --preset quick")

    # Print summary
    print(f"\n{'='*60}")
    print(f"Evaluation Summary: {args.skill_name}")
    print(f"{'='*60}")
    for key, val in aggregate.items():
        print(f"  {key}: {val:.3f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
