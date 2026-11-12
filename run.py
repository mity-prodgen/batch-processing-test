"""Sync vs batch cost harness for Anthropic, OpenAI and Gemini.

    python run.py                      # all providers that have an API key set
    python run.py --providers openai   # just one
    python run.py --dry-run            # print cost estimates, call nothing

Per provider: show a cost estimate and ask, run a small sync sample, submit the
full set as one batch. Then poll every batch until it ends, save raw per-request
results to results/, and write results/summary.md.

Safe to interrupt while polling: batch ids are saved in results/state.json and a
re-run resumes polling instead of submitting (and paying) again.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import yaml

from dataset import load_or_create
from providers import PROVIDERS, SYSTEM_PROMPT, now, record, user_prompt
from report import build_summary, cost

ROOT = Path(__file__).parent


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))


def estimate(cls, tickets, sync_n, price) -> dict:
    """Rough pre-flight numbers: ~4 chars/token plus a little per-request overhead."""
    def in_tokens(rows):
        return sum((len(SYSTEM_PROMPT) + len(user_prompt(t["text"]))) // 4 + 12 for t in rows)
    n, n_in, s_in = len(tickets), in_tokens(tickets), in_tokens(tickets[:sync_n])
    return {
        "input_tokens": n_in,
        "sync": cost(s_in, sync_n * cls.est_output_tokens, price["standard"]),
        "batch": cost(n_in, n * cls.est_output_tokens, price["batch"]),
        # Upper bound: every request (sync sample + batch) uses its full output cap.
        "worst": cost(s_in, sync_n * cls.max_output_tokens, price["standard"])
                 + cost(n_in, n * cls.max_output_tokens, price["batch"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--providers", default=",".join(PROVIDERS),
                    help="comma-separated subset of: " + ", ".join(PROVIDERS))
    ap.add_argument("--n", type=int, default=1000, help="requests per batch")
    ap.add_argument("--sync-n", type=int, default=20, help="prompts also run synchronously")
    ap.add_argument("--poll-interval", type=int, default=30, help="seconds between status checks")
    ap.add_argument("--max-spend", type=float, default=5.0,
                    help="refuse to submit if worst-case spend for this run exceeds this (USD)")
    ap.add_argument("--dry-run", action="store_true", help="print estimates only")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore saved state and submit new batches (costs money again)")
    ap.add_argument("--results-dir", type=Path, default=ROOT / "results")
    ap.add_argument("--prices", type=Path, default=ROOT / "prices.yaml")
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "tickets.jsonl")
    args = ap.parse_args()

    names = [p.strip() for p in args.providers.split(",") if p.strip()]
    unknown = [p for p in names if p not in PROVIDERS]
    if unknown:
        ap.error(f"unknown provider(s): {', '.join(unknown)}")

    prices = yaml.safe_load(args.prices.read_text())
    tickets = load_or_create(args.data, args.n)
    expected = {t["id"]: t["label"] for t in tickets}
    args.results_dir.mkdir(parents=True, exist_ok=True)
    state_path = args.results_dir / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    def save_state():
        state_path.write_text(json.dumps(state, indent=2) + "\n")

    def save_records(name, mode, rows):
        for r in rows:
            r["expected"] = expected.get(r["id"])
        write_jsonl(args.results_dir / f"{name}_{mode}.jsonl", rows)

    print(f"{len(tickets)} tickets, {args.sync_n} sync per provider, "
          f"spend cap ${args.max_spend:.2f} (worst case)\n")

    # ---- Phase 1: estimate, confirm, sync sample, submit batch -----------------------
    active = {}          # name -> provider instance with a batch to poll
    committed = 0.0      # worst-case spend approved so far in this run
    for name in names:
        cls, price = PROVIDERS[name], prices[name]
        model = price["model"]
        st = state.get(name)

        if st and not args.fresh:
            if st["status"] == "done":
                print(f"[{name}] already complete ({st['batch_id']}); use --fresh to rerun.")
                continue
            if not args.dry_run and cls.api_key():
                print(f"[{name}] resuming batch {st['batch_id']} submitted {st['submitted_at']}")
                active[name] = cls(st["model"])
                continue

        est = estimate(cls, tickets, args.sync_n, price)
        print(f"[{name}] model {model}")
        print(f"    est. input tokens : ~{est['input_tokens']:,} for the batch")
        print(f"    est. cost         : ${est['sync']:.4f} sync sample + ${est['batch']:.4f} batch "
              f"= ${est['sync'] + est['batch']:.4f}")
        print(f"    worst case        : ${est['worst']:.4f} "
              f"(every request hits the {cls.max_output_tokens}-token output cap)")
        if args.dry_run:
            print()
            continue
        if not cls.api_key():
            print(f"    skipped: set {' or '.join(cls.env_keys)} to run this provider.\n")
            continue
        if committed + est["worst"] > args.max_spend:
            print(f"    skipped: worst case would take this run to "
                  f"${committed + est['worst']:.2f}, over --max-spend ${args.max_spend:.2f}.\n")
            continue
        answer = input(f"    Run {args.sync_n} sync requests and submit a batch of "
                       f"{len(tickets)} to {name}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("    skipped.\n")
            continue
        committed += est["worst"]

        provider = cls(model)
        print(f"    running {args.sync_n} sync requests...", flush=True)
        sync_rows = [provider.sync(t) for t in tickets[:args.sync_n]]
        save_records(name, "sync", sync_rows)
        failed = [r for r in sync_rows if r["status"] != "succeeded"]
        if len(failed) == len(sync_rows) and sync_rows:
            print(f"    every sync request failed, not submitting the batch.\n"
                  f"    first error: {failed[0]['error']}\n")
            continue
        if failed:
            print(f"    {len(failed)} sync request(s) failed; first error: {failed[0]['error']}")

        print("    submitting batch...", flush=True)
        try:
            sub = provider.submit(tickets)
        except Exception as e:
            print(f"    batch submission failed: {e!r}\n")
            continue
        state[name] = {"model": model, "status": "submitted", "n": len(tickets), **sub}
        save_state()
        active[name] = provider
        print(f"    submitted {sub['batch_id']} at {sub['submitted_at']}\n")

    if args.dry_run:
        return 0

    # ---- Phase 2: poll until every batch has ended, then fetch results ----------------
    try:
        while active:
            for name in list(active):
                provider, st = active[name], state[name]
                try:
                    status = provider.poll(st["batch_id"])
                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] {name}: poll failed ({e!r}), will retry")
                    continue
                print(f"[{time.strftime('%H:%M:%S')}] {name}: {status['status']} {status['counts']}")
                if not status["done"]:
                    continue
                rows = provider.fetch(st["batch_id"], tickets)
                seen = {r["id"] for r in rows}
                rows += [record(name, st["model"], "batch", t["id"], "missing",
                                error=f"no result returned; batch ended as {status['status']}")
                         for t in tickets[:st["n"]] if t["id"] not in seen]
                rows.sort(key=lambda r: r["id"])
                save_records(name, "batch", rows)
                st.update(status="done", final_status=status["status"],
                          counts=status["counts"], observed_done_at=now(),
                          completed_at=status["completed_at"] or now())
                save_state()
                del active[name]
                ok = sum(r["status"] == "succeeded" for r in rows)
                print(f"    {name}: saved {len(rows)} results ({ok} succeeded)")
            if active:
                time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("\nInterrupted. Batches keep running on the provider side; "
              "re-run `python run.py` to resume polling.")
        return 130

    # ---- Phase 3: report ---------------------------------------------------------------
    summary = build_summary(args.results_dir, prices)
    (args.results_dir / "summary.md").write_text(summary)
    print(f"\nwrote {args.results_dir / 'summary.md'}\n")
    print(summary.split("## Detail")[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
