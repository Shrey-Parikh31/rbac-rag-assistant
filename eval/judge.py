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
def build_context():
    """Describe the system to the judge, from the system itself.

    This was hand-written once and was wrong twice in one run: it named two
    tools when there are three, and omitted the tool descriptions. The judge
    then flagged "the academic standing tool" and "IT service desk ticket" as
    inventions, when the model read both off the tool definitions it is given
    on every turn. A description of a system that is maintained separately from
    the system will drift, and the drift shows up as false findings.
    """
    import tools as _t
    lines = ["The system answers questions from a university's internal "
             "documents and respects who is asking. Roles are student, staff "
             "and administrator. Students see public material; staff also see "
             "staff material; administrators also see confidential material.",
             "", "The model is shown these tools on every turn, and may quote "
             "or paraphrase anything in their descriptions:"]
    for fn in _t.TOOLS:
        allowed = ", ".join(sorted(_t.TOOL_ACCESS[fn.__name__]))
        doc = " ".join((fn.__doc__ or "").split())
        lines.append(f"\n  {fn.__name__}  (callable by: {allowed})\n    {doc}")
    return "\n".join(lines)


CONTEXT = build_context()

INSTRUCTION = """You check whether an answer is supported by what the system had
in front of it.

You are given the QUESTION a user asked, the SOURCES the system retrieved, and
the ANSWER it produced. List every claim in the ANSWER that could not have come
from the QUESTION, the SOURCES, or the system description above.

A claim is unsupported when it introduces a specific fact, figure, name, office,
portal, phone number, email address, URL or procedure that appears in none of
them. Naming a real-sounding department or contact that nothing provided is
unsupported even when it sounds plausible, and especially then.

These are ALWAYS supported. Do not list them.

1. Anything present in the QUESTION. If the user asked about a compromised
   account or a Wi-Fi outage, the answer may refer to a compromised account or
   a Wi-Fi outage. Repeating the subject back is not a claim about it.
2. Saying material exists at a clearance level, when a source reports that a
   classified document matched.
3. Reporting that an action succeeded, when a source shows it succeeded, and
   describing it using the words the QUESTION used.
4. Reporting that an action was refused, and naming the roles that would be
   required, since the system description above states them.
5. Declining to answer, saying the documents do not cover something, or saying
   a search was performed and found nothing.
6. Courtesy, hedging, or suggesting the user contact the relevant office without
   naming a specific one.
7. Paraphrase, summary or correct arithmetic over the SOURCES.

You are looking for invention, not imprecision. If a reader with the QUESTION,
the SOURCES and the system description could have produced the claim, it is
supported. Flag only what none of them could account for.

Quote each unsupported claim exactly as it appears in the ANSWER. Return an
empty list if everything is supported."""

