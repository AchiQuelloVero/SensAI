import argparse
import json
import sys
import time
from pathlib import Path

from structured import InvalidOutputError, StructuredOutputError, chat_json, load_schema

CASES_PATH = Path(__file__).parent / "structured_cases.json"
SETUPS = [
    ("prompt only", {"use_format": False, "max_retries": 0}),
    ("+ format", {"use_format": True, "max_retries": 0}),
    ("+ retries", {"use_format": True, "max_retries": 2}),
]


def parse_args():
    parser = argparse.ArgumentParser(description="Measure how reliable structured output is.")
    parser.add_argument(
        "-m", "--models",
        nargs="+",
        default=["qwen3:8b"],
        help="models to test (default: qwen3:8b)",
    )
    parser.add_argument(
        "-c", "--cases",
        default=CASES_PATH,
        help="test cases file (default: evals/structured_cases.json)",
    )
    return parser.parse_args()


def load_cases(path):
    try:
        with open(path, encoding="utf-8") as f:
            cases = json.load(f)
        schemas = {case["schema"]: load_schema(case["schema"]) for case in cases}
    except (OSError, ValueError, KeyError, TypeError) as e:
        sys.exit(f"error: cannot read test cases {path}: {e}")
    except StructuredOutputError as e:
        sys.exit(f"error: {e}")
    return cases, schemas


def run_setup(model, cases, schemas, options):
    successes, attempts, seconds = 0, 0, 0.0
    for case in cases:
        stats = {}
        messages = [{"role": "user", "content": case["prompt"]}]
        start = time.time()
        try:
            chat_json(model, messages, schemas[case["schema"]], stats=stats, **options)
            successes += 1
        except InvalidOutputError as e:
            print(f"    failed: {case['prompt'][:50]!r}: {e}")
        except StructuredOutputError as e:
            sys.exit(f"error: {e}")
        attempts += stats["attempts"]
        seconds += time.time() - start
    return successes, attempts, seconds


def main():
    args = parse_args()
    cases, schemas = load_cases(args.cases)
    rows = []
    for model in args.models:
        for name in sorted(schemas):
            schema_cases = [c for c in cases if c["schema"] == name]
            for setup, options in SETUPS:
                print(f"{model} / {name} / {setup} ({len(schema_cases)} cases)...")
                ok, attempts, seconds = run_setup(model, schema_cases, schemas, options)
                n = len(schema_cases)
                rows.append((model, name, setup, f"{ok}/{n} ({ok / n:.0%})", f"{attempts / n:.2f}", f"{seconds / n:.1f}s"))

    header = ("model", "schema", "setup", "valid", "avg attempts", "avg time")
    widths = [max(len(str(row[i])) for row in [header, *rows]) for i in range(len(header))]
    print()
    for row in [header, *rows]:
        print("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)))


if __name__ == "__main__":
    main()
