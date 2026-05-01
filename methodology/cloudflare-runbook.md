# Edge hardening runbook (Cloudflare)

This document is the operator runbook for the edge-layer defences that protect the public benchmark surface. It is the load-bearing complement to:

- `public/robots.txt` (declarative, ignored by ~all AI crawlers)
- API-layer rate limits (5/IP/15min ephemeral mints, 5/IP/day verification mints, 60/IP/min global unauthenticated — enforced by VaultCrux M2)
- `noindex,nofollow` meta tags on every `/bench/*` and `/benchmarks/*` page (M4)
- `X-Robots-Tag: noindex, nofollow, noarchive` HTTP header on `/v1/benchmarks/:set/questions/:id/gold` (M1)

## Why this exists

LME-S and LoCoMo corpora are already on HuggingFace, so the marginal contamination risk from public exposure is small for *these specific datasets*. But the verification surface is also a structural primitive that future non-public benchmarks will run on, and AI training crawlers ignore `robots.txt` aggressively. Edge defences keep the *primitive* uncontaminated even as the public surface grows.

There is also a per-IP server-cost concern. A scraper that ignores rate limits, rotates IPs, and pulls every chunk from every public benchmark tenant can:

- Exhaust the verification mint daily quota for legitimate verifiers (denial-of-verification)
- Push GPU/embedding cost up despite caching
- Pollute observability with synthetic traffic

The rules below are tuned to defeat the cheap-rotation attack while keeping legitimate browser visitors and verification harnesses unimpeded.

## Required Cloudflare configuration

These rules apply to the public hostname serving the Frontdoor (e.g. `vaultcrux.cuecrux.com`) and the API hostname (e.g. `api.vaultcrux.io`). Apply both — the Frontdoor proxies through to the API for `/v1/**`, but the API hostname is also reachable directly.

### 1. Bot Fight Mode (or Super Bot Fight Mode if available)

Settings → Security → Bots:

- **Bot Fight Mode**: ON
- For paid plans: **Super Bot Fight Mode** with "Definitely automated" → Block, "Likely automated" → Managed Challenge, "Verified bots" → Allow

Verified bots include search engine crawlers and uptime monitors. Everything else hits the challenge. Verification harnesses run as `python-requests` or `curl/*` user agents — those will see the challenge once per IP per session, then be allowed. Document this expectation on `/benchmarks/verify`.

### 2. WAF custom rule — block AI training crawlers by user-agent

Security → WAF → Custom rules. Create rule "Block AI crawlers from bench surface":

```
Expression:
  (http.host eq "vaultcrux.cuecrux.com" or http.host eq "api.vaultcrux.io")
  and (
    starts_with(http.request.uri.path, "/bench/")
    or starts_with(http.request.uri.path, "/benchmarks/")
    or starts_with(http.request.uri.path, "/v1/benchmarks/")
    or starts_with(http.request.uri.path, "/v1/passports/")
    or starts_with(http.request.uri.path, "/v1/receipts/verify")
  )
  and (
    http.user_agent contains "GPTBot"
    or http.user_agent contains "ChatGPT-User"
    or http.user_agent contains "OAI-SearchBot"
    or http.user_agent contains "ClaudeBot"
    or http.user_agent contains "Claude-Web"
    or http.user_agent contains "anthropic-ai"
    or http.user_agent contains "Google-Extended"
    or http.user_agent contains "PerplexityBot"
    or http.user_agent contains "cohere-ai"
    or http.user_agent contains "Bytespider"
    or http.user_agent contains "CCBot"
    or http.user_agent contains "FacebookBot"
    or http.user_agent contains "Diffbot"
    or http.user_agent contains "Applebot-Extended"
  )

Action: Block
```

### 3. Rate-limiting rules

Security → WAF → Rate limiting rules.

**Rule A: bench surface IP rate limit (defence-in-depth above the API rate limit)**

```
Expression:
  starts_with(http.request.uri.path, "/v1/benchmarks/")
  or starts_with(http.request.uri.path, "/v1/passports/")
  or starts_with(http.request.uri.path, "/v1/receipts/verify")

Characteristics: IP
Threshold: 600 requests / 1 minute
Action: Managed Challenge for 1 minute
```

