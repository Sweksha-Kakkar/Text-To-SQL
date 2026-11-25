"""Measure execution accuracy for each (model, context mode) combination.

Examples (run from the project folder):
    python -m eval.run_eval                                  # default model, all 3 modes
    python -m eval.run_eval --models gemini-3.5-flash gemini-3.5-flash-lite --modes rag
    python -m eval.run_eval --limit 5                        # quick smoke test

Free-tier note: each question is 1-3 Gemini calls. --delay spaces them out.
Results go to eval/results/<timestamp>.json and eval/results/latest.json,
which the Streamlit "Evaluation" page displays.
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from core.executor import run_query  # noqa: E402
from core.pipeline import MODES, Pipeline  # noqa: E402
from eval.compare import results_match  # noqa: E402

HERE = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=[config.DEFAULT_MODEL])
    ap.add_argument("--modes", nargs="+", default=list(MODES), choices=list(MODES))
    ap.add_argument("--thinking", default=config.DEFAULT_THINKING, choices=config.THINKING_LEVELS)
    ap.add_argument("--limit", type=int, default=None, help="only the first N questions")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds between questions")
    args = ap.parse_args()

    tests = json.loads((HERE / "test_set.json").read_text(encoding="utf-8"))[: args.limit]
    gold = {t["id"]: run_query(t["gold_sql"]).df for t in tests}
    pipeline = Pipeline()

    rows = []
    for model in args.models:
        for mode in args.modes:
            print(f"\n=== {model} | {mode} | thinking={args.thinking} ===")
            for t in tests:
                r = pipeline.ask(t["question"], model=model, mode=mode, thinking=args.thinking, handle_empty=False)
                # Free-tier overload (503/429 on every model) is not a wrong answer: wait, retry.
                for wait in (30, 60):
                    if not (r.error and r.error.startswith("All models failed")):
                        break
                    print(f"  ...Gemini unavailable, retrying in {wait}s")
                    time.sleep(wait)
                    r = pipeline.ask(t["question"], model=model, mode=mode, thinking=args.thinking, handle_empty=False)
                correct = r.ok and results_match(gold[t["id"]], r.df, t.get("match", "all"))
                rows.append({
                    "id": t["id"], "difficulty": t["difficulty"], "question": t["question"],
                    "model": model, "mode": mode, "thinking": args.thinking,
                    "correct": correct, "sql": r.sql, "error": r.error,
                    "attempts": len(r.attempts), "self_corrected": r.ok and len(r.attempts) > 1,
                    "model_used": r.attempts[-1].model if r.attempts else None,
                    "seconds": round(r.total_seconds, 2),
                })
                mark = "PASS" if correct else ("ERR " if r.error else "FAIL")
                print(f"  [{mark}] #{t['id']:>2} {t['difficulty']:<6} {r.total_seconds:5.1f}s  {t['question'][:70]}")
                time.sleep(args.delay)

    summary = {}
    for model in args.models:
        for mode in args.modes:
            sel = [r for r in rows if r["model"] == model and r["mode"] == mode]
            acc = sum(r["correct"] for r in sel) / len(sel)
            summary[f"{model} | {mode}"] = round(100 * acc, 1)
            by_diff = {d: f"{sum(r['correct'] for r in sel if r['difficulty'] == d)}/"
                          f"{sum(1 for r in sel if r['difficulty'] == d)}"
                       for d in ("easy", "medium", "hard")}
            print(f"{model:<24} {mode:<12} accuracy {100 * acc:5.1f}%  {by_diff}  "
                  f"avg {sum(r['seconds'] for r in sel) / len(sel):.1f}s")

    out = {"run_at": datetime.now().isoformat(timespec="seconds"), "n_questions": len(tests),
           "summary": summary, "rows": rows}
    results = HERE / "results"
    results.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for name in (f"{stamp}.json", "latest.json"):
        (results / name).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nsaved eval/results/{stamp}.json")


if __name__ == "__main__":
    main()
