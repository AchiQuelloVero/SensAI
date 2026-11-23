import time

from cache.check import same_question, stands_alone
from cache.semantic_cache import CacheError, SemanticCache, cache_key
from call_tracking.call_log import log_call
from core.config import AppConfig
from core.contracts import CacheLookup
from guardrails.pii import mask_pii
from rag.embeddings import Embedder
from rag.errors import RagError
from structured import StructuredOutputError

ERRORS = (CacheError, RagError, StructuredOutputError)


def format_seconds(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.1f} s"
    if seconds < 60:
        return f"{seconds:.0f} s"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} min {rest} s"


def has_personal_data(text: str) -> bool:
    """The guardrails keep emails, phones, IBANs and cards out of saved sessions: the cache file must not hold them either."""
    return bool(mask_pii(text)[1])


def is_new_chat(history) -> bool:
    """A later question may depend on the conversation ('and in Paris?'): see stands_alone."""
    return not any(m["role"] in ("user", "assistant") for m in history)


class AnswerCache:
    """Reuses the answer to a question that was already asked. Built from the live config on each call,
    like RagService, so switching the cache on or off applies to the next question.
    If anything fails, the cache is skipped and the model answers as usual."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._standalone_for = None
        self._standalone = None

    def store(self) -> SemanticCache:
        return SemanticCache(self.config.cache_path)

    def stands_alone(self, model: str, history, question: str) -> tuple[bool, str]:
        """Asked once per message: lookup and save both need it."""
        asked = (model, len(history), question)
        if self._standalone_for != asked:
            self._standalone_for = asked
            try:
                self._standalone = stands_alone(model, history, question)
            except StructuredOutputError as e:
                self._standalone = e
        if isinstance(self._standalone, Exception):
            raise self._standalone
        return self._standalone

    def skip_reason(self, model: str, history, question: str) -> tuple[str, str] | None:
        """Why the cache can't be used for this question, as (code, sentence), or None if it can."""
        if not self.config.cache_enabled:
            return "off", ""
        if self.config.rag_enabled:
            return "rag", "not used: RAG is on, answers then depend on your documents"
        if has_personal_data(question):
            return "personal_data", "not used: the question contains personal data"
        if not is_new_chat(history):
            if not self.config.cache_mid_chat:
                return "follow_up", "not used: mid-chat lookup is off, only a chat's first question is cached"
            alone, why = self.stands_alone(model, history, question)
            if not alone:
                return "follow_up", f"not used: the question depends on the earlier messages ({why})"
        return None

    def key(self, model: str, history) -> str:
        system = history[0]["content"] if history and history[0]["role"] == "system" else ""
        return cache_key(model, self.config.cache_embed_model, system)

    def embed(self, question: str):
        return Embedder(self.config.cache_embed_model, self.config.host).embed_one(question)

    def miss(self, start: float, reason: str, note: str, **details) -> CacheLookup:
        if reason != "off":
            log_call(self.config.log_file, {"cache": "miss", "reason": reason, **details})
        return CacheLookup(lookup_s=time.perf_counter() - start, reason=reason, note=note)

    def lookup(self, model: str, history, question: str) -> CacheLookup:
        """The saved answer to the same question, or why there is none."""
        cfg = self.config
        start = time.perf_counter()
        try:
            skip = self.skip_reason(model, history, question)
            if skip is not None:
                return self.miss(start, *skip)
            hit = self.store().closest(self.key(model, history), self.embed(question))
            if hit is None:
                return self.miss(start, "empty", "no saved answer yet for this model and system prompt")
            score = round(hit.score, 2)
            if hit.score < cfg.cache_threshold:
                return self.miss(
                    start, "no_match", f"no similar question saved (closest {score:.2f}, needs {cfg.cache_threshold:.2f})",
                    score=score,
                )
            if cfg.cache_verify:
                same, difference = same_question(model, hit.question, question)
                if not same:
                    return self.miss(
                        start, "rejected", f"a similar question was saved ({score:.2f}) but the model says it differs: {difference}",
                        score=score, saved_question=hit.question, difference=difference,
                    )
            lookup_s = time.perf_counter() - start
            saved_s = max(0.0, hit.duration_s - lookup_s)
            self.store().record_hit(hit.id, saved_s)
        except ERRORS as e:
            return self.miss(start, "error", f"not used: {e}", error=str(e))
        log_call(cfg.log_file, {
            "cache": "hit",
            "score": score,
            "verified": cfg.cache_verify,
            "lookup_s": round(lookup_s, 3),
            "saved_s": round(saved_s, 3),
        })
        return CacheLookup(answer=hit.answer, lookup_s=lookup_s, saved_s=saved_s)

    def forget(self, question: str, answer: str) -> bool:
        try:
            removed = self.store().forget(question, answer)
        except ERRORS as e:
            log_call(self.config.log_file, {"cache": "error", "error": str(e)})
            return False
        if removed:
            log_call(self.config.log_file, {"cache": "forget", "removed": removed})
        return removed > 0

    def save(self, model: str, history, question: str, answer: str, duration_s: float = 0.0,
             used_tools: bool = False) -> str:
        if not answer.strip():
            return ""
        try:
            if self.skip_reason(model, history, question) is not None:
                return ""
        except ERRORS:
            return ""
        if used_tools:
            return "answer not saved: it used tools, so it can change"
        if has_personal_data(answer):
            return "answer not saved: it contains personal data"
        try:
            self.store().add(self.key(model, history), question, self.embed(question), answer, duration_s, model)
        except ERRORS as e:
            log_call(self.config.log_file, {"cache": "error", "error": str(e)})
            return f"answer not saved: {e}"
        return "answer saved"
