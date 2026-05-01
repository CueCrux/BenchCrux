# Methodology

How the published CueCrux benchmark numbers were produced, and what a verifier is reproducing when they run BenchCrux.

## Benchmarks covered

- **LongMemEval-S (LME-S)** — 500 multi-session conversational memory questions across six question types: MS (multi-session), TR (temporal reasoning), KU (knowledge update), SSA (single-session attribute), SSU (single-session update), SSP (single-session preference). Each question has its own conversation history; each history is ingested into its own VaultCrux tenant.
- **LoCoMo** — 10 long-form conversations with multiple questions each. Each conversation is ingested into its own tenant.

## Architecture under test

The verification surface exercises three load-bearing pieces of the CueCrux retrieval stack:

### 1. Chunk enrichment pipeline ("New Pilot-65")

At ingest time, each session is chunked and the chunk text is enriched with:

- A literal `[Context: 'Session N']` header, with a `superseded` annotation when the chunk has been superseded by a later session
- Inline date markers (e.g. `(2023-01-19)`) preserved in the chunk body
- Session-context bridging so cross-session references resolve at retrieval time

The enrichment is baked into the chunk **bytes** at ingest, not rendered at read time. This means the chunks Pattern B sees during retrieval are exactly the chunks any verifier sees through `/v1/retrieve` or the public read endpoints — there is no per-request transformation that could drift.

### 2. Hybrid retrieval

VaultCrux runs a fusion of:

- `pgvector` HNSW with `ef_search=800` (set globally on the database; raising this from the default 40 was load-bearing for narrow tenants)
- Lexical scoring on `content_tsv`
- A fusion layer that combines the two with weights internal to the system

A passport-driven router dispatches engrams and a session_procedure on `iteration=1` of `/v1/memory/retrieve`, classified by (intent × passport capability class). The 9-engram registry covers the structural retrieval patterns observed in LME-S — multi-entity aggregation, temporal duration, supersession resolution, etc.

### 3. CROWN receipts

Every `/v1/retrieve` call mints a **retrieval receipt** with:

- An opaque `id` (UUID) and a `receipt_hash` (BLAKE3 of canonical inputs)
- The tenant_id, agent_id, chunk ids returned, result count, search mode, and creation timestamp

Tools that emit signed CROWN execution receipts (Ed25519) chain via `parent_receipt_hash`. Both kinds are verifiable via `POST /v1/receipts/verify`.

#### Chain proof format v1

`POST /v1/receipts/verify` accepts `{ "receipt_hash": "<32-128 hex chars>" }` and returns:

```json
{
  "ok": true,
  "data": {
    "valid": true,
    "kind": "retrieval" | "execution",
    "chain_proof_format": "v1",
    "receipt": { "id": "...", "receipt_hash": "...", "tenant_id": "...", "..." : "..." },
    "signature": null | {
      "crown_signature": "<hex>",
      "signing_kid": "<key id>",
      "signing_pub": "<hex>",
      "verified": true | false,
      "algorithm": "ed25519"
    },
    "chain": [
      { "id": "...", "receipt_hash": "...", "parent_receipt_hash": "...|null",
        "tool_name": "...", "outcome": "...", "occurred_at": "...",
        "signing_kid": "...", "signature_verified": true | false }
    ],
    "reason": null | "signature_invalid" | "receipt_unsigned",
    "notes": "...optional human-readable explanation..."
  }
}
```

**Privacy boundary:** the endpoint only returns receipts whose tenant is in a `is_public=true` benchmark set. Receipts from private tenants 404 — the endpoint never confirms or denies their existence beyond "not found in any public benchmark set."

**Reimplementation, no trust required:**

- For `kind: "retrieval"`, the proof is membership in the public benchmark receipts table. The receipt_hash is `blake3(canonical_json({tenantId, agentId, chunkIds, resultCount, searchMode, projectionVersionId}))`. A verifier can recompute it locally and confirm against the response.
- For `kind: "execution"`, the proof is an Ed25519 signature over the receipt_hash. Verify with the published `signing_pub` (Ed25519 public key) using any standard library:
  ```
  ed25519.verify(signature=hex_to_bytes(crown_signature),
                 message=hex_to_bytes(receipt_hash),
                 public_key=hex_to_bytes(signing_pub))
  ```
