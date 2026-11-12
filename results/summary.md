# Sync vs batch API cost: 1,000-ticket classification

Generated 2026-11-11T20:24:52-08:00 from the raw per-request results in this directory.

| Provider (model) | Requests (ok / total) | Tokens | Sync cost | Batch cost | $ saved | % saved | Batch turnaround |
|---|---|---|---|---|---|---|---|
| openai (`gpt-5.4-mini`) | 1,000 / 1,000 | 71,162 in / 4,018 out | $0.0715 | $0.0357 | $0.0357 | 50.0% | 3m 39s |
| gemini (`gemini-3.8-flash`) | 1,000 / 1,000 | 65,980 in / 40,247 out | $0.2004 | $0.1002 | $0.1002 | 50.0% | 2m 50s |
| anthropic (`claude-haiku-4-5`) | 1,000 / 1,000 | 71,906 in / 4,000 out | $0.0919 | $0.0460 | $0.0460 | 50.0% | 3m 03s |

## Detail

| Provider | Failed / expired batch requests | Sync latency (median / max) | Actual cost of sync sample | Sync vs batch label agreement | Batch accuracy vs dataset label | Reasoning tokens in output | Cost per 1M requests (sync → batch) |
|---|---|---|---|---|---|---|---|
| openai | 0 | 0.89s / 2.31s | $0.0014 | 19 / 20 | 98.1% | 0 | $71 → $36 |
| gemini | 0 | 1.65s / 3.68s | $0.0044 | 20 / 20 | 94.4% | 39,265 | $200 → $100 |
| anthropic | 0 | 0.52s / 0.70s | $0.0018 | 19 / 20 | 94.8% | 0 | $92 → $46 |

## How to read this

- **Sync cost** and **batch cost** are both computed from the token counts the batch run actually reported (successful requests only), multiplied by the standard and batch list prices in `prices.yaml`. Sync cost is therefore what the same 1,000 requests would have cost synchronously, not a separate measured bill.
- **% saved** follows directly from each provider's published batch discount; the measured parts are the token counts, turnaround and failure counts.
- **Batch turnaround** is provider-reported creation time to provider-reported end time (falls back to the local clock when a provider omits one).
- **Tokens out** is billed output, which includes reasoning/thinking tokens where a model produces them.
- **Sync latency** is wall-clock per request over the sequential sync sample; label agreement compares each sync answer with the batch answer for the same ticket.
- **Cost per 1M requests** scales this workload's per-request cost linearly.
- Prompt caching is not used (prompts are far below every provider's caching minimum).

Prices used (USD per 1M tokens):

| Provider | Model | Standard in / out | Batch in / out | Source |
|---|---|---|---|---|
| anthropic | `claude-haiku-4-5` | $1.0 / $5.0 | $0.5 / $2.5 | https://platform.claude.com/docs/en/about-claude/pricing |
| openai | `gpt-5.4-mini` | $0.75 / $4.5 | $0.375 / $2.25 | https://developers.openai.com/api/docs/pricing |
| gemini | `gemini-3.8-flash` | $0.75 / $3.75 | $0.375 / $1.875 | https://aistudio.google.com/docs/pricing |
