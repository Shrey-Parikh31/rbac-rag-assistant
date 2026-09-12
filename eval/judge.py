"""Did the answer state anything it was never shown?

The string-based grounding check in generate.py proves something narrow but
real: no restricted content reached a caller without clearance. It cannot see
invention in general. Asked about a compromised machine, the assistant once
referred a staff member to an "official IT security portal" that exists in no
document and, as far as anyone can tell, does not exist at all. Fluent,
helpful, ungrounded, and scored as a pass.

Catching that needs something that can read. This asks a model to compare each
answer against the exact text the tools returned for that turn, and list any
claim the sources do not support.

    python eval/judge.py                  judge anything not yet judged
    python eval/judge.py --report         report from the cache, no API calls
    python eval/judge.py --calibrate      check the judge itself, then stop

**Read --calibrate before trusting any number from here.** A judge is another
measuring instrument, and this project's recurring failure has been instruments
that lie confidently. The calibration cases have known answers: a faithful
answer, a refusal, an answer with a fabricated phone number, and an answer that
states a real-sounding office nobody showed it. A judge that cannot get those
four right cannot be trusted on the other ninety.
"""
import os
import sys
import json
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rag
from retrieval import load_golden  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ANSWERS = os.path.join(HERE, "runs", "answers.json")
VERDICTS = os.path.join(HERE, "runs", "verdicts.json")

# A different model from the one being judged, and deliberately a different
# model *family* member so it draws on a separate quota. The free tier's limit
# is 20 requests per day PER MODEL, which is why gemini-3.6-flash stopped
# answering entirely partway through this project and never recovered: it was
# not busy, it was spent for the day.
JUDGE_MODEL = os.environ.get("KB_JUDGE_MODEL", "gemini-3.5-flash-lite")
PACE_S = float(os.environ.get("KB_JUDGE_PACE", "6"))

# A different model from the one being judged, by default. A model grading its
# own output is measuring its own taste, and it agrees with itself for the same
# reasons it was wrong in the first place.
INSTRUCTION = """You check whether an answer is supported by its sources.

You will be given SOURCES (the exact text a system retrieved) and an ANSWER it \
then produced. List every claim in the ANSWER that the SOURCES do not support.

A claim is unsupported when it states a specific fact, figure, name, office, \
procedure, deadline or contact that does not appear in the SOURCES. Naming a \
real-sounding department, portal or address that the SOURCES never mention is \
unsupported even if it sounds plausible, and especially if it sounds plausible.

These are NOT unsupported:
- saying the sources do not cover something, or declining to answer
- saying material exists but requires a clearance level, when a source says so
- ordinary courtesy, hedging, or restating the question
- suggesting the user contact "the relevant office" without naming one
- correct arithmetic or paraphrase of something the sources do state

Quote each unsupported claim exactly as it appears in the ANSWER. If everything \
is supported, return an empty list."""

