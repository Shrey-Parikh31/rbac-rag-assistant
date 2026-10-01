"""Check what the model wrote before anyone reads it.

Every other control in this project runs *before* the model: clearance is bound
to the request, forbidden chunks never enter the ranking, and tools refuse
callers who may not use them. Those are the strong controls and they are where
the real safety lives.

This is the last one, and it guards a different thing: **what the model says
about what it was shown.** Retrieval can be perfect and the sentence still
wrong. Layer 3 found exactly that. Asked for the adjunct pay rate, the assistant
told a student to contact "the office responsible for faculty compensation", a
phrase it was never shown. It had inferred the subject of the document it was
refused, and it inferred correctly. Nothing leaked, every control held, and the
student learned what the confidential document was about.

So there are two checks, and both come from a failure that already happened:

1. **Contents.** The answer repeats restricted material. This is the one that
   cannot happen if retrieval is correct, which is exactly why it is checked:
   a control that depends on another control being right is not a second layer.
2. **Subject.** The answer names what a refused document is about, using a word
   that appears neither in the passages it was shown nor in the user's question.
   That second condition is the whole difficulty; see `described_subject`.

ponytail: string matching, not a classifier. The words below are the published
vocabulary of four documents, and matching them costs nothing and runs offline,
which means it runs on every request rather than when somebody remembers. The
ceiling is real and worth stating: **this catches the known words, not a
paraphrase.** A model that says "what teaching staff are paid annually" slips
past every entry here. The LLM-as-judge in eval/ is what catches that class, and
it costs a model call per answer, which is why it runs on a schedule and this
runs inline. Upgrade path if paraphrase leaks show up in judged runs: an
embedding similarity check against the restricted documents, which is affordable
because their vectors are already in memory.
"""

# The contents that must never reach a caller below the clearance owning them.
#
# Deliberately a separate list from SECRETS in eval/retrieval.py, which scores
# the golden set, and it must stay separate. That file holds the *checker's*
# opinion and this one holds the *system's*, and the whole lesson of Layer 4 is
# what happens when those are the same object: weaken one and you silently
# weaken the thing that would have caught you. test_guardrails.py asserts this
# list still covers that one, so they drift apart loudly instead of quietly.
SECRETS = {
    "confidential": ["78,000", "96,000", "95,000", "124,000", "120,000",
                     "187,000", "1,450"],
    "staff": ["one hour of discovery", "remediate", "five business days",
              "written postmortem"],
}

# What each restricted document is *about*. Naming any of these to somebody who
# was refused the document describes it, which is the failure in the docstring
# above. Distinct from SECRETS: a system can be flawless about contents and
# still answer twenty yes-or-no questions into a title.
SUBJECTS = {
    "confidential": ["compensation", "salary", "pay scale", "pay band",
                     "adjunct", "wages", "earnings", "payroll"],
    "staff": ["phishing", "ransomware", "malware", "breach", "incident response",
              "remediation", "on-call"],
}

CLEARANCE = {"student": {"public"},
             "staff": {"public", "staff"},
             "admin": {"public", "staff", "confidential"}}

# What a blocked answer is replaced with. A refusal, not an error: the caller
# asked a reasonable question and the fault is this service's, so it says what
# happened without inventing a reason or describing what it withheld.
BLOCKED = ("This answer was withheld by an output check: the draft reply "
           "referred to material you are not cleared to read. That is a fault "
           "in this service rather than in your question. Try asking more "
           "narrowly, or ask the office that owns the material directly.")


def above(role):
    """Classification levels `role` may not read."""
    allowed = CLEARANCE.get(role, {"public"})
    return [lvl for lvl in SUBJECTS if lvl not in allowed]


def leaked_contents(answer, role):
    low = answer.lower()
    return [s for lvl in above(role) for s in SECRETS.get(lvl, [])
            if s.lower() in low]


def described_subject(answer, role, question="", shown=""):
    """Subject words in the answer that it did not get from the user or a passage.

    The exclusions carry this function. Without them it fires constantly on
    correct behaviour, and an output check that blocks good answers is one
    somebody turns off within a week:

    - **What the user said.** A student who asks about salaries and is told
      "material about salaries exists and you may not read it" has learned
      nothing; they supplied the word.
    - **What the model was shown.** If a word appears in a passage the caller was
      legitimately given, repeating it is quoting a document they may read, not
      disclosing one they may not.

    What is left is the dangerous case and the one Layer 3 actually caught: a
    word in the answer with no source anywhere in the turn, which means the model
    produced it from its own knowledge of the question it was refused.
    """
    low, said, saw = answer.lower(), question.lower(), shown.lower()
    return [w for lvl in above(role) for w in SUBJECTS.get(lvl, [])
            if w in low and w not in said and w not in saw]


def check(answer, role, question="", shown=""):
    """(ok, reason, text). `text` is the answer, or the refusal that replaces it.

    `shown` is everything the tools returned during the turn, concatenated. The
    caller has it; this cannot reconstruct it, and guessing would turn the
    second check into a source of false blocks.
    """
    leaked = leaked_contents(answer, role)
    if leaked:
        return False, f"contents above clearance: {', '.join(leaked[:3])}", BLOCKED

    described = described_subject(answer, role, question, shown)
    if described:
        return False, f"named the subject of a refused document: {', '.join(described[:3])}", BLOCKED

    return True, "", answer


def demo():
    """Run with `python guardrails.py`. Each case is a failure that happened."""
    shown = "No passages you are cleared to read match that query, but material classified 'confidential' does match."

    # The Layer 3 finding, exactly. Nothing leaked; the subject was inferred.
    ok, why, text = check(
        "I can't show you that. Contact the office responsible for faculty "
        "compensation.", "student", question="what is the adjunct pay rate", shown=shown)
    assert not ok and "compensation" in why, why
    assert text == BLOCKED

    # The same sentence to somebody cleared for it is fine.
    ok, _, _ = check("Contact the office responsible for faculty compensation.",
                     "admin", question="pay rates", shown=shown)
    assert ok

    # The user said the word, so repeating it discloses nothing.
    ok, _, _ = check("Material about compensation exists and you may not read it.",
                     "student", question="what is the compensation policy", shown=shown)
    assert ok

    # The model was shown the word in a passage it was allowed to see.
    ok, _, _ = check("The grading policy mentions salary deductions.", "student",
                     question="grading", shown="... salary deductions ...")
    assert ok

    # Contents, which should be impossible if retrieval is right, and is checked
    # precisely because this layer must not assume that.
    ok, why, text = check("Assistant professors start at 78,000.", "student")
    assert not ok and "78,000" in why
    assert text == BLOCKED

    # A staff member reading staff material is not a leak.
    ok, _, _ = check("Report it within one hour of discovery.", "staff")
    assert ok
    ok, why, _ = check("Report it within one hour of discovery.", "student")
    assert not ok, "a student was shown staff contents"

    print("guardrails: ok")


if __name__ == "__main__":
    demo()
