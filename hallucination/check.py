import time
from dataclasses import dataclass, field

from hallucination.claims import extract_claims
from hallucination.consistency import check_consistency, sample_answers
from hallucination.evidence import collect_evidence
from hallucination.judge import COMMON_KNOWLEDGE, is_flagged, judge_claims

FEEDBACK = """\
A fact-checker found claims in your last answer that nothing you were given supports:
{claims}
Write the answer again: correct or remove these claims, say "I don't know" where you have no evidence, \
and keep the parts that are fine. Write only the new answer, without mentioning the fact-checker."""


@dataclass
class CheckResult:
    verdicts: list[dict] = field(default_factory=list)  # one per claim, see judge_claims
    flagged: list[dict] = field(default_factory=list)  # the verdicts to act on
    samples: int = 0  # answers sampled for the self-consistency check
    duration_s: float = 0.0


def check_answer(chat_client, judge, config, messages, turn_start, response):
    """Check the answer that ends `messages` (the turn starting at messages[turn_start])
    against what the assistant knew. Raises StructuredOutputError or OllamaError when
    a model call fails."""
    start = time.perf_counter()
    question = messages[turn_start]["content"]
    claims = extract_claims(judge, question, messages[-1]["content"])
    evidence = collect_evidence(messages, turn_start, response.excerpts)
    verdicts = judge_claims(judge, claims, evidence)

    samples = 0
    consistency = config["self_consistency"]
    # tool results and documents settle the claims they back; the model's own
    # knowledge is what can waver from one answer to the next
    from_memory = [v for v in verdicts if v["verdict"] == "supported" and is_common_knowledge(v)]
    if consistency["enabled"] and from_memory:
        answers = sample_answers(chat_client, response.request, consistency["samples"], consistency["temperature"])
        samples = len(answers)
        agreements = check_consistency(judge, [v["claim"] for v in from_memory], answers)
        for verdict, agreement in zip(from_memory, agreements):
            verdict["consistency"] = {key: agreement[key] for key in ("verdict", "evidence", "confidence")}

    threshold = config["confidence_threshold"]
    flagged = [v for v in verdicts if is_flagged(v, threshold) or diverges(v, threshold)]
    return CheckResult(verdicts, flagged, samples, round(time.perf_counter() - start, 2))


def is_common_knowledge(verdict):
    return verdict["evidence"].strip().lower() == COMMON_KNOWLEDGE


def diverges(verdict, threshold):
    """The samples disagree with a claim the judge accepted as common knowledge."""
    agreement = verdict.get("consistency")
    return agreement is not None and is_flagged(agreement, threshold)


def problem(verdict):
    """Why a flagged claim was flagged, in a few words."""
    if verdict["verdict"] == "supported":
        return "the samples disagree"
    if verdict["verdict"] == "contradicted":
        cited = " ".join(verdict["evidence"].split())
        return "contradicted by " + ((cited[:77] + "...") if len(cited) > 80 else cited or "the evidence")
    return "not supported by anything you were given"


def regenerate(chat_client, response, answer, flagged, on_text_chunk=None):
    """The answer written again once, from the same messages, with the judge's feedback."""
    claims = "\n".join(f"- {v['claim']} ({problem(v)})" for v in flagged)
    request = [
        *response.request,
        {"role": "assistant", "content": answer},
        {"role": "system", "content": FEEDBACK.format(claims=claims)},
    ]
    return chat_client.chat(request, tools=[], on_text_chunk=on_text_chunk)


def result_view(result):
    """A check as the web page shows it: the flagged claims with why and how sure the judge is."""
    return {
        "claims": len(result.verdicts),
        "samples": result.samples,
        "duration_s": result.duration_s,
        "flagged": [
            {
                "claim": v["claim"],
                "verdict": v["verdict"],
                "problem": problem(v),
                "confidence": v.get("consistency", v)["confidence"],
            }
            for v in result.flagged
        ],
    }


def log_record(result, judge, action, regenerated=None):
    """The call log entry for one check: every claim with its verdict and score, and the action taken."""
    record = {
        "hallucination": "check",
        "judge_model": judge,
        "action": action,
        "claims": len(result.verdicts),
        "flagged": len(result.flagged),
        "samples": result.samples,
        "verdicts": result.verdicts,
        "duration_s": result.duration_s,
    }
    if regenerated is not None:
        record["regenerated"] = {
            "claims": len(regenerated.verdicts),
            "flagged": len(regenerated.flagged),
            "verdicts": regenerated.verdicts,
            "duration_s": regenerated.duration_s,
        }
    return record
