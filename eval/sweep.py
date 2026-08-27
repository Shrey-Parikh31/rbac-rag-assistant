"""Sweep the minimum-similarity cutoff and print what each value costs.

The question this answers: TF-IDF returns a hit for any shared word, so
"academic disciplines" matches the grading policy on the word "academic". Does
a floor on the similarity score remove the nonsense without removing the real
matches, and if so, where is it?

This exists to be deleted once the cutoff is chosen. It is an experiment, not
a feature.

    python eval/sweep.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rag
import tools
from retrieval import load_golden, score  # noqa: E402


def run_at(floor, cases):
    rag.MIN_SCORE = floor
    tools._index = None          # force a rebuild so nothing is cached across runs
    results = score(cases)
    total = sum(r["ok"] for r in results)
    by = {}
    for r in results:
        d = by.setdefault(r["kind"], [0, 0])
        d[0] += r["ok"]
        d[1] += 1
    should_not = [r for r in results if r["kind"] in ("answer", "absent")]
    false_notices = sum(r["got"] == "restricted" for r in should_not)
    para = [r for r in results if r.get("paraphrase")]
    return {"floor": floor, "total": total, "n": len(results), "by": by,
            "false_notices": false_notices, "para": sum(r["ok"] for r in para),
            "n_para": len(para), "leaks": sum(bool(r["leaks"]) for r in results)}


if __name__ == "__main__":
    cases = load_golden()
    print(f"\n{'floor':>7} {'total':>10} {'answer':>9} {'restricted':>12} "
          f"{'absent':>9} {'false notices':>15} {'leaks':>7}")
    for floor in (0.0, 0.02, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20):
        r = run_at(floor, cases)
        b = r["by"]
        f = lambda k: f"{b[k][0]}/{b[k][1]}" if k in b else "-"
        print(f"{floor:>7.2f} {r['total']:>4}/{r['n']:<5} {f('answer'):>9} "
              f"{f('restricted'):>12} {f('absent'):>9} {r['false_notices']:>15} "
              f"{r['leaks']:>7}")
    print("\nHigher floor removes nonsense matches and eventually removes real ones.")
    print("Pick the value where 'absent' stops improving and 'answer' starts falling.\n")
