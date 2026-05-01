#!/usr/bin/env python3
# Licensed under the CueCrux Community Licence v1.0; see LICENCE.md
"""
Minimal OpenAI example — verify a single LME-S question end-to-end.

Mirrors anthropic_example.py with the OpenAI SDK. Useful for verifiers running
on GPT-class models.

Usage:
    pip install requests openai
    export OPENAI_API_KEY="..."
    python examples/openai_example.py [--qid <8-char qid>]
"""
import argparse
import base64
import json
import os
import sys

import requests
from openai import OpenAI

API = os.environ.get("BENCHCRUX_API_URL", "https://api.vaultcrux.io")
DEFAULT_QID = "001be529"


def _normalize(t: str) -> str:
    return " ".join(str(t).lower().strip().split())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--qid", default=DEFAULT_QID)
    p.add_argument("--model", default="gpt-4o")
    args = p.parse_args()

    mint = requests.post(
        f"{API}/v1/passports/verification",
        json={"set_id": "lme-s"},
        timeout=10,
    ).json()["data"]
    passport = base64.urlsafe_b64encode(
        json.dumps(mint["passport"]).encode()
    ).rstrip(b"=").decode()
    print(f"Minted passport {mint['token_id']} (expires {mint['expires_at']})", file=sys.stderr)

    q = requests.get(f"{API}/v1/benchmarks/lme-s/questions/{args.qid}").json()["data"]
    gold = requests.get(f"{API}/v1/benchmarks/lme-s/questions/{args.qid}/gold").json()["data"]["gold"]

    res = requests.post(
        f"{API}/v1/retrieve",
        headers={"x-rcx-capability-token": passport, "content-type": "application/json"},
        json={"query": q["questionText"], "tenantId": q["tenantId"]},
        timeout=30,
    ).json()["data"]

    chunks = res.get("results", [])
    chunk_text = "\n\n".join(
        f"[Engram {i+1}] {c.get('content', '')}" for i, c in enumerate(chunks)
    ) or "(no chunks retrieved)"

    client = OpenAI()
    resp = client.chat.completions.create(
        model=args.model,
        messages=[
            {
                "role": "user",
                "content": (
                    "Answer the question using ONLY the engrams below. "
                    "If the answer isn't supported by the engrams, say so explicitly.\n\n"
                    f"{chunk_text}\n\n"
                    f"Question: {q['questionText']}\nAnswer:"
                ),
            }
        ],
    )
    prediction = (resp.choices[0].message.content or "").strip()

    gold_text = gold.get("answer") if isinstance(gold, dict) else gold
    correct = _normalize(str(gold_text)) in _normalize(prediction)

    print(json.dumps({
        "qid": args.qid,
        "type": q["questionType"],
        "tenant_id": q["tenantId"],
        "snapshot_version": q["snapshotVersion"],
        "engram_count": len(chunks),
        "prediction": prediction,
        "gold": gold_text,
        "correct": correct,
    }, indent=2))
    return 0 if correct else 2


if __name__ == "__main__":
    sys.exit(main())
