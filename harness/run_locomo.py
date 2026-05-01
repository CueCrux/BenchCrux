#!/usr/bin/env python3
# Licensed under the CueCrux Community Licence v1.0; see LICENCE.md
"""
BenchCrux LoCoMo verification harness.

LoCoMo has 10 conversations × ~150 questions each (≈1986 total). Tenants are
named `__locomo_<sample_id>` (e.g. `__locomo_conv-26`). Many questions per tenant.

USAGE
-----

    export BENCHCRUX_PASSPORT="bcx_..."          # or pass via --passport
    export BENCHCRUX_API_URL="https://api.vaultcrux.io"

    python harness/run_locomo.py --limit 10                  # smoke
    python harness/run_locomo.py --conv conv-26              # single conversation
    python harness/run_locomo.py --llm anthropic --model claude-sonnet-4-6

CONTRACT
--------

Same five public endpoints as run_lme_s.py — only the set_id changes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional, Protocol

try:
    import requests
except ImportError as exc:  # pragma: no cover
    sys.stderr.write("BenchCrux harness requires `requests` — install with `pip install requests`.\n")
    raise SystemExit(1) from exc


API_BASE_DEFAULT = "https://api.vaultcrux.io"
USER_AGENT = "benchcrux-harness/0.1 (+https://github.com/cuecrux/BenchCrux)"
SET_LOCOMO = "locomo"


@dataclass
class Question:
    id: str
    question_type: str
    question_text: str
    tenant_id: str
    snapshot_version: str
    sample_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Engram:
    chunk_id: str
    text: str
    score: float
    session_id: str


class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


def _llm_anthropic(model: str) -> LLMClient:
    import anthropic  # type: ignore
    client = anthropic.Anthropic()

    class _AnthropicLLM:
        def complete(self, prompt: str) -> str:
            resp = client.messages.create(
                model=model, max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(block.text for block in resp.content if block.type == "text")

    return _AnthropicLLM()


def _llm_openai(model: str) -> LLMClient:
    from openai import OpenAI  # type: ignore
    client = OpenAI()

    class _OpenAILLM:
        def complete(self, prompt: str) -> str:
            resp = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}])
            return resp.choices[0].message.content or ""

    return _OpenAILLM()


def _llm_predictions_only() -> LLMClient:
    class _Echo:
        def complete(self, prompt: str) -> str:
            return f"<PROMPT_FOR_VERIFIER_LLM>{prompt}</PROMPT_FOR_VERIFIER_LLM>"
    return _Echo()


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
            raise RuntimeError("BENCHCRUX_PASSPORT not set — see https://github.com/cuecrux/BenchCrux#verification-flow")
        return cls(api_base=os.environ.get("BENCHCRUX_API_URL", API_BASE_DEFAULT), passport=passport)

    def _get(self, path: str) -> Any:
        resp = self.session.get(f"{self.api_base}{path}", timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict, *, auth: bool = True) -> Any:
        headers = {"Content-Type": "application/json"}
        if auth and self.passport:
            headers["x-rcx-capability-token"] = self.passport
        resp = self.session.post(f"{self.api_base}{path}", json=body, headers=headers, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def questions(self, conv_filter: Optional[str] = None) -> Iterator[Question]:
        cursor: Optional[str] = None
        while True:
            qs = f"?cursor={cursor}" if cursor else ""
            page = self._get(f"/v1/benchmarks/{SET_LOCOMO}/questions{qs}")
            data = page["data"] if "data" in page else page
            for stub in data["questions"]:
                detail = self._get(f"/v1/benchmarks/{SET_LOCOMO}/questions/{stub['qid']}")
                detail_data = detail["data"] if "data" in detail else detail
                metadata = detail_data.get("metadata", {}) or {}
                sample_id = metadata.get("sample_id") or stub["qid"].rsplit("-q", 1)[0]
                if conv_filter and sample_id != conv_filter:
                    continue
                yield Question(
                    id=detail_data["qid"],
                    question_type=detail_data.get("questionType") or "?",
                    question_text=detail_data["questionText"],
                    tenant_id=detail_data["tenantId"],
                    snapshot_version=detail_data["snapshotVersion"],
                    sample_id=sample_id,
                    metadata=metadata,
                )
            cursor = data.get("nextCursor")
            if not cursor:
                return

    def gold(self, qid: str) -> Any:
        resp = self._get(f"/v1/benchmarks/{SET_LOCOMO}/questions/{qid}/gold")
        return resp["data"]["gold"] if "data" in resp else resp["gold"]

    def retrieve(self, *, tenant_id: str, query: str) -> dict:
        body = {"tenantId": tenant_id, "query": query}
        resp = self._post("/v1/retrieve", body, auth=True)
        return resp["data"] if "data" in resp else resp


def run(args: argparse.Namespace) -> int:
    client = BenchCruxClient(api_base=args.api_base, passport=args.passport)
    if args.llm == "anthropic":
        llm = _llm_anthropic(args.model or "claude-sonnet-4-6")
    elif args.llm == "openai":
        llm = _llm_openai(args.model or "gpt-4o")
    else:
        llm = _llm_predictions_only()

    out = sys.stdout
    total = 0
    started = time.time()
    for question in client.questions(conv_filter=args.conv):
        if args.limit and total >= args.limit:
            break
        retrieval = client.retrieve(tenant_id=question.tenant_id, query=question.question_text)
        try:
            gold = client.gold(question.id)
        except requests.HTTPError:
            gold = None  # LoCoMo has questions without gold annotations
        chunks = retrieval.get("results", [])
        prompt = (
            f"You are answering a question about a long-form conversation.\n\n"
            f"Engrams retrieved:\n"
            + "\n\n".join(f"[{i+1}] {c.get('content', '')}" for i, c in enumerate(chunks))
            + f"\n\nQuestion: {question.question_text}\nAnswer:"
        )
        prediction = llm.complete(prompt)
        record = {
            "qid": question.id,
            "sample_id": question.sample_id,
            "type": question.question_type,
            "tenant_id": question.tenant_id,
            "snapshot_version": question.snapshot_version,
            "engram_count": len(chunks),
            "prediction": prediction,
            "gold": gold,
        }
        out.write(json.dumps(record) + "\n")
        out.flush()
        total += 1

    elapsed = time.time() - started
    print(f"# done: {total} questions in {elapsed:.1f}s", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api-base", default=os.environ.get("BENCHCRUX_API_URL", API_BASE_DEFAULT))
    p.add_argument("--passport", default=os.environ.get("BENCHCRUX_PASSPORT"))
    p.add_argument("--llm", choices=["anthropic", "openai", "predictions-only"], default="predictions-only")
    p.add_argument("--model", default=None)
    p.add_argument("--limit", type=int, default=None, help="stop after N questions")
    p.add_argument("--conv", default=None, help="restrict to a single conversation (e.g. conv-26)")
    args = p.parse_args()

    if not args.passport:
        sys.stderr.write("error: BENCHCRUX_PASSPORT not set\n")
        return 1
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
