# Licensed under the CueCrux Community Licence v1.0; see LICENCE.md
"""
Reference scorers for BenchCrux harness output.

LME-S in the original paper uses GPT-4-as-judge. We provide:

- `exact_match` — strict string equality after normalisation. Useful as a floor.
- `normalized_match` — lowercase + whitespace + punctuation stripped, substring check.
- `gpt4_judge_stub` — placeholder showing the prompt template; implement with your
  own OpenAI key. The published Pattern B numbers used GPT-4o (model gpt-4o-2024-08-06).

USAGE
-----

    from harness.scoring import exact_match, normalized_match
    score = normalized_match(prediction="Rust", gold="rust")  # → True
"""
from __future__ import annotations

import re
from typing import Any


def _normalize(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"[\s ]+", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    return text


def exact_match(prediction: str, gold: Any) -> bool:
    """Strict equality after stripping whitespace, case-insensitive.
    For LoCoMo gold answers that are dicts {answer, evidence}, scores against `answer`.
    """
    gold_text = gold.get("answer") if isinstance(gold, dict) else gold
    if gold_text is None:
        return False
    return _normalize(str(prediction)) == _normalize(str(gold_text))


def normalized_match(prediction: str, gold: Any) -> bool:
    """Substring-with-normalisation. Catches '4' ↔ '4 cities (NY, Chicago, Austin, Seattle)'.
    Asymmetric: gold must appear in prediction. This is what LME-S 'lenient' grading uses.
    """
    gold_text = gold.get("answer") if isinstance(gold, dict) else gold
    if gold_text is None:
        return False
    return _normalize(str(gold_text)) in _normalize(str(prediction))


# Stub showing the GPT-4-as-judge prompt that the published Pattern B run used.
# This module deliberately does NOT call OpenAI itself — verifiers wire it up
# with their own API key, model, and rate limits.

GPT4_JUDGE_PROMPT_TEMPLATE = (
    "You are evaluating an answer to a question about a long conversation.\n\n"
    "Question: {question}\n"
    "Gold answer: {gold}\n"
    "Predicted answer: {prediction}\n\n"
    "Is the predicted answer semantically equivalent to the gold answer? "
    "Reply with exactly one word: YES or NO."
)


def gpt4_judge_stub(question: str, prediction: str, gold: Any) -> str:
    """Returns the prompt that should be sent to GPT-4o for judging.

    The Pattern B published numbers used:
        model = "gpt-4o-2024-08-06"
        temperature = 0
        Then parsed YES/NO from the first token.

    This stub returns the prompt; the caller invokes their LLM. Example:

        from openai import OpenAI
        prompt = gpt4_judge_stub(q.question, prediction, gold)
        decision = OpenAI().chat.completions.create(
            model="gpt-4o-2024-08-06", temperature=0,
            messages=[{"role": "user", "content": prompt}]).choices[0].message.content
        is_correct = decision.strip().upper().startswith("YES")
    """
    gold_text = gold.get("answer") if isinstance(gold, dict) else gold
    return GPT4_JUDGE_PROMPT_TEMPLATE.format(
        question=question, gold=gold_text, prediction=prediction)


def score_jsonl(path: str, scorer=normalized_match) -> dict[str, int]:
    """Score a JSONL file produced by run_lme_s.py / run_locomo.py.

    Returns a dict of counts: {total, correct, gold_missing}.
    """
    import json
    counts = {"total": 0, "correct": 0, "gold_missing": 0}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            counts["total"] += 1
            if r.get("gold") is None:
                counts["gold_missing"] += 1
                continue
            if scorer(r.get("prediction", ""), r["gold"]):
                counts["correct"] += 1
    return counts


__all__ = [
    "exact_match",
    "normalized_match",
    "gpt4_judge_stub",
    "score_jsonl",
    "GPT4_JUDGE_PROMPT_TEMPLATE",
]
