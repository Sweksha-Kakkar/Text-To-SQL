"""Score TableTalk on the two public datasets, with and without the written business rules.

Run from the project folder:  python -m eval.run_real_eval
(load the data first: python -m scripts.load_real_data)
"""
import json
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import datasets  # noqa: E402
from core.executor import run_query  # noqa: E402
from core.pipeline import Pipeline  # noqa: E402
from eval.compare import results_match  # noqa: E402

HERE = Path(__file__).resolve().parent


def main() -> None:
    tests = json.loads((HERE / "real_test_set.json").read_text(encoding="utf-8"))
    loaded = {d.name: d for d in datasets.list_datasets()}
    gold = {t["id"]: run_query(t["gold_sql"], schema=loaded[t["dataset"]].schema).df for t in tests}
    pipeline = Pipeline()

    rows = []
    for setup in ("with rules", "without rules"):
        print(f"\n=== {setup} ===")
        for t in tests:
            ds = loaded[t["dataset"]]
            if setup == "without rules":
                ds = replace(ds, rules="")
            for wait in (30, 60, None):
                r = pipeline.ask(t["question"], dataset=ds, handle_empty=False, use_feedback=False)
                if wait is None or not (r.error and r.error.startswith("All models failed")):
                    break
                time.sleep(wait)  # free-tier overload is not a wrong answer
            correct = r.ok and results_match(gold[t["id"]], r.df, t.get("match", "all"))
            rows.append({"id": t["id"], "dataset": t["dataset"], "question": t["question"], "setup": setup,
                         "correct": correct, "sql": r.sql, "error": r.error,
                         "model_used": r.attempts[-1].model if r.attempts else None})
            print(f"  [{'PASS' if correct else 'FAIL'}] #{t['id']:>2} {t['question']}", flush=True)
            time.sleep(2)

    summary = {}
    for setup in ("with rules", "without rules"):
        sel = [r for r in rows if r["setup"] == setup]
        summary[setup] = f"{sum(r['correct'] for r in sel)}/{len(sel)}"
    print("\n", summary)
    out = {"run_at": datetime.now().isoformat(timespec="seconds"), "summary": summary, "rows": rows}
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "real.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
