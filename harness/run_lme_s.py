#!/usr/bin/env python3
# Licensed under the CueCrux Community Licence v1.0; see LICENCE.md
"""
BenchCrux LME-S verification harness.

Reproduces the published Pattern B numbers on LongMemEval-S by calling the same
retrieval pipeline that produced them and letting the verifier supply their own
LLM for prompt completion and their own scorer for grading.

USAGE
-----

    export BENCHCRUX_PASSPORT="bcx_..."          # 30-day verification passport
    export BENCHCRUX_API_URL="https://api.vaultcrux.io"

    python harness/run_lme_s.py --limit 10       # smoke test
    python harness/run_lme_s.py                  # full 500

    # Bring your own LLM:
    python harness/run_lme_s.py --llm anthropic --model claude-sonnet-4-6
    python harness/run_lme_s.py --llm openai    --model gpt-4o

CONTRACT
--------

This script depends on three public endpoints on the VaultCrux API:

    GET  /v1/benchmarks/manifest
    GET  /v1/benchmarks/lme-s/questions
    GET  /v1/benchmarks/lme-s/questions/:id
    GET  /v1/benchmarks/lme-s/questions/:id/gold
    POST /v1/retrieve                  (verification-passport-gated)
    POST /v1/receipts/verify           (public, no passport required)

The shapes below are the consumer-side contract; they match the spec in the
verification-browser ExecPlan and will land in production progressively across
milestones M1-M3 of that plan.

DEPENDENCIES
------------

Standard library plus `requests`. Bring your own LLM SDK if you want one of the
built-in --llm wrappers; otherwise pass --predictions-only and call the LLM
yourself in a downstream script.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Iterable, Iterator, Optional, Protocol

try:
    import requests
except ImportError as exc:  # pragma: no cover
    sys.stderr.write("BenchCrux harness requires `requests` — install with `pip install requests`.\n")
    raise SystemExit(1) from exc


API_BASE_DEFAULT = "https://api.vaultcrux.io"
USER_AGENT = "benchcrux-harness/0.1 (+https://github.com/cuecrux/BenchCrux)"
SET_LME_S = "lme-s"


# ---------------------------------------------------------------------------
# Response shapes (the public API contract)
# ---------------------------------------------------------------------------

@dataclass
class Question:
    id: str
    type: str  # one of MS, TR, KU, SSA, SSU, SSP
    question_text: str
    tenant_id: str
    snapshot_version: str


@dataclass
class Engram:
    chunk_id: str
    text: str          # post-enrichment, byte-identical to /sessions response
    score: float
    session_id: str
    occurred_at: Optional[str] = None
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None


@dataclass
class CrownReceipt:
    receipt_id: str
    chain_head: str
    signed_at: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    engrams: list[Engram]
    receipt: CrownReceipt
    dispatched_template: Optional[dict[str, Any]]
    recommended_prompt: str
    snapshot_version: str


# ---------------------------------------------------------------------------
# LLM interface — verifier supplies their own
# ---------------------------------------------------------------------------

class LLMClient(Protocol):
    """Minimal interface — anything that turns a prompt into a string works."""
    def complete(self, prompt: str) -> str: ...


def _llm_anthropic(model: str) -> LLMClient:
    """Wrapper for the Anthropic SDK. Requires `pip install anthropic` and ANTHROPIC_API_KEY."""
    import anthropic  # type: ignore  # imported lazily so harness has zero hard deps
    client = anthropic.Anthropic()

    class _AnthropicLLM:
        def complete(self, prompt: str) -> str:
            resp = client.messages.create(
                model=model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(block.text for block in resp.content if block.type == "text")

    return _AnthropicLLM()


def _llm_openai(model: str) -> LLMClient:
    """Wrapper for the OpenAI SDK. Requires `pip install openai` and OPENAI_API_KEY."""
    from openai import OpenAI  # type: ignore
    client = OpenAI()

    class _OpenAILLM:
        def complete(self, prompt: str) -> str:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content or ""

    return _OpenAILLM()


def _llm_predictions_only() -> LLMClient:
    """No-op LLM — emits the prompt as the prediction so verifiers can pipe to their own LLM."""

    class _Echo:
        def complete(self, prompt: str) -> str:
            return f"<PROMPT_FOR_VERIFIER_LLM>{prompt}</PROMPT_FOR_VERIFIER_LLM>"

    return _Echo()


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class BenchCruxClient:
    def __init__(self, api_base: str, passport: Optional[str], session: Optional[requests.Session] = None):
        self.api_base = api_base.rstrip("/")
        self.passport = passport
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    @classmethod
    def from_env(cls) -> "BenchCruxClient":
        passport = os.environ.get("BENCHCRUX_PASSPORT")
        if not passport:
            raise RuntimeError("BENCHCRUX_PASSPORT not set — request a verification passport from verify@cuecrux.io")
        return cls(api_base=os.environ.get("BENCHCRUX_API_URL", API_BASE_DEFAULT), passport=passport)

    def _get(self, path: str, *, auth: bool = False) -> Any:
        headers = {"Authorization": f"Bearer {self.passport}"} if auth and self.passport else {}
        resp = self.session.get(f"{self.api_base}{path}", headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict[str, Any], *, auth: bool = True) -> Any:
        headers = {"Content-Type": "application/json"}
        if auth and self.passport:
            headers["Authorization"] = f"Bearer {self.passport}"
        resp = self.session.post(f"{self.api_base}{path}", json=body, headers=headers, timeout=60)
        resp.raise_for_status()
        return resp.json()

    # -- public read endpoints (no passport required for the read paths) --

    def manifest(self) -> dict[str, Any]:
        return self._get("/v1/benchmarks/manifest")

    def questions(self, set_id: str = SET_LME_S) -> Iterator[Question]:
        cursor: Optional[str] = None
        while True:
            qs = f"?cursor={cursor}" if cursor else ""
            page = self._get(f"/v1/benchmarks/{set_id}/questions{qs}")
            for stub in page["questions"]:
                detail = self._get(f"/v1/benchmarks/{set_id}/questions/{stub['id']}")
                yield Question(
                    id=detail["id"],
                    type=detail["type"],
                    question_text=detail["question_text"],
                    tenant_id=detail["tenant_id"],
                    snapshot_version=detail["snapshot_version"],
                )
            cursor = page.get("next_cursor")
            if not cursor:
                return

    def gold(self, set_id: str, question_id: str) -> Any:
        return self._get(f"/v1/benchmarks/{set_id}/questions/{question_id}/gold")["gold"]

    # -- passport-gated endpoints --

    def retrieve(self, *, tenant_id: str, query: str, lane: str = "fusion") -> RetrievalResult:
        body = {"tenant_id": tenant_id, "query": query, "lane": lane}
        payload = self._post("/v1/retrieve", body, auth=True)
        engrams = [Engram(**e) for e in payload["engrams"]]
        receipt_raw = payload["receipt"]
        return RetrievalResult(
            engrams=engrams,
            receipt=CrownReceipt(
                receipt_id=receipt_raw["receipt_id"],
                chain_head=receipt_raw["chain_head"],
                signed_at=receipt_raw["signed_at"],
                raw=receipt_raw,
            ),
            dispatched_template=payload.get("dispatched_template"),
            recommended_prompt=payload["recommended_prompt"],
            snapshot_version=payload["snapshot_version"],
        )

    def verify_receipt(self, receipt: CrownReceipt) -> bool:
        # Public verify endpoint — no passport required, restricted to receipts
        # from public benchmark sets. Returns valid + chain + signature proof.
        # See methodology/ chain proof format v1.
        body = {"receipt_hash": receipt.chain_head or receipt.receipt_id}
        envelope = self._post("/v1/receipts/verify", body, auth=False)
        data = envelope.get("data") if isinstance(envelope, dict) else None
        return bool(data and data.get("valid"))


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    client = BenchCruxClient(api_base=args.api_base, passport=args.passport)

    if args.llm == "anthropic":
        llm = _llm_anthropic(args.model or "claude-sonnet-4-6")
    elif args.llm == "openai":
        llm = _llm_openai(args.model or "gpt-4o")
    elif args.llm == "predictions-only":
        llm = _llm_predictions_only()
    else:
        raise ValueError(f"unknown --llm: {args.llm}")

    manifest = client.manifest()
    snapshot = next((s for s in manifest["sets"] if s["id"] == SET_LME_S), None)
    if snapshot is None:
        raise RuntimeError("LME-S not present in /v1/benchmarks/manifest")
    print(f"# snapshot_version={snapshot['snapshot_version']} tenants={snapshot['tenant_count']} questions={snapshot['question_count']}", file=sys.stderr)

    out = sys.stdout
    correct_receipts = 0
    total = 0
    started = time.time()
    for question in client.questions(SET_LME_S):
        if args.limit and total >= args.limit:
            break
        result = client.retrieve(tenant_id=question.tenant_id, query=question.question_text)
        receipt_ok = client.verify_receipt(result.receipt)
        correct_receipts += int(receipt_ok)
        prediction = llm.complete(result.recommended_prompt)
        gold = client.gold(SET_LME_S, question.id)
        record = {
            "qid": question.id,
            "type": question.type,
            "tenant_id": question.tenant_id,
            "snapshot_version": result.snapshot_version,
            "receipt_id": result.receipt.receipt_id,
            "receipt_valid": receipt_ok,
            "engram_count": len(result.engrams),
            "prediction": prediction,
            "gold": gold,
        }
        out.write(json.dumps(record) + "\n")
        out.flush()
        total += 1

    elapsed = time.time() - started
    print(
        f"# done: {total} questions, {correct_receipts}/{total} valid receipts, {elapsed:.1f}s",
        file=sys.stderr,
    )
    print("# scoring is intentionally out-of-scope for this script — see harness/scoring/ for the reference scorer", file=sys.stderr)
    return 0 if correct_receipts == total else 2


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api-base", default=os.environ.get("BENCHCRUX_API_URL", API_BASE_DEFAULT))
    p.add_argument("--passport", default=os.environ.get("BENCHCRUX_PASSPORT"))
    p.add_argument("--llm", choices=["anthropic", "openai", "predictions-only"], default="predictions-only")
    p.add_argument("--model", default=None)
    p.add_argument("--limit", type=int, default=None, help="stop after N questions (smoke test)")
    args = p.parse_args()

    if not args.passport:
        sys.stderr.write("error: BENCHCRUX_PASSPORT not set and --passport not provided\n")
        sys.stderr.write("       request a verification passport from verify@cuecrux.io\n")
        return 1
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
