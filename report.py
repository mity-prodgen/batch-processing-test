"""Build results/summary.md from the recorded per-request JSONL + prices.yaml.

    python report.py                       # recompute after editing prices.yaml
    python report.py --results-dir results
"""
import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).parent


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def cost(input_tokens: int, output_tokens: int, price: dict) -> float:
    """price = {"input": $/MTok, "output": $/MTok}"""
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def turnaround_s(st: dict) -> float | None:
    if not (st.get("submitted_at") and st.get("completed_at")):
        return None
    return (datetime.fromisoformat(st["completed_at"])
            - datetime.fromisoformat(st["submitted_at"])).total_seconds()


def build_summary(results_dir: Path, prices: dict) -> str:
    state = json.loads((results_dir / "state.json").read_text()) \
        if (results_dir / "state.json").exists() else {}
    main, detail = [], []

    for name, st in state.items():
        if st.get("status") != "done":
            continue
        price = prices[name]
        batch = read_jsonl(results_dir / f"{name}_batch.jsonl")
        sync = read_jsonl(results_dir / f"{name}_sync.jsonl")
        ok = [r for r in batch if r["status"] == "succeeded"]
        in_tok = sum(r["input_tokens"] for r in ok)
        out_tok = sum(r["output_tokens"] for r in ok)
        reasoning = sum(r["reasoning_tokens"] for r in ok)

        std_cost = cost(in_tok, out_tok, price["standard"])
        batch_cost = cost(in_tok, out_tok, price["batch"])
        saved = std_cost - batch_cost
        pct = 100 * saved / std_cost if std_cost else 0.0
        main.append(
            f"| {name} (`{st['model']}`) | {len(ok):,} / {len(batch):,} "
            f"| {in_tok:,} in / {out_tok:,} out "
            f"| ${std_cost:.4f} | ${batch_cost:.4f} | ${saved:.4f} | {pct:.1f}% "
            f"| {fmt_duration(turnaround_s(st))} |")

        by_status = {}
        for r in batch:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        failed = ", ".join(f"{v} {k}" for k, v in sorted(by_status.items())
                           if k != "succeeded") or "0"

        sync_ok = [r for r in sync if r["status"] == "succeeded"]
        lat = [r["latency_s"] for r in sync_ok if r["latency_s"] is not None]
        latency = f"{statistics.median(lat):.2f}s / {max(lat):.2f}s" if lat else "n/a"
        sync_cost = cost(sum(r["input_tokens"] for r in sync_ok),
                         sum(r["output_tokens"] for r in sync_ok), price["standard"])

        batch_by_id = {r["id"]: r for r in ok}
        pairs = [(r, batch_by_id[r["id"]]) for r in sync_ok if r["id"] in batch_by_id]
        agree = sum(1 for s, b in pairs if s["label"] and s["label"] == b["label"])
        consistency = f"{agree} / {len(pairs)}" if pairs else "n/a"

        truth = {r["id"]: r.get("expected") for r in batch}
        scored = [r for r in ok if truth.get(r["id"])]
        acc = (f"{100 * sum(r['label'] == truth[r['id']] for r in scored) / len(scored):.1f}%"
               if scored else "n/a")

        per_m = 1_000_000 / len(ok) if ok else 0
        detail.append(
            f"| {name} | {failed} | {latency} | ${sync_cost:.4f} | {consistency} | {acc} "
            f"| {reasoning:,} | ${std_cost * per_m:,.0f} → ${batch_cost * per_m:,.0f} |")

    lines = [
        "# Sync vs batch API cost: 1,000-ticket classification",
        "",
        f"Generated {datetime.now().astimezone().isoformat(timespec='seconds')} "
        "from the raw per-request results in this directory.",
        "",
        "| Provider (model) | Requests (ok / total) | Tokens | Sync cost | Batch cost "
        "| $ saved | % saved | Batch turnaround |",
        "|---|---|---|---|---|---|---|---|",
        *(main or ["| _no completed batches yet_ | | | | | | | |"]),
        "",
        "## Detail",
        "",
        "| Provider | Failed / expired batch requests | Sync latency (median / max) "
        "| Actual cost of sync sample | Sync vs batch label agreement "
        "| Batch accuracy vs dataset label | Reasoning tokens in output "
        "| Cost per 1M requests (sync → batch) |",
        "|---|---|---|---|---|---|---|---|",
        *detail,
        "",
        "## How to read this",
        "",
        "- **Sync cost** and **batch cost** are both computed from the token counts the "
        "batch run actually reported (successful requests only), multiplied by the "
        "standard and batch list prices in `prices.yaml`. Sync cost is therefore what "
        "the same 1,000 requests would have cost synchronously, not a separate measured bill.",
        "- **% saved** follows directly from each provider's published batch discount; "
        "the measured parts are the token counts, turnaround and failure counts.",
        "- **Batch turnaround** is provider-reported creation time to provider-reported "
        "end time (falls back to the local clock when a provider omits one).",
        "- **Tokens out** is billed output, which includes reasoning/thinking tokens "
        "where a model produces them.",
        "- **Sync latency** is wall-clock per request over the sequential sync sample; "
        "label agreement compares each sync answer with the batch answer for the same ticket.",
        "- **Cost per 1M requests** scales this workload's per-request cost linearly.",
        "- Prompt caching is not used (prompts are far below every provider's caching minimum).",
        "",
        "Prices used (USD per 1M tokens):",
        "",
        "| Provider | Model | Standard in / out | Batch in / out | Source |",
        "|---|---|---|---|---|",
        *(f"| {n} | `{p['model']}` | ${p['standard']['input']} / ${p['standard']['output']} "
          f"| ${p['batch']['input']} / ${p['batch']['output']} | {p['source']} |"
          for n, p in prices.items()),
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results-dir", type=Path, default=ROOT / "results")
    ap.add_argument("--prices", type=Path, default=ROOT / "prices.yaml")
    args = ap.parse_args()
    out = args.results_dir / "summary.md"
    out.write_text(build_summary(args.results_dir, yaml.safe_load(args.prices.read_text())))
    print(f"wrote {out}")
