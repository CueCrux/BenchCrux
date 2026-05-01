# BenchCrux

**Public verification harness for CueCrux benchmark publishing.**

The numbers we publish — Pattern B at 100% on LME-S valid questions, chunks-only on LoCoMo, and whatever lands next — should not be taken on faith. BenchCrux is the toolkit any external party needs to reproduce them, with their own LLM, against the same retrieval system that produced the published results.

## What you get

- **The post-enrichment session payload Pattern B consumed.** Supersession markers, session context headers, and the New Pilot-65 enrichment baked into the chunk bytes. What you see is what the published run saw.
- **A signed CROWN receipt on every retrieval.** Independently verifiable via a public endpoint or via your own implementation of the receipt spec.
- **A 10-line verification loop in Python (TypeScript parity coming).** Plug in your LLM of choice; we don't care which.

## Verification flow

1. **Request a verification passport.** Email `verify@cuecrux.io` with your name, affiliation, and which benchmark you want to verify (LME-S, LoCoMo, or both). You'll receive a 30-day, read-only passport scoped to the benchmark tenant allowlist. Manual issuance is intentional for the MVP — we want to talk to verifiers.

2. **Set the passport in your environment.**
   ```bash
   export BENCHCRUX_PASSPORT="bcx_..."
   export BENCHCRUX_API_URL="https://api.vaultcrux.io"
   ```

3. **Run the harness.**
   ```bash
   pip install requests
   python harness/run_lme_s.py --limit 10  # smoke test on first 10 questions
   python harness/run_lme_s.py             # full 500
   ```
   Bring your own LLM via `--llm` (`anthropic`, `openai`, or a custom callable — see `harness/README.md`).

4. **Compare against the published numbers.** The harness prints per-question predictions, retrieval receipts, and a summary. Score against gold using your preferred scorer (LME-S uses GPT-4-as-judge in the original paper; we publish a reference scorer in `harness/scoring/`).

## The 10-line loop

```python
from harness.client import BenchCruxClient

client = BenchCruxClient.from_env()        # reads BENCHCRUX_PASSPORT + BENCHCRUX_API_URL

for question in client.questions("lme-s"):
    result = client.retrieve(
        tenant_id=question.tenant_id,
        query=question.question_text,
    )
    assert client.verify_receipt(result.receipt)
    prediction = your_llm(result.recommended_prompt)
    gold = client.gold("lme-s", question.id)
    print(question.id, question.type, prediction, gold)
```

That's the whole verification surface.

## What's in this repo

| Path | Purpose |
|---|---|
| `harness/` | Python verification scripts (`run_lme_s.py`, `run_locomo.py`, scoring helpers) |
| `methodology/` | Pattern B, retrieval architecture, CROWN receipt structure, chunk enrichment pipeline |
| `SNAPSHOT_POLICY.md` | Corpus snapshot bump policy — when published numbers may diverge from current retrieval |
| `LICENCE.md` | CueCrux Community Licence v1.0 |
| `CITATION.cff` | Academic citation metadata |

## How this works under the hood

When the harness calls `/v1/retrieve` with your verification passport, the server:

1. Validates the passport against the benchmark tenant allowlist (LME-S: 500 tenants, one per question; LoCoMo: 10 tenants, one per conversation).
2. Routes the query through the same retrieval pipeline that produced the published numbers — `pgvector` HNSW with `ef_search=800`, lexical/dense fusion, V2 canonical predicates, server-dispatched Pattern B engrams + session_procedure based on (intent × passport capability class).
3. Returns ranked engrams with their post-enrichment chunk text, a signed CROWN receipt, the dispatched template metadata, and a recommended prompt construction.

You build a prompt (the server-recommended one is included), call your LLM, and score against gold. Same loop the published run used.

See [`methodology/`](methodology/) for the full architecture writeup.

## Reproducibility commitment

The chunk text returned by the public read endpoints (`/v1/benchmarks/lme-s/questions/:id/sessions`) and the chunk text returned by `/v1/retrieve` are **byte-identical** for the same `chunk_id`, modulo encryption envelope. CI in the VaultCrux repo enforces this on every build. If you find a divergence, please open an issue — that's the load-bearing assertion behind everything else.

## Snapshot pinning

Every benchmark response includes a `snapshot_version`. Published Pattern B results were produced against `benchmark-snapshot-v1`. If the snapshot ever advances, the public browser on the VaultCrux site shows a banner indicating which version is live, and BenchCrux releases a tagged version pinning the matching harness configuration. See [`SNAPSHOT_POLICY.md`](SNAPSHOT_POLICY.md).

## Status

This repository ships the bootstrap scaffolding plus a Python harness skeleton against the API contract. The full feature set lands progressively as the public read API endpoints come online (see ExecPlan milestones M1–M3 in PlanCrux). The harness is structured so it will run end-to-end the moment the endpoints are live.

Until then: clone, read the harness, send feedback. The contract is documented in `harness/run_lme_s.py` and the methodology directory.

## Citing

```bibtex
@software{benchcrux2026,
  author = {{CueCrux BenchCrux Maintainers}},
  title  = {BenchCrux: public verification harness for CueCrux benchmarks},
  year   = {2026},
  url    = {https://github.com/cuecrux/BenchCrux}
}
```

Or use [`CITATION.cff`](CITATION.cff) directly.

## Licence

CueCrux Community Licence v1.0 (CCL v1.0). Source-available, not open-source. See [`LICENCE.md`](LICENCE.md).

The CCL permits internal use, modification, audit, and academic publication with attribution. It prohibits redistribution as a competing managed service. After the Change Date, the licence converts to Apache 2.0.

## Contact

- Verification passport requests: `verify@cuecrux.io`
- Methodology questions / issues: open an issue on this repo
- Security disclosures: see `SECURITY.md` in the parent CueCrux organisation

---

Part of the [CueCrux](https://cuecrux.io) ecosystem. BenchCrux is the public companion to VaultCrux retrieval and the published Pattern B benchmark results.
