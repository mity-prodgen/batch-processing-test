"""Can thinking be reduced or switched off on the Gemini model the harness used?

    .venv/bin/python gemini_thinking_probe.py

Sends the first 20 tickets synchronously under four thinking settings and reports,
for each, whether the API accepted it and how many thinking / answer tokens came
back. Also prints the provider-side timestamps of the batch job from the main run.
About 80 tiny requests, well under one cent. Writes results/gemini_thinking_probe.json.
"""
import json
from pathlib import Path

import yaml

from dataset import load_or_create
from providers import GeminiProvider, SYSTEM_PROMPT, parse_label, user_prompt

ROOT = Path(__file__).parent
VARIANTS = {
    "thinking_level LOW (what the harness used)": {"thinking_level": "LOW"},
    "no thinking_config (model default)": None,
    "thinking_level MINIMAL": {"thinking_level": "MINIMAL"},
    "thinking_budget 0 (off)": {"thinking_budget": 0},
}


def main():
    model = yaml.safe_load((ROOT / "prices.yaml").read_text())["gemini"]["model"]
    if not GeminiProvider.api_key():
        raise SystemExit("set GEMINI_API_KEY (or GOOGLE_API_KEY) first")
    g = GeminiProvider(model)
    tickets = load_or_create(ROOT / "data" / "tickets.jsonl")[:20]
    report = {"model": model, "variants": {}}

    for name, thinking in VARIANTS.items():
        cfg = {"system_instruction": SYSTEM_PROMPT, "max_output_tokens": g.max_output_tokens}
        if thinking is not None:
            cfg["thinking_config"] = thinking
        rows, first_error = [], None
        for t in tickets:
            try:
                resp = g.client.models.generate_content(
                    model=model, contents=user_prompt(t["text"]), config=cfg)
            except Exception as e:
                first_error = first_error or repr(e)[:400]
                if not rows:        # rejected outright: no point sending the other 19
                    break
                continue
            u = resp.usage_metadata
            rows.append({
                "id": t["id"],
                "thoughts": u.thoughts_token_count or 0,
                "answer": u.candidates_token_count or 0,
                "input": u.prompt_token_count or 0,
                "correct": parse_label(resp.text) == t["label"],
            })
        n = len(rows)
        summary = {
            "accepted": n > 0,
            "requests_ok": n,
            "error": first_error,
            "thinking_tokens_total": sum(r["thoughts"] for r in rows),
            "answer_tokens_total": sum(r["answer"] for r in rows),
            "billed_output_per_request": round(sum(r["thoughts"] + r["answer"] for r in rows) / n, 1) if n else None,
            "requests_with_zero_thinking": sum(r["thoughts"] == 0 for r in rows),
            "matches_dataset_label": sum(r["correct"] for r in rows),
        }
        report["variants"][name] = summary
        print(f"\n{name}")
        if not n:
            print(f"    REJECTED: {first_error}")
            continue
        print(f"    accepted, {n}/20 ok" + (f" (first error: {first_error})" if first_error else ""))
        print(f"    thinking tokens: {summary['thinking_tokens_total']} total, "
              f"{summary['requests_with_zero_thinking']}/{n} requests used none")
        print(f"    answer tokens:   {summary['answer_tokens_total']} total")
        print(f"    billed output per request: {summary['billed_output_per_request']}")
        print(f"    matches dataset label: {summary['matches_dataset_label']}/{n}")

    state_path = ROOT / "results" / "state.json"
    if state_path.exists() and "gemini" in (state := json.loads(state_path.read_text())):
        job = g.client.batches.get(name=state["gemini"]["batch_id"])
        report["batch_job"] = {
            "provider_create_time": str(job.create_time), "provider_start_time": str(job.start_time),
            "provider_end_time": str(job.end_time),
            "harness_submitted_at": state["gemini"]["submitted_at"],
            "harness_completed_at": state["gemini"]["completed_at"],
        }
        print("\nbatch job timestamps from the main run")
        for k, v in report["batch_job"].items():
            print(f"    {k}: {v}")

    out = ROOT / "results" / "gemini_thinking_probe.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
