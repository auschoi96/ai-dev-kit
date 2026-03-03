#!/usr/bin/env python3
"""Run MLflow evaluation for a skill.

Usage:
    python mlflow_eval.py <skill_name> [--filter-category <category>] [--run-name <name>]

Environment Variables:
    DATABRICKS_CONFIG_PROFILE - Databricks CLI profile (default: "DEFAULT")
    MLFLOW_TRACKING_URI - Set to "databricks" for Databricks MLflow
    MLFLOW_EXPERIMENT_NAME - Experiment path (e.g., "/Users/{user}/skill-test")
"""
import sys
import argparse

# Import common utilities
from _common import setup_path, print_result, handle_error


def main():
    parser = argparse.ArgumentParser(description="Run MLflow evaluation for a skill")
    parser.add_argument("skill_name", help="Name of skill to evaluate")
    parser.add_argument("--filter-category", help="Filter by test category")
    parser.add_argument("--run-name", help="Custom MLflow run name")
    args = parser.parse_args()

    setup_path()

    try:
        from skill_test.runners import evaluate_skill

        result = evaluate_skill(
            args.skill_name,
            filter_category=args.filter_category,
            run_name=args.run_name,
        )

        # Convert to standard result format
        if result.get("run_id"):
            result["success"] = True
        else:
            result["success"] = False

        sys.exit(print_result(result))

    except Exception as e:
        sys.exit(handle_error(e, args.skill_name))


if __name__ == "__main__":
    main()
