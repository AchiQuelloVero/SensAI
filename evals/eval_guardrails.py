import argparse
import json
import sys
import time
from pathlib import Path

from core.config import DEFAULT_MODEL
from guardrails.config import GuardrailError, load_config
from guardrails.guard import guard_input, guard_output

CASES_PATH = Path(__file__).parent / "guardrail_cases.json"
OUTPUT_CASES_PATH = Path(__file__).parent / "guardrail_output_cases.json"


def parse_args():
    parser = argparse.ArgumentParser(description="Measure how well the guardrails decide, on messages and on answers.")
    parser.add_argument(
        "-m", "--models",
        nargs="+",
        default=[DEFAULT_MODEL],
        help=f"models to test (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "-c", "--cases",
        default=CASES_PATH,
        help="message cases file (default: evals/guardrail_cases.json)",
    )
    parser.add_argument(
        "-o", "--output-cases",
        default=OUTPUT_CASES_PATH,
        help="answer cases file (default: evals/guardrail_output_cases.json)",
    )
    parser.add_argument(
        "-k", "--kinds",
        nargs="+",
        choices=["input", "output"],
        default=["input", "output"],
        help="check messages, answers or both (default: both)",
    )
    return parser.parse_args()


def load_cases(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        sys.exit(f"error: cannot read test cases {path}: {e}")


def run_model(model, cases, config):
    results = []
    for case in cases:
        case_config = dict(config, scope=case["scope"]) if "scope" in case else config
        start = time.time()
        decision = guard_input(model, case["text"], case_config)
        if decision["category"] == "check_failed":
            sys.exit(f"error: the check itself failed: {decision['reason']}")
        results.append((case, decision, time.time() - start))
    return results


def print_table(header, rows):
    widths = [max(len(str(row[i])) for row in [header, *rows]) for i in range(len(header))]
    print()
    for row in [header, *rows]:
        print("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)))


def run_output(model, cases, config):
    correct = withdrawn = missed = 0
    seconds = 0.0
    for case in cases:
        start = time.time()
        decision = guard_output(model, case["question"], case["answer"], config)
        seconds += time.time() - start
        if decision["category"] == "check_failed":
            sys.exit(f"error: the check itself failed: {decision['reason']}")
        got = decision["action"]
        if got == case["expected"]:
            correct += 1
            continue
        if got == "block":
            withdrawn += 1
        else:
            missed += 1
        print(f"    expected {case['expected']:5} got {got:5} ({decision['reason'][:70]}): {case['answer'][:50]!r}")
    n = len(cases)
    return f"{correct}/{n} ({correct / n:.0%})", withdrawn, missed, f"{seconds / n:.1f}s"


def eval_outputs(models, path, config):
    sets = load_cases(path)
    config = dict(config, output_check=True)
    rows = []
    for model in models:
        for name, cases in sets.items():
            print(f"{model}, answers, {name} set ({len(cases)} cases)...")
            rows.append((model, name, *run_output(model, cases, config)))
    print_table(("model", "set", "correct", "wrongly withdrawn", "missed", "avg time"), rows)


def eval_inputs(models, cases, config):
    rows = []
    for model in models:
        print(f"{model} ({len(cases)} cases)...")
        results = run_model(model, cases, config)
        correct = wrongly_blocked = missed = 0
        for case, decision, _ in results:
            got = decision["action"]
            if got == case["expected"]:
                correct += 1
                continue
            if got == "block":
                wrongly_blocked += 1
            elif case["expected"] == "block":
                missed += 1
            print(f"    expected {case['expected']:5} got {got:5} ({decision['category']}): {case['text'][:60]!r}")
        n = len(cases)
        avg_time = sum(seconds for _, _, seconds in results) / n
        rows.append((model, f"{correct}/{n} ({correct / n:.0%})", wrongly_blocked, missed, f"{avg_time:.1f}s"))

    print_table(("model", "correct", "wrongly blocked", "missed", "avg time"), rows)


def main():
    args = parse_args()
    try:
        config = load_config()
    except GuardrailError as e:
        sys.exit(f"error: {e}")
    if "input" in args.kinds:
        eval_inputs(args.models, load_cases(args.cases), config)
    if "output" in args.kinds:
        if "input" in args.kinds:
            print()
        eval_outputs(args.models, args.output_cases, config)


if __name__ == "__main__":
    main()
