# Snapshot Policy

Published CueCrux benchmark numbers are tied to a corpus **snapshot**. This document is the public mirror of the internal snapshot policy maintained in PlanCrux. Anything published externally — Substack posts, papers, marketing pages — names a specific snapshot version. BenchCrux releases are tagged to match.

## What a snapshot pins

A snapshot version pins the exact configuration that produced the published numbers:

- **Corpus state** — the chunked + enriched session text in each benchmark tenant, as of a specific ingest timestamp
- **Enrichment pipeline version** — the chunk-context bridging, supersession marker convention, session header format ("New Pilot-65" peak as of `benchmark-snapshot-v1`)
- **Retrieval configuration** — `pgvector hnsw.ef_search=800`, V2 canonical predicates, fusion weights, the 9-engram registry
- **Passport dispatch table** — which (intent × capability class) combinations dispatch which session_procedure and engram set

If any of those change, the snapshot version bumps.

## Current snapshot

| Field | Value |
|---|---|
| Version | `benchmark-snapshot-v1` |
| Effective from | 2026-05-11 |
| Status | Live |
| Published numbers | _(linked from main README on first publication)_ |

## What triggers a bump

A snapshot bump is required when any of the following changes:

1. The chunk enrichment pipeline emits different bytes for the same input session text
2. The retrieval pipeline (HNSW config, fusion weights, lane behaviour) changes in a way that affects which chunks rank for a given query
3. The Pattern B engram registry or session_procedure changes
4. A benchmark corpus is re-ingested with different chunking, different supersession resolution, or any other ingest-time difference
5. Tenant scope/allowlist changes (e.g. adding a new benchmark set to the public surface)

## What does NOT trigger a bump

- Bug fixes that return the system to the documented behaviour (drift correction)
- Internal observability or metric additions
- Performance optimisations that produce identical results
- Adding a new question type filter or pagination cursor to the public read API
- Documentation updates to BenchCrux methodology

## What happens at a bump

1. A new snapshot version is tagged in the VaultCrux repo with the exact config + corpus state
2. The previous snapshot is preserved as a pinned read-only state for the duration of the publication's relevance window (minimum: 6 months from first publication)
3. The public benchmark browser shows a banner indicating which snapshot is live and which produced the published numbers, with a link to the previous snapshot's preserved state
4. BenchCrux publishes a tagged release pinning the harness against the new snapshot, with a CHANGELOG entry naming what changed
5. Re-publication of benchmark numbers happens only after the new snapshot has been tested end-to-end

## What this means for verifiers

- **If the snapshot version in your harness output matches the published-results page, your numbers should match within LLM nondeterminism.** If they don't, please open an issue with the divergent question IDs.
- **If the snapshot has bumped since the published results, expect divergence.** The size of the divergence depends on what changed; the CHANGELOG names it.
- **You can always re-run against a previous snapshot** by passing `--snapshot benchmark-snapshot-v1` to the harness (lands in M10 of the verification-browser ExecPlan).

## Reporting a suspected silent change

If you have evidence that retrieval behaviour has changed without a snapshot bump (e.g. the same query against the same `tenant_id` returns different chunks across two runs at the same `snapshot_version`), please:

1. Open an issue on this repo with the two divergent receipts
2. Tag with `snapshot-drift`
3. Include the `chunk_id`, `snapshot_version`, and timestamps from both runs

This is a load-bearing concern and we want to hear about it immediately.
