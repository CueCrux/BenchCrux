# Harness

Verification scripts for reproducing the published CueCrux benchmark numbers.

## Status

| Script | Status | Purpose |
|---|---|---|
| `run_lme_s.py` | Runnable | LongMemEval-S — 500 questions, one tenant per question |
| `run_locomo.py` | Runnable | LoCoMo — 10 conversations, ~150 questions each (~1986 total) |
| `scoring/` | Runnable | Reference scorers: `exact_match`, `normalized_match`, `gpt4_judge_stub`, `score_jsonl` |

The scripts are runnable today end-to-end. With `--llm predictions-only` they emit the prompt for downstream processing without calling an LLM (no API key required, useful for caching prompts and running scoring offline).

For minimal end-to-end examples in a single file (mint → retrieve → ask LLM → compare against gold), see [`../examples/`](../examples/).

## Quick start

```bash
pip install requests

export BENCHCRUX_PASSPORT="bcx_..."
export BENCHCRUX_API_URL="https://api.vaultcrux.io"

# Smoke test on first 10 questions, no LLM call:
python harness/run_lme_s.py --limit 10 --llm predictions-only > predictions.jsonl

# Full run with Anthropic:
pip install anthropic
export ANTHROPIC_API_KEY="..."
python harness/run_lme_s.py --llm anthropic --model claude-sonnet-4-6 > predictions.jsonl

# Full run with OpenAI:
pip install openai
export OPENAI_API_KEY="..."
python harness/run_lme_s.py --llm openai --model gpt-4o > predictions.jsonl
```

## Output format

One JSON object per line, written to stdout:

```json
{
  "qid": "07741c45-...",
  "type": "MS",
  "tenant_id": "lme-s-q-07741c45",
  "snapshot_version": "benchmark-snapshot-v1",
  "receipt_id": "rcpt_...",
  "receipt_valid": true,
  "engram_count": 5,
  "prediction": "...",
  "gold": "..."
}
```

Pipe to `jq`, store as JSONL, or feed directly into `scoring/`:

```python
from harness.scoring import score_jsonl, normalized_match
print(score_jsonl("predictions.jsonl", scorer=normalized_match))
# → {'total': 500, 'correct': 478, 'gold_missing': 0}
```

## Bringing your own LLM

`--llm` accepts `anthropic`, `openai`, or `predictions-only`. To wire a different provider, copy the `_llm_*` factory functions in `run_lme_s.py` — the `LLMClient` protocol only requires `complete(prompt: str) -> str`.

## What the harness does NOT do

- **It does not score.** LME-S has a specific GPT-4-as-judge protocol that BenchCrux will publish in `scoring/`. Until then, the harness emits predictions + gold and you score yourself.
- **It does not retry.** Every retrieval call is exactly one HTTP POST. If a call fails the harness raises and exits — by design, so verification runs are simple to reason about.
- **It does not cache.** Each run re-fetches questions and re-issues retrievals. If you want caching, wrap the client.

## Reproducibility

Every run prints the `snapshot_version` it ran against. If that doesn't match what the published-results page says, your numbers may not match either — see [`../SNAPSHOT_POLICY.md`](../SNAPSHOT_POLICY.md).
