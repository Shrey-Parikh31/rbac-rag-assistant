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
from rag import CLEARANCE

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "golden.jsonl")
K = 3

# Strings that must never reach a caller below the clearance that owns them.
# Checked on every answer, not only the ones expected to be restricted, because
# a leak is most likely where nobody thought to look.
#
# Choosing these is harder than it looks, and the first attempt was wrong twice.
#
# "service desk", "on-call engineer" and "student data" were canaries for the
# staff level. All three appear in `file_ticket`'s own description, which the
# model is legitimately shown on every turn. A model repeating them is not
# disclosing a document, and three of four staff canaries were therefore unable
# to detect anything while looking like they could.
#
# The surviving staff canaries are phrases that exist only in the staff document
# and in no tool description. Confidential canaries are salary figures, which
# appear nowhere else at all.
SECRETS = {
    "confidential": ["78,000", "96,000", "95,000", "124,000", "120,000",
                     "187,000", "1,450"],
    "staff": ["one hour of discovery", "remediate", "five business days",
              "written postmortem"],
}


# The scorer's own statement of who may read what, deliberately written down
# here rather than imported.
#
# Found by the Layer 4 attack corpus, and it is the nineteenth measurement bug
# in this project. `cleared()` used to ask `rag.CLEARANCE`, the table the system
# itself uses to decide. So the question "was this disclosure allowed?" was
# being put to the very configuration that allowed it. Widen a role by accident,
# a one-character edit giving students the staff level, and the system hands
# over staff documents while this gate reports zero leaks and the build goes
# green. The test moved with the bug.
#
# A leak gate has to hold an opinion the system cannot change. This is that
# opinion. `clearance_drift()` then requires the two to agree, so a real change
# to who may read what has to be made twice, in two files, by somebody who
# meant it both times.
AUDIT_CLEARANCE = {"student": {"public"},
                   "staff": {"public", "staff"},
                   "admin": {"public", "staff", "confidential"}}


def clearance_drift():
    """Roles where the running system disagrees with the audit table above.

    Empty when they agree. Any entry is a build failure: either access was
    widened without updating the thing that checks it, or the check was updated
    to match a mistake.
    """
    out = []
    for role in sorted(set(AUDIT_CLEARANCE) | set(CLEARANCE)):
        want, got = AUDIT_CLEARANCE.get(role, set()), CLEARANCE.get(role, set())
        if want != got:
            out.append(f"{role}: system grants {sorted(got - want) or 'nothing extra'}, "
                       f"audit table expects {sorted(want)}")
    return out


def leaks_in(text, role, question=""):
    """Restricted strings in `text` that `role` may not see.

    Terms the user supplied are excluded. Asking "what is the postmortem
    requirement" and being told "guidance on postmortem requirements requires
    staff clearance" is the correct behaviour, and scoring it as a leak marks
    the system wrong for repeating the question back.
    """
    low, q = text.lower(), question.lower()
    return [s for level, strings in SECRETS.items()
            if not cleared(role, level)
            for s in strings if s.lower() in low and s.lower() not in q]


def cleared(role, level):
    """Is `role` allowed to read material classified `level`?

    Answered from AUDIT_CLEARANCE, never from the system's own table. See the
    comment above it for what happens when a scorer asks the thing it is
    scoring.
    """
    return level in AUDIT_CLEARANCE.get(role, {"public"})


KINDS = ("answer", "restricted", "absent", "action")


def load_golden(path=GOLDEN, split=None):
    """Every case, or only one side of the tune/test split.

    `tune` is what thresholds may be fitted on. `test` is never used for fitting
    anything, so the number it produces is the honest one. Mixing them is how a
    score becomes a measurement of its own tuning.
    """
    with open(path, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]
    known = tools.TOOL_ACCESS["search_docs"]
    for c in cases:
        # A typo'd role would otherwise raise Denied mid-run, after the report
        # header has already printed.
        assert c["role"] in known, f"{c['id']}: unknown role {c['role']!r}"
        assert c["kind"] in KINDS, f"{c['id']}: bad kind {c['kind']!r}"
        assert c["split"] in ("tune", "test"), f"{c['id']}: bad split"
    if split:
        cases = [c for c in cases if c["split"] == split]
    return cases


def classify(answer):
    """What kind of response did search_docs produce?"""
    if answer == tools.NO_MATCH:
        return "absent"
    if answer.startswith(tools.RESTRICTED_PREFIX):
        return "restricted"
    return "answer"


def score(cases):
    index = tools.index()   # the same index the tools use, so KB_DOCS cannot diverge
    results = []

    # `action` cases are commands that should invoke a tool. Retrieval is not
    # what they test, so scoring them here would measure the wrong thing and
    # dilute the number. eval/generate.py owns them.
    for c in [c for c in cases if c["kind"] != "action"]:
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

        leaks = leaks_in(answer, c["role"], c["q"])

        results.append({**c, "got": got, "ok": ok, "detail": detail, "leaks": leaks})

    return results


def summarise(results):
    """Totals for one set of results, used per split and overall."""
    by = {}
    for r in results:
        d = by.setdefault(r["kind"], [0, 0])
        d[0] += r["ok"]
        d[1] += 1
    para = [r for r in results if r.get("paraphrase")]
    return {"total": sum(r["ok"] for r in results), "n": len(results), "by": by,
            "para": sum(r["ok"] for r in para), "n_para": len(para),
            "leaks": sum(bool(r["leaks"]) for r in results)}


