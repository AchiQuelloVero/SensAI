import argparse
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import structured
from cache.check import same_question, stands_alone
from cache.semantic_cache import unit
from core.config import CACHE_EMBED_MODEL, CACHE_THRESHOLD, DEFAULT_MODEL, HOST
from model.ollama import OllamaError
from model.openrouter import PREFIX, OpenRouterClient
from rag.embeddings import Embedder
from rag.errors import RagError
from structured import StructuredOutputError

CASES_PATH = Path(__file__).parent / "cache_cases.json"
THRESHOLDS = [0.6, 0.7, 0.8, 0.9]
PAIR_SETS = {"pairs": "tuning", "new_pairs": "new"}
# free OpenRouter models allow about 20 requests a minute
REMOTE_PAUSE_S = 3.2
# seconds spent waiting for the rate limit, left out of the measured times
paused = [0.0]


def timer():
    return time.time() - paused[0]


def parse_args():
    parser = argparse.ArgumentParser(description="Measure when the semantic cache reuses an answer it should not.")
    parser.add_argument(
        "-m", "--models",
        nargs="+",
        default=[DEFAULT_MODEL],
        help=f"models that make the 'same question?' and 'stands alone?' checks (default: {DEFAULT_MODEL}); "
             "openrouter/<model> runs the checks on OpenRouter for comparison, the app keeps them local",
    )
    parser.add_argument(
        "-t", "--thresholds",
        nargs="+",
        type=float,
        default=THRESHOLDS,
        help="similarity thresholds to compare (default: 0.6 0.7 0.8 0.9)",
    )
    parser.add_argument(
        "-k", "--kinds",
        nargs="+",
        choices=["pairs", "follow_ups"],
        default=["pairs", "follow_ups"],
        help="what to test (default: both)",
    )
    parser.add_argument(
        "-c", "--cases",
        default=CASES_PATH,
        help="test cases file (default: evals/cache_cases.json)",
    )
    return parser.parse_args()