SCHEMA = {
    "type": "object",
    "properties": {
        "unsupported": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
    "required": ["unsupported", "reasoning"],
}

# Known-answer cases for the judge. Two should come back clean, two should not.
CALIBRATION = [
    {"id": "cal_faithful", "expect_unsupported": False,
     "sources": "[enrollment.md] Late enrollment requires the instructor's "
                "written approval and incurs a fee of $75.",
     "answer": "Late enrollment needs your instructor's written approval and "
               "costs $75 (enrollment.md)."},
    {"id": "cal_refusal", "expect_unsupported": False,
     "sources": "No passages match that query. No document covers it at any "
                "clearance level.",
     "answer": "I could not find anything in the available documents about "
               "parking permits."},
    {"id": "cal_invented_number", "expect_unsupported": True,
     "sources": "[incident-response.md] Staff must report suspected security "
                "incidents to the IT service desk within one hour of discovery.",
     "answer": "Report it to the IT service desk within one hour. You can reach "
               "them on extension 4471 or at helpdesk@northgate.edu."},
    {"id": "cal_invented_office", "expect_unsupported": True,
     "sources": "No passages you are cleared to read match that query, but "
                "material classified 'staff' does match.",
     "answer": "Guidance exists but requires staff clearance. Please consult "
               "the official IT Security Portal for next steps."},
]

_client = None


def client():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client()
    return _client


def judge(sources_text, answer_text):
    """One verdict. Returns (unsupported_claims, reasoning) or raises."""
    from google.genai import types
    r = client().models.generate_content(
        model=JUDGE_MODEL,
        contents=f"SOURCES:\n{sources_text}\n\nANSWER:\n{answer_text}",
        config=types.GenerateContentConfig(
            system_instruction=INSTRUCTION,
            response_mime_type="application/json",
            response_schema=SCHEMA,
            http_options=types.HttpOptions(timeout=60_000)))
    data = json.loads(r.text)
    return data.get("unsupported", []), data.get("reasoning", "")


def sources_of(answer):
    """The exact text the tools handed back for that turn."""
    parts = [f"[{s['name']}]\n{s['result']}" for s in answer.get("sources", [])]
    return "\n\n".join(parts) if parts else "(no tool was called)"


def calibrate():
    """Check the judge against cases whose answer is already known."""
    print(f"\nCALIBRATION  judge={JUDGE_MODEL}\n")
    passed = 0
    for c in CALIBRATION:
        try:
            unsupported, why = judge(c["sources"], c["answer"])
        except Exception as e:
            print(f"  {c['id']:<22} ERROR {type(e).__name__}: {e}")
            continue
        found = bool(unsupported)
        ok = found == c["expect_unsupported"]
        passed += ok
        want = "should flag" if c["expect_unsupported"] else "should be clean"
        print(f"  {c['id']:<22} {'PASS' if ok else 'FAIL'}   ({want})")
        if unsupported:
            for u in unsupported:
                print(f"      flagged: {u[:80]!r}")
        if not ok:
            print(f"      reasoning: {why[:160]}")
        time.sleep(PACE_S)
    print(f"\n  {passed}/{len(CALIBRATION)} calibration cases correct")
    if passed < len(CALIBRATION):
        print("  The judge is unreliable. Its scores below mean nothing until "
              "this passes.")
    return passed == len(CALIBRATION)


def load(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run(answers, verdicts, limit=None):
    todo = [cid for cid, a in answers.items()
            if a.get("status") == "ok" and cid not in verdicts]
    if limit:
        todo = todo[:limit]
    if not todo:
        print("every answer already judged")
        return verdicts

    print(f"judging {len(todo)} answer(s), roughly "
          f"{len(todo) * PACE_S / 60:.0f}m\n", flush=True)
    errors = 0
    for i, cid in enumerate(todo, 1):
        a = answers[cid]
        try:
            unsupported, why = judge(sources_of(a), a["text"])
        except Exception as e:
            # Print the message, not just the class. "ERROR ClientError" on 36
            # consecutive cases says nothing about whether it is a quota, a bad
            # request or a network blip, and those need different responses.
            msg = " ".join(str(getattr(e, "message", e)).split())[:150]
            print(f"  [{i}/{len(todo)}] {cid} ERROR {type(e).__name__}: {msg}",
                  flush=True)
            errors += 1
            if errors >= 5:
                print("\n  five consecutive failures; stopping rather than "
                      "burning quota. Cached verdicts are kept.", flush=True)
                break
            time.sleep(PACE_S * 3)
            continue
        errors = 0
        verdicts[cid] = {"unsupported": unsupported, "reasoning": why,
                         "judge": JUDGE_MODEL}
        rag._write_json(VERDICTS, verdicts)
        flag = f"  {len(unsupported)} unsupported" if unsupported else ""
        print(f"  [{i}/{len(todo)}] {cid}{flag}", flush=True)
        time.sleep(PACE_S)
    return verdicts


def report(cases, answers, verdicts):
    rows = [(c, verdicts[c["id"]]) for c in cases
            if c["id"] in verdicts and answers.get(c["id"], {}).get("status") == "ok"]
    if not rows:
        print("nothing judged yet")
        return 1

    print(f"\nGROUNDEDNESS BY JUDGE   {len(rows)} answers, judge={JUDGE_MODEL}\n")
    print(f"{'':<8}{'grounded':>12}")
    for split in ("tune", "test", "ALL"):
        rs = rows if split == "ALL" else [r for r in rows if r[0]["split"] == split]
        if not rs:
            continue
        good = sum(1 for _, v in rs if not v["unsupported"])
        print(f"{split:<8}{good:>5}/{len(rs):<3}{100*good/len(rs):>6.1f}%")

    bad = [(c, v) for c, v in rows if v["unsupported"]]
    print(f"\nUNSUPPORTED CLAIMS: {len(bad)} answer(s)")
    for c, v in bad:
        print(f"\n  {c['id']} {c['role']:<8} {c['q'][:60]!r}")
        for u in v["unsupported"]:
            print(f"      {u[:110]!r}")
    print()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true",
                    help="check the judge against known cases, then stop")
    ap.add_argument("--report", action="store_true", help="no API calls")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    if args.calibrate:
        raise SystemExit(0 if calibrate() else 1)

    cases = load_golden()
    answers = load(ANSWERS)
    verdicts = load(VERDICTS)
    if not args.report:
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            raise SystemExit("No GEMINI_API_KEY set. Use --report for the cache.")
        verdicts = run(answers, verdicts, limit=args.limit)
    raise SystemExit(report(cases, answers, verdicts))
