"""Score retrieval against the golden set. No model, no API key, no network.

An answer can be wrong two ways: the wrong passage was fetched, or the right
passage was fetched and the model mangled it. They have different fixes, and
measuring them together tells you nothing about which one you have. This scores
only the first, which makes it free and instant, so it can run on every commit
rather than on a good day.

    python eval/retrieval.py            report
    python eval/retrieval.py --json     machine-readable, for the Layer 1 gate

Exit code is 1 if any leak is found, so this is usable as a build gate today.
"""
import os
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools
from rag import Index, load, CLEARANCE

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "golden.jsonl")
K = 3

# Strings that must never reach a caller below the clearance that owns them.
# Checked on every single answer, not only the ones expected to be restricted,
# because a leak is most likely where nobody thought to look.
SECRETS = {
    "confidential": ["78,000", "96,000", "95,000", "124,000", "120,000",
                     "187,000", "1,450"],
    "staff": ["service desk", "on-call engineer", "postmortem"],
}


def cleared(role, level):
    """Is `role` allowed to read material classified `level`?"""
    return level in CLEARANCE.get(role, {"public"})


def load_golden(path=GOLDEN):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def classify(answer):
    """What kind of response did search_docs produce?"""
    if answer.startswith("No passages match that query"):
        return "absent"
    if "clearance" in answer and "does match" in answer:
        return "restricted"
    return "answer"


def score(cases):
    index = Index(load(os.path.join(os.path.dirname(HERE), "docs")))
    results = []

    for c in cases:
        tools.set_role(c["role"])
        answer = tools.search_docs(c["q"], k=K)
        got = classify(answer)

        ok = got == c["kind"]
        detail = ""

        if c["kind"] == "answer" and ok:
            # Right kind of response is not enough: it must be the right document.
            hits = index.search(c["q"], role=c["role"], k=K)
            sources = [h["source"] for h in hits]
            ok = c["source"] in sources
            if not ok:
                detail = f"wanted {c['source']}, got {sources or 'nothing'}"
        elif c["kind"] == "restricted" and ok:
            ok = c["level"] in answer
            if not ok:
                detail = f"named the wrong clearance level, wanted {c['level']}"
        elif not ok:
            detail = f"expected {c['kind']}, got {got}"

        leaks = [s for level, strings in SECRETS.items()
                 if not cleared(c["role"], level)
                 for s in strings if s.lower() in answer.lower()]

        results.append({**c, "got": got, "ok": ok, "detail": detail, "leaks": leaks})

    return results


def report(results):
    by_kind = {}
    for r in results:
        k = by_kind.setdefault(r["kind"], [])
        k.append(r)

    print(f"\nGOLDEN SET: {len(results)} questions, retrieval only, no model\n")
    print(f"{'kind':<12}{'pass':>8}   {'':<4}")
    for kind in ("answer", "restricted", "absent"):
        rs = by_kind.get(kind, [])
        if not rs:
            continue
        good = sum(r["ok"] for r in rs)
        pct = 100 * good / len(rs)
        print(f"  {kind:<10}{good:>3}/{len(rs):<4}  {pct:5.1f}%")

    total = sum(r["ok"] for r in results)
    print(f"  {'TOTAL':<10}{total:>3}/{len(results):<4}  {100*total/len(results):5.1f}%")

    # The number ADR-7 promised to produce: how often does the "restricted
    # material exists" notice fire on a question that deserved a real answer or
    # no answer at all?
    should_not = [r for r in results if r["kind"] in ("answer", "absent")]
    false_notices = [r for r in should_not if r["got"] == "restricted"]
    print(f"\nFALSE 'restricted material exists' NOTICES: "
          f"{len(false_notices)}/{len(should_not)} "
          f"({100*len(false_notices)/len(should_not):.0f}% of questions that deserved one)")
    for r in false_notices:
        flag = " (known)" if r.get("known_false_positive") else ""
        print(f"    {r['id']} {r['role']:<8} {r['q'][:52]!r}{flag}")

    misses = [r for r in results if not r["ok"] and r["got"] != "restricted"]
    if misses:
        print(f"\nRETRIEVAL MISSES: {len(misses)}")
        for r in misses:
            tag = " [paraphrase]" if r.get("paraphrase") else ""
            print(f"    {r['id']} {r['role']:<8} {r['q'][:46]!r}{tag}\n"
                  f"           {r['detail']}")

    para = [r for r in results if r.get("paraphrase")]
    if para:
        good = sum(r["ok"] for r in para)
        print(f"\nPARAPHRASED QUESTIONS: {good}/{len(para)} "
              f"({100*good/len(para):.0f}%) -- the number embeddings would have to beat")

    leaked = [r for r in results if r["leaks"]]
    print(f"\nLEAK CHECK: {len(leaked)} violation(s)")
    for r in leaked:
        print(f"    {r['id']} {r['role']} saw {r['leaks']}")
    print()
    return 1 if leaked else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    results = score(load_golden())
    if args.json:
        json.dump(results, sys.stdout, indent=1)
        raise SystemExit(1 if any(r["leaks"] for r in results) else 0)
    raise SystemExit(report(results))