def load_cases(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        sys.exit(f"error: cannot read test cases {path}: {e}")


def strip_fences(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    return text


@contextmanager
def checks_on(model):
    """chat_json always runs on Ollama: for an OpenRouter model, send its calls there for the length of the eval."""
    if not model.startswith(PREFIX):
        yield
        return
    client = OpenRouterClient(model)

    def ask(_model, messages, _schema, _think, _host):
        time.sleep(REMOTE_PAUSE_S)
        paused[0] += REMOTE_PAUSE_S
        for wait in (20, 40, None):
            try:
                reply = client.chat(messages, tools=[], options={"temperature": 0})
                return strip_fences(reply.content), "stop"
            except OllamaError as e:
                if "429" not in str(e) or wait is None:
                    raise StructuredOutputError(str(e))
                print(f"    rate limited, waiting {wait}s...")
                time.sleep(wait)
                paused[0] += wait

    saved = structured.ask_ollama, structured.local_model
    structured.ask_ollama, structured.local_model = ask, lambda m: m
    try:
        yield
    finally:
        structured.ask_ollama, structured.local_model = saved


def similarity(embedder, a, b):
    return float(unit(embedder.embed_one(a)) @ unit(embedder.embed_one(b)))


def check_pairs(model, pairs, scores, lowest):
    """The model's verdict for each pair the cache could reuse at the lowest threshold (the others never reach it)."""
    verdicts, seconds = [], []
    for pair, score in zip(pairs, scores):
        if score < lowest:
            verdicts.append(None)
            continue
        start = timer()
        same, _ = same_question(model, pair["saved"], pair["new"])
        seconds.append(timer() - start)
        verdicts.append(same)
        if same != pair["same"]:
            print(f"    expected same={pair['same']!s:5} got {same!s:5}: {pair['saved']!r} / {pair['new']!r}")
    return verdicts, seconds


def outcome(pairs, scores, verdicts, threshold, verify):
    reused = wrong = missed = 0
    for pair, score, same in zip(pairs, scores, verdicts):
        served = score >= threshold and (not verify or same)
        if served and pair["same"]:
            reused += 1
        elif served:
            wrong += 1
        elif pair["same"]:
            missed += 1
    n_same = sum(p["same"] for p in pairs)
    return f"{reused}/{n_same}", f"{wrong}/{len(pairs) - n_same}", missed


def check_follow_ups(model, follow_ups):
    """How many follow-ups are classified right, per set: {"tuning": [right, total], "new": [...]}, and the times."""
    counts, seconds = {}, []
    for conversation in follow_ups:
        count = counts.setdefault("new" if conversation.get("new") else "tuning", [0, 0])
        for question in conversation["questions"]:
            start = timer()
            alone, why = stands_alone(model, conversation["history"], question["text"])
            seconds.append(timer() - start)
            count[1] += 1
            if alone == question["standalone"]:
                count[0] += 1
            else:
                print(f"    expected standalone={question['standalone']!s:5} got {alone!s:5} ({why}): {question['text']!r}")
    return counts, seconds


def print_table(header, rows):
    widths = [max(len(str(row[i])) for row in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows]:
        print("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)))


def main():
    args = parse_args()
    cases = load_cases(args.cases)
    sets = {name: cases[key] for key, name in PAIR_SETS.items() if key in cases}
    embedder = Embedder(CACHE_EMBED_MODEL, HOST)
    summary = []

    try:
        scores = {}
        if "pairs" in args.kinds:
            for name, pairs in sets.items():
                print(f"similarity of the {len(pairs)} {name} pairs ({CACHE_EMBED_MODEL})...")
                scores[name] = [similarity(embedder, p["saved"], p["new"]) for p in pairs]
                for pair, score in sorted(zip(pairs, scores[name]), key=lambda item: -item[1]):
                    print(f"    {score:.2f}  {'same' if pair['same'] else 'diff'}  {pair['saved']!r} / {pair['new']!r}")
                print(f"\nwithout the model check ({name} pairs):")
                rows = [(f"{t:.2f}", *outcome(pairs, scores[name], [None] * len(pairs), t, False)) for t in args.thresholds]
                print_table(("threshold", "reused", "wrong", "missed"), rows)
                print()

        for model in args.models:
            row = [model]
            with checks_on(model):
                if "pairs" in args.kinds:
                    all_seconds = []
                    for name, pairs in sets.items():
                        print(f"{model}: 'same question?' check on the {name} pairs...")
                        verdicts, seconds = check_pairs(model, pairs, scores[name], min(args.thresholds))
                        all_seconds += seconds
                        rows = [(f"{t:.2f}", *outcome(pairs, scores[name], verdicts, t, True)) for t in args.thresholds]
                        print_table(("threshold", "reused", "wrong", "missed"), rows)
                        print()
                        reused, wrong, _ = outcome(pairs, scores[name], verdicts, CACHE_THRESHOLD, True)
                        row += [wrong, reused]
                    row.append(f"{sum(all_seconds) / len(all_seconds):.1f}s" if all_seconds else "-")
                if "follow_ups" in args.kinds:
                    print(f"{model}: 'stands alone?' check on follow-ups...")
                    counts, seconds = check_follow_ups(model, cases["follow_ups"])
                    row += [f"{counts[name][0]}/{counts[name][1]}" for name in ("tuning", "new") if name in counts]
                    row.append(f"{sum(seconds) / len(seconds):.1f}s")
                    print()
            summary.append(row)
    except (RagError, StructuredOutputError) as e:
        sys.exit(f"error: {e}")

    header = ["model"]
    if "pairs" in args.kinds:
        for name in sets:
            header += [f"wrong ({name})", f"reused ({name})"]
        header.append("same? time")
    if "follow_ups" in args.kinds:
        follow_up_sets = {"new" if c.get("new") else "tuning" for c in cases["follow_ups"]}
        header += [f"follow-ups right ({name})" for name in ("tuning", "new") if name in follow_up_sets]
        header.append("alone? time")
    print(f"at the default threshold {CACHE_THRESHOLD:.2f} with the model check:")
    print_table(header, summary)


if __name__ == "__main__":
    main()
