# Sync vs batch API cost harness

Sends the same 1,000 short support-ticket classification prompts through the batch
APIs of Anthropic, OpenAI and Google Gemini, runs 20 of them synchronously for
comparison, and reports what the workload cost at batch prices versus standard prices.

| Provider | Model (set in `prices.yaml`) | Batch mechanism |
|---|---|---|
| Anthropic | `claude-haiku-4-5` | Message Batches API |
| OpenAI | `gpt-5.4-mini` | Batch API, JSONL file upload, `/v1/chat/completions` |
| Gemini | `gemini-3.8-flash` | Batch Mode, inline requests |

## Results (run of 2026-11-11)

1,000 requests per provider, one run each. Full tables and the prices used are in
[`results/summary.md`](results/summary.md); raw per-request data is alongside it.

| Provider (model) | Requests (ok / total) | Tokens (in / out) | Sync cost | Batch cost | $ saved | % saved | Batch turnaround |
|---|---|---|---|---|---|---|---|
| Anthropic (`claude-haiku-4-5`) | 1,000 / 1,000 | 71,906 / 4,000 | $0.0919 | $0.0460 | $0.0460 | 50.0% | 3m 03s |
| OpenAI (`gpt-5.4-mini`) | 1,000 / 1,000 | 71,162 / 4,018 | $0.0715 | $0.0357 | $0.0357 | 50.0% | 3m 39s |
| Gemini (`gemini-3.8-flash`) | 1,000 / 1,000 | 65,980 / 40,247 | $0.2004 | $0.1002 | $0.1002 | 50.0% | 2m 50s |

| Provider | Failed / expired | Sync latency (median / max, 20 requests) | Sync vs batch label agreement | Cost per 1M requests (sync → batch) |
|---|---|---|---|---|
| Anthropic | 0 | 0.52s / 0.70s | 19 / 20 | $92 → $46 |
| OpenAI | 0 | 0.89s / 2.31s | 19 / 20 | $71 → $36 |
| Gemini | 0 | 1.65s / 3.68s | 20 / 20 | $200 → $100 |

Notes on this run:

- The 50% saving is each provider's published batch discount applied to the measured
  token counts. The measured parts are tokens, absolute cost, turnaround and failures.
- Turnaround is a single run per provider, and not simultaneous: OpenAI and Gemini
  were submitted around 21:44 UTC on 2026-11-11, Anthropic around 04:21 UTC on
  2026-11-12. All three providers allow up to 24 hours.
- 39,265 of Gemini's 40,247 output tokens are thinking tokens, which is why it costs
  more than twice the others. Its price is the promotional rate valid through
  2026-12-31.
- Total spend was about $0.19 at list prices, including the three sync samples.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

API keys are read from environment variables and never written to disk:

```bash
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
export GEMINI_API_KEY=...      # GOOGLE_API_KEY also works
```

A provider without a key is skipped, so you can run them one at a time.
Gemini batch needs a paid-tier key.

## Run

```bash
.venv/bin/python run.py --dry-run     # cost estimates only, no API calls
.venv/bin/python run.py               # the real thing
```

For each provider the script prints an estimated cost and a worst-case cost, then
asks before spending anything. On `y` it runs the 20 sync requests, then submits the
batch. Once all batches are submitted it polls every 30 seconds until they end,
downloads the results and writes the report.

Batches can take minutes to hours (all three providers allow up to 24h). You can
Ctrl-C while it polls: batch ids are stored in `results/state.json`, and running
`python run.py` again resumes polling rather than resubmitting.

Useful flags:

| Flag | Meaning |
|---|---|
| `--providers anthropic,openai` | run a subset |
| `--fresh` | ignore saved state and submit new batches (spends again) |
| `--max-spend 5` | skip a provider if the run's worst-case spend would exceed this |
| `--n 1000`, `--sync-n 20` | workload size |
| `--poll-interval 30` | seconds between status checks |

To rerun from scratch, delete `results/` (or pass `--fresh`).

## Output

| File | Contents |
|---|---|
| `results/summary.md` | the comparison table, a detail table, and the prices used |
| `results/<provider>_batch.jsonl` | one line per batch request: status, output, parsed label, token counts, and the provider's raw response under `raw` |
| `results/<provider>_sync.jsonl` | same for the sync sample, plus `latency_s` |
| `results/state.json` | batch ids, submission and completion timestamps, final request counts |
| `data/tickets.jsonl` | the generated dataset (`id`, `text`, `label`) |

## How costs are computed

Token counts come from each provider's own usage fields on every successful batch
result. They are multiplied by the list prices in [`prices.yaml`](prices.yaml):
once at the standard rate (what the workload would have cost synchronously) and
once at the batch rate. Edit `prices.yaml` and run `python report.py` to recompute
without calling any API.

Things to know before quoting the numbers:

- All three providers publish a flat 50% batch discount, so "% saved" will be 50%
  by construction. The measured results are token counts, absolute cost per
  provider, turnaround time, and failed/expired requests.
- Billed output includes reasoning tokens. OpenAI runs with `reasoning_effort: none`.
  Gemini 3.x Flash cannot disable thinking, so it runs at `thinking_level: LOW` and
  its output count is mostly thinking tokens; the detail table shows how many.
- Gemini's price is a promotional rate valid through 2026-12-31 (see `prices.yaml`).
- Prices were last checked on 2026-11-11. Re-check the source URLs before publishing.
- Sampling is left at each provider's default (current models and SDKs reject or
  discourage setting temperature), so sync-vs-batch label agreement is a spot
  check, not a determinism test.

## Dataset

`dataset.py` generates 1,000 unique synthetic tickets from templates with a fixed
seed (42) across five categories: billing, technical, account, shipping, general.
The prompt is identical for every provider: a one-sentence system instruction plus
`Ticket: <text>`; the model replies with the category name.

## Layout

```
run.py         orchestration: estimate, confirm, sync sample, submit, poll, save
providers.py   the three provider clients and the shared prompt
report.py      summary.md from recorded results + prices.yaml
dataset.py     synthetic ticket generator
prices.yaml    models and per-token prices, with sources
```