def split_table(results):
    """The headline. test is the number that has not been fitted to."""
    print(f"\n{'':<8}{'total':>12}{'answer':>10}{'restricted':>12}"
          f"{'absent':>9}{'paraphrase':>13}{'leaks':>7}")
    for split in ("tune", "test", "ALL"):
        rs = results if split == "ALL" else [r for r in results if r["split"] == split]
        if not rs:
            continue
        s = summarise(rs)
        cell = lambda k: (f"{s['by'][k][0]}/{s['by'][k][1]}" if k in s["by"] else "-")
        pct = 100 * s["total"] / s["n"]
        para = f"{s['para']}/{s['n_para']}" if s["n_para"] else "-"
        print(f"{split:<8}{s['total']:>5}/{s['n']:<3}{pct:>5.1f}%"
              f"{cell('answer'):>10}{cell('restricted'):>12}{cell('absent'):>9}"
              f"{para:>13}{s['leaks']:>7}")
    print("\n  tune  = thresholds were fitted on these, so this number flatters")
    print("  test  = never used for fitting anything. This is the honest one.")


def report(results):
    by_kind = {}
    for r in results:
        k = by_kind.setdefault(r["kind"], [])
        k.append(r)

    print(f"\nGOLDEN SET: {len(results)} retrieval cases, no model, no network")
    split_table(results)

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


BASELINE = os.path.join(HERE, "baseline.json")


def gate(results):
    """Fail the build if a case that used to pass no longer does.

    Not a percentage. A percentage hides a swap: fix one case, break another,
    and the score is unchanged while the system has quietly changed who it
    fails. It also moves whenever a question is added, so it cannot tell a
    regression from a bigger denominator.

    Per-case comparison has neither problem, and it is only honest because this
    scorer is deterministic -- cached vectors, no model, no sampling -- so a
    case that changes verdict changed for a reason. A gate this strict on a
    stochastic scorer would fire on noise and be switched off inside a week,
    which is the usual way a quality gate dies.
    """
    now = {r["id"] for r in results if r["ok"]}
    leaked = [r for r in results if r["leaks"]]

    if not os.path.exists(BASELINE):
        raise SystemExit(f"no baseline at {BASELINE}; write one with --accept")

    with open(BASELINE, encoding="utf-8") as f:
        base = json.load(f)
    was = set(base["passing"])

    broke = sorted(was - now)
    fixed = sorted(now - was)
    added = sorted({r["id"] for r in results} - set(base["all_ids"]))

    print(f"GATE  baseline {len(was)}/{len(base['all_ids'])} passing, "
          f"recorded {base['recorded']}")
    if fixed:
        print(f"  improved: {', '.join(fixed)}")
    if added:
        print(f"  new cases, not gated until accepted: {', '.join(added)}")
    for r in leaked:
        print(f"  LEAK  {r['id']} {r['role']} saw {r['leaks']}")
    for cid in broke:
        r = next(x for x in results if x["id"] == cid)
        print(f"  REGRESSED  {cid} {r['role']:<6} {r['q'][:50]!r}\n"
              f"             {r['detail'] or r['got']}")

    if broke or leaked:
        print(f"\nFAILED: {len(broke)} regression(s), {len(leaked)} leak(s).\n"
              f"Fix them, or accept the new behaviour deliberately with --accept.")
        return 1
    print("\nPASSED: nothing that worked is broken."
          + (f" {len(fixed)} case(s) improved." if fixed else ""))
    return 0


def accept(results):
    """Record current behaviour as the bar to hold.

    Deliberately a separate command. A gate that updates its own baseline when
    it fails measures nothing at all, so lowering the bar has to be a commit
    somebody can see in a diff.
    """
    import datetime
    body = {"recorded": datetime.date.today().isoformat(),
            "passing": sorted(r["id"] for r in results if r["ok"]),
            "all_ids": sorted(r["id"] for r in results)}
    with open(BASELINE, "w", encoding="utf-8") as f:
        json.dump(body, f, indent=1)
    print(f"baseline written: {len(body['passing'])}/{len(body['all_ids'])} passing")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--gate", action="store_true", help="exit 1 on a regression; for CI")
    ap.add_argument("--accept", action="store_true", help="record current results as the baseline")
    args = ap.parse_args()

    # Before anything is scored. Every leak number below is computed against
    # AUDIT_CLEARANCE, so if the running system no longer matches it, the two
    # halves of this file are measuring different systems and no result it
    # prints can be trusted. Loud and first, rather than a footnote under a
    # green table.
    drift = clearance_drift()
    if drift:
        print("CLEARANCE DRIFT: the system and the audit table disagree about "
              "who may read what.", file=sys.stderr)
        for line in drift:
            print(f"  {line}", file=sys.stderr)
        print("Fix rag.CLEARANCE, or change AUDIT_CLEARANCE in this file if the "
              "new access really is intended.", file=sys.stderr)
        raise SystemExit(2)

    results = score(load_golden())
    if args.json:
        json.dump(results, sys.stdout, indent=1)
        raise SystemExit(1 if any(r["leaks"] for r in results) else 0)
    if args.accept:
        raise SystemExit(accept(results))
    if args.gate:
        raise SystemExit(gate(results))
    raise SystemExit(report(results))