- The chain walks via `parent_receipt_hash` and is also restricted to public tenants. A `null` parent means the chain root.

**Failure modes:**

- `valid: false, reason: "signature_invalid"` — the receipt exists, has a signature, but the signature does not verify against the stored public key. Indicates tampering or a bug.
- `valid: false, reason: "receipt_unsigned"` — the receipt exists in `execution_receipts` but has no signature. This shouldn't happen for retrievals served under verification passports; report it if you see it.
- `404 Not found` — receipt is not in any public benchmark set. Either the receipt is from a private tenant (and the verifier was never authorized to see it), or the hash is fabricated.

The chain_proof_format is versioned (`v1` today). Future format bumps will add new fields without removing existing ones.

## Pattern B

"Pattern B" is the consumer architecture that produced the published results. In the published runs, Sonnet `Task()` subagents iterate retrieval calls per question with full LLM reasoning on each turn — typically 1-3 calls per question, occasionally up to 8 for hard multi-entity aggregations. The agent decides when it has enough context to answer.

Pattern B is **not** a server-side feature. It's a consumer pattern. A verifier doesn't have to use it — they can use any agent loop or zero-shot strategy. What BenchCrux verifies is that **the retrieval surface returns the same engrams** the published Pattern B run consumed. What the verifier's LLM does with those engrams is their choice.

The recommended prompt returned by `/v1/retrieve` is what Pattern B's first iteration would have built; subsequent iterations are the verifier's loop to design.

## Reproducibility commitment

The chunk text returned by `/v1/benchmarks/lme-s/questions/:id/sessions` is **byte-identical** to the chunk text returned by `/v1/retrieve` for the same `chunk_id`, modulo the encryption envelope. CI in the VaultCrux repo enforces this on every build via a diff test. If a verifier finds a divergence, that is a load-bearing bug and we want to hear about it.

## Snapshot pinning

Published results are tied to a specific corpus snapshot. The first publishable snapshot is `benchmark-snapshot-v1`. Each benchmark API response includes a `snapshot_version` so verifiers can confirm they are running against the same state that produced the published numbers. See [`../SNAPSHOT_POLICY.md`](../SNAPSHOT_POLICY.md) for what triggers a snapshot bump and how it affects verification.

## Caveats verifiers should know about

### Memorisation contamination

LoCoMo has been demonstrated to be in the training data of several recent commercial models. GPT-5.4-Mini and GPT-5.5 produce verbatim gold answers for some LoCoMo questions even with **zero retrieved chunks**. This is not a retrieval result; it is recall of the training corpus.

LME-S corpus is also publicly available on HuggingFace and is therefore at the same contamination risk. Verifiers running these benchmarks on commercial frontier models should expect their numbers to reflect a mix of retrieval quality and training-set memorisation. The way to disambiguate is to look at the engram coverage and BenchCrux receipt: a verbatim-gold prediction with low engram coverage and low retrieval scores is evidence of memorisation, not retrieval.

### Self-judging vs blind grading

LME-S in the original paper uses a GPT-4-as-judge grader. BenchCrux will publish a reference scorer in `harness/scoring/` once that lands. Until then, verifiers can score against gold using whatever methodology they prefer. We recommend reporting both strict (exact match) and lenient (judge-graded) scores for transparency.

### Gold answer errors

Two confirmed gold-annotation errors are documented in the public methodology:

- LME-S `370a8ff4` — gold says 15 weeks; correct answer (per the conversation dates) is ~11.6 weeks. Pattern B's prediction is correct; gold is wrong.
- LME-S `gpt4_88806d6e` — gold says "Tom"; correct answer (per the conversation timeline) is "Mark and Sarah". Pattern B's prediction is correct; gold is wrong.

Verifiers reproducing strict-gold scores should expect at least these two questions to come back "wrong" with a correct answer. We've contacted the LME-S maintainers about both.

## Further reading

- `harness/run_lme_s.py` — the verification loop (also serves as the most precise specification of the public API contract)
- `../SNAPSHOT_POLICY.md` — when published numbers may diverge from current retrieval
- `../README.md` — top-level verification flow