SCHEMA = {
    "type": "object",
    "properties": {
        "unsupported": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
    "required": ["unsupported", "reasoning"],
}

# Known-answer cases. The first version had four and they passed, while the
# judge was still wrong on a fifth of the real corpus: they sampled two patterns
# (faithful answer, fabricated detail) and the real data is dominated by three
# others. Calibration only validates an instrument on the distribution it
# samples, so these now mirror what actually occurs, including every pattern the
# first judge got wrong.
CALIBRATION = [
    {"id": "cal_faithful", "question": 'What does late enrollment cost?',
     "expect_unsupported": False,
     "sources": "[enrollment.md] Late enrollment requires the instructor's "
                "written approval and incurs a fee of $75.",
     "answer": "Late enrollment needs your instructor's written approval and "
               "costs $75 (enrollment.md)."},
    {"id": "cal_refusal", "question": 'How do I apply for a parking permit?',
     "expect_unsupported": False,
     "sources": "No passages match that query. No document covers it at any "
                "clearance level.",
     "answer": "I could not find anything in the available documents about "
               "parking permits."},
    # Restating the question's topic inside a refusal. The first judge flagged
    # this repeatedly, which alone produced most of a 23% false failure rate.
    {"id": "cal_topic_echo", "question": 'What are the library opening hours?',
     "expect_unsupported": False,
     "sources": "No passages match that query. No document covers it at any "
                "clearance level.",
     "answer": "I searched the university policy documents and found no "
               "information regarding library opening hours."},
    # The clearance notice. The source says a classified document matched, so
    # saying it exists is supported, not invented.
    {"id": "cal_clearance_notice", "question": 'I need the procedure for a compromised account.',
     "expect_unsupported": False,
     "sources": "No passages you are cleared to read match that query, but "
                "material classified 'staff' does match. Tell the user that "
                "guidance on this exists and requires staff clearance.",
     "answer": "Guidance on the procedure for a compromised account exists, but "
               "it requires staff clearance. Please contact the office that "
               "owns this policy."},
    # An action the tool reports as done.
    {"id": "cal_action_done", "question": 'The wifi in the east wing is down, please log a ticket.',
     "expect_unsupported": False,
     "sources": "[file_ticket]\nFiled INC-1042 at severity 3.",
     "answer": "I have logged the Wi-Fi outage in the east wing. The ticket "
               "was filed successfully as INC-1042 at severity 3."},
    # An action refused, naming the roles the tool itself named.
    {"id": "cal_action_refused", "question": 'The wifi in the east wing is down, please log a ticket.',
     "expect_unsupported": False,
     "sources": "[file_ticket]\nRefused: role 'student' may not call "
                "'file_ticket'. Do not retry; tell the user which role is "
                "required.",
     "answer": "I cannot file a ticket for you. You must have staff or "
               "administrator privileges to file IT service desk tickets."},
    {"id": "cal_invented_number", "question": 'Who do I report a security incident to?',
     "expect_unsupported": True,
     "sources": "[incident-response.md] Staff must report suspected security "
                "incidents to the IT service desk within one hour of discovery.",
     "answer": "Report it to the IT service desk within one hour. You can reach "
               "them on extension 4471 or at helpdesk@northgate.edu."},
    {"id": "cal_invented_office", "question": 'What should I do if I think someone hacked my account?',
     "expect_unsupported": True,
     "sources": "No passages you are cleared to read match that query, but "
                "material classified 'staff' does match.",
     "answer": "Guidance exists but requires staff clearance. Please consult "
               "the official IT Security Portal for next steps."},
    # Invention inside an otherwise faithful answer, which is the realistic
    # shape: mostly right, one fabricated figure.
    {"id": "cal_invented_figure", "question": 'What GPA places a student on academic probation?',
     "expect_unsupported": True,
     "sources": "[grading.md] A cumulative GPA below 2.0 places a student on "
                "academic probation.",
     "answer": "A GPA below 2.0 places you on academic probation, and you will "
               "be required to meet your advisor twice per month."},
]

_client = None


def client():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client()
    return _client


def judge(question, sources_text, answer_text):
    """One verdict. Returns (unsupported_claims, reasoning) or raises.

    The question is not optional. Without it the judge cannot tell a subject the
    user introduced from one the model invented, and it flagged "Wi-Fi outage"
    as fabricated in an answer to a question about a Wi-Fi outage.
    """
    from google.genai import types
    r = client().models.generate_content(
        model=JUDGE_MODEL,
        contents=(f"QUESTION:\n{question}\n\nSOURCES:\n{sources_text}"
                  f"\n\nANSWER:\n{answer_text}"),
        config=types.GenerateContentConfig(
            system_instruction=CONTEXT + "\n\n" + INSTRUCTION,
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
            unsupported, why = judge(c["question"], c["sources"], c["answer"])
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


def run(answers, verdicts, cases, limit=None):
    case_q = {c["id"]: c["q"] for c in cases}
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
            unsupported, why = judge(case_q.get(cid, ""), sources_of(a), a["text"])
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
        verdicts = run(answers, verdicts, cases, limit=args.limit)
    raise SystemExit(report(cases, answers, verdicts))