The 600/min is well above the legitimate verifier load (a 500-question harness at 1qps takes 8.3 minutes, so peak ~60/min). Catches abusive scrapers without inconveniencing real users.

**Rule B: passport mint hard cap**

```
Expression:
  http.request.method eq "POST"
  and (
    http.request.uri.path eq "/v1/passports/verification"
    or http.request.uri.path eq "/v1/passports/ephemeral"
  )

Characteristics: IP
Threshold: 30 requests / 1 hour
Action: Block for 24 hours
```

The API-layer rate limit (5/IP/day verification + 5/15min ephemeral) is the primary control; this edge rule catches IPs that hammer the mint endpoint hoping for a race condition or trying to enumerate.

### 4. Page rules — caching policy

Caching → Page rules:

- `*/v1/benchmarks/manifest` → Cache level: Standard, Edge TTL: 5 minutes (the API also caches at 5min)
- `*/v1/benchmarks/*/questions` → Cache level: Standard, Edge TTL: 1 hour, Browser TTL: 24 hours
- `*/v1/benchmarks/*/questions/*` (detail) → Edge TTL: 1 hour
- `*/v1/benchmarks/*/questions/*/sessions` → Edge TTL: 1 hour
- `*/v1/benchmarks/*/questions/*/gold` → **Bypass cache** (low-volume, separate URL specifically so we can policy it independently — `noarchive` + bypass means search/AI cache won't store it)
- `*/v1/passports/*` → **Bypass cache**
- `*/v1/retrieve` → **Bypass cache**
- `*/v1/receipts/verify` → Cache level: Standard, Edge TTL: 60 seconds

### 5. Headers transformation rule (defence in depth on `noarchive`)

Rules → Transform Rules → Modify Response Header:

```
Expression:
  starts_with(http.request.uri.path, "/v1/benchmarks/")
  and ends_with(http.request.uri.path, "/gold")

Action: Set X-Robots-Tag = "noindex, nofollow, noarchive"
```

The API already sets this header (M1), but the edge rule guarantees it even if a future API change accidentally drops it.

## Observability — what to watch

Add Cloudflare Analytics dashboards filtered to:

- Path matches `/v1/benchmarks/` or `/v1/passports/` or `/bench/` or `/benchmarks/`
- Group by ASN + country + user-agent
- Alert when:
  - Mint endpoint sees >50 requests/hour from a single ASN
  - Bench surface sees >10k requests/hour aggregate
  - Verified-bot traffic exceeds 5% of total bench traffic (suggests spoofing — verified bots have low base rate here)

For abuse research, log every retrieve call carrying a verification or ephemeral RCX-CT to a separate analytics stream. The API request_id (`x-request-id` header on every response) lets you correlate edge events to API logs.

## Manual verification before publication

Before any external link goes live (Substack post, Hacker News submission, etc.), confirm from a clean browser:

1. `curl -A 'GPTBot/1.0' https://vaultcrux.cuecrux.com/bench/lmes/001be529` returns 403 (WAF block)
2. `curl -I https://api.vaultcrux.io/v1/benchmarks/lme-s/questions/001be529/gold` includes `X-Robots-Tag: noindex, nofollow, noarchive`
3. `curl https://vaultcrux.cuecrux.com/robots.txt` lists the AI-bot disallow blocks
4. The page source at `view-source:https://vaultcrux.cuecrux.com/bench/lmes/001be529` includes `<meta name="robots" content="noindex,nofollow">`
5. Soft-launch (no external announcement) for 48 hours, watch the analytics dashboard, confirm no anomalous traffic
6. Run `apps/api/scripts/verify-benchmark-byte-identity.mjs --sample 25` and confirm zero divergences

If any of those fail, do not announce.

## Rollback

If any rule misfires and blocks legitimate verifiers:

1. Cloudflare WAF custom rules can be paused individually from the dashboard with no deploy
2. Rate limit rules paused via the same path
3. The API-layer rate limits in `vaultcrux.api_rate_windows` survive Cloudflare being entirely bypassed

The edge layer is defence-in-depth. The API rate limits are the primary control. If both fail, the per-token call cap (2× question count per token) is the hard ceiling on scrape volume.
