# Evaluation

Layer 3. The purpose is not a score. It is being able to say *why* an answer was
wrong, and whether a change helped.

Every number here has been wrong at least once, and each time the instrument was
broken rather than the system. That history is kept deliberately: it is the most
useful thing this layer produced.

---

## The three scorers

An answer can fail in ways that need different fixes, so they are measured
separately.

| | What it catches | Cost |
|---|---|---|
| `retrieval.py` | the wrong passage was fetched | free, offline, instant |
| `generate.py` | wrong facts, wrong refusal, wrong tool, leaked content | one API call per case |
| `judge.py` | claims invented out of nothing | one API call per case |

The judge found two real defects no string check could reach, both of the same
shape: the assistant described a document it had been **refused**. Asked for the
adjunct pay rate it said "contact the office responsible for faculty
compensation"; asked about reporting timeframes it said "the documentation
regarding security procedures". Nothing named either. The confidential file is
in fact titled "Faculty Compensation Bands", so the model was **right**, from
training, about a document it was correctly denied. The content stayed secret and
the subject did not. See ADR-12.

```powershell
.\.venv\Scripts\python.exe eval\retrieval.py     # free, gates every commit
.\.venv\Scripts\python.exe eval\generate.py      # live, ~40 min for 91
.\.venv\Scripts\python.exe eval\judge.py         # groundedness
.\.venv\Scripts\python.exe eval\judge.py --calibrate   # check the judge first
```

Retrieval scoring needs no key and no network, because the vector cache is
committed. That is what lets it run on every commit rather than on a good day.
Its exit code is 1 on any leak, so it is already a usable build gate.

---

## tune and test

**The reported score used to measure its own tuning.** The similarity floor was
swept across all 48 questions and then reported on all 48. The number flattered
by an unknown amount and nothing said so.

Every case now carries a `split`, assigned by hashing its id and stratified by
kind and role, so no human chose what to hold back. Thresholds are fitted on
`tune`. `test` is read once, at the end.

### Current numbers

**Retrieval**, 85 cases:

| | total | answer | restricted | absent | paraphrase | leaks |
|---|---|---|---|---|---|---|
| tune | 52/55, 94.5% | 33/34 | 10/10 | 9/11 | 9/10 | 0 |
| **test** | **28/30, 93.3%** | 19/19 | 5/5 | 4/6 | 4/4 | **0** |
| all | 80/85, 94.1% | 52/53 | 15/15 | 13/17 | 13/14 | 0 |

**End to end**, 91 cases, `gemini-3.1-flash-lite`:

| | correctness | refusal | grounding | tool use |
|---|---|---|---|---|
| tune | 44/44 | 59/59 | 59/59 | 59/59 |
| **test** | **24/24** | **32/32** | **32/32** | **32/32** |

**Groundedness by judge**: 59/59 tune, 32/32 test, **91/91, zero unsupported
claims**. Median 2.5s, p95 7.7s, 79,996 tokens, $0.00 on the free tier.

**Everything passes, and that is a limitation rather than a result.** A set with
no failing case cannot tell an improvement from a regression, which is exactly
the criticism levelled at tool use when it scored 100% across a set containing
no case that could fail it. These 91 have now been used to find and fix five
real defects; they have largely stopped discriminating. The next useful work on
this layer is harder cases, not a higher number.

The tune-to-test gap is the honest cost of having written both the documents and
the tune questions. It is reported, not hidden.

---

## The dataset

91 cases in `golden.jsonl`, one per line. `kind` is the expected behaviour:

- **`answer`** — a document the caller may read should come back, and `source`
  names which one. Returning *a* document is not a pass.
- **`restricted`** — nothing readable matches but material at `level` does, and
  the notice must name the right level.
- **`absent`** — nothing covers this at any clearance level.
- **`action`** — a command that must invoke a tool, or must be blocked from it.

### What was added, and why

It started at 48 and every addition closed a hole where the set could not fail:

| Added | Because |
|---|---|
| 14 statements and imperatives | every case was a question with a "?" |
| 4 informal, with typos | every case was written in clean prose |
| 4 multi-fact | no case needed two facts in one answer |
| 6 more paraphrases (14 total) | this is the measurement that decides ADR-3 |
| 6 more uncovered topics | refusing needs as much coverage as answering |
| **6 action cases** | **tool use scored 100% on a set with no case that could fail it** |

That last row is the important one. Tool use was reported at 100% across a set
containing no command that required filing a ticket and none that required being
blocked from filing one. **A metric with no available failing case is not
evidence.** The action cases exist so that number can be wrong.

### Curation rules

**Every role appears on both sides of every boundary.** A set that only tests
denial cannot tell a working rule from a broken index, which is exactly the trap
Layer 0 fell into: an access-control test passed for weeks because *nothing*
could be retrieved, not because the rule worked.

**Paraphrases avoid the document's vocabulary entirely.** "My marks look wrong"
against a document that says "grade appeal".

**`absent` cases are plausible, not silly.** Parking, library hours, dress codes.
A university could hold policies on all of them; this one does not. "What is the
airspeed of a swallow" would pass trivially and prove nothing.

**Known failures stay in, marked.** q029 wrongly triggers the clearance notice.
Deleting it would raise the score and hide the problem.

**Rejected:** cases with more than one defensible answer (the scorer would be
measuring the labeller), cases needing knowledge outside the corpus, and
adversarial prompts, which belong to Layer 4 and would drag down a retrieval
metric if mixed in here.

---

## Decisions the data made

### Spelling versus meaning

Each strategy swept to its own best floor, on the 48-case set:

| Strategy | Total | finds doc | refuses right | says "none" | paraphrase | plural |
|---|---|---|---|---|---|---|
| word matching | 40/48 | 26/29 | 7/9 | 7/10 | 4/8 | fails |
| character n-grams | 37/48 | 28/29 | 7/9 | **2/10** | 6/8 | passes |
| **meaning (embeddings)** | **45/48** | 28/29 | 8/9 | **9/10** | 6/8 | passes |
| hybrid, words then meaning | 41/48 | 26/29 | 8/9 | 7/10 | 4/8 | passes |

Character n-grams are the cautionary row: better at finding, and they collapse
refusing, because when everything looks a bit similar to everything, nothing
looks like nothing. Embeddings were expected to cost something similar. They did
not, which only the golden set could have shown.

The hybrid is the useful negative. Falling back to meaning only when words find
nothing scores *worse* than meaning alone, because word matching keeps
confidently finding wrong documents, so the fallback rarely runs.

### Author keywords

Policy documents are written in policy language. People ask in their own. The
incident document says "suspected compromise" and "remediate"; the person typing
says "someone hacked my account" and "can I just clean it up myself".

A student asking the second was told **no information exists**, when guidance
exists and is staff-only. Retrieval scored it 0.5872 against a floor of 0.62.

**Rewording the reply was rejected.** The system genuinely did not know the
document existed, so a canned "requires staff clearance" would have been a lie
every time it was right that nothing exists.

Instead every document carries author-written subject keywords in its front
matter, the same job a librarian does with subject headings. They are embedded
with the chunk and never shown in an answer.

| | before | after |
|---|---|---|
| q017 "hacked my account" | 0.5872 ✗ | 0.6401 ✓ |
| q036 "clean up a compromised machine" | 0.5839 ✗ | 0.6219 ✓ |

**Watch this one.** Keywords lifted tune from 92.7% to 94.5% and left test at
90.0%. The gain appeared only on questions I had already seen. Without the split
it would have looked like a clean improvement.

### Visibility was outvoting relevance

A student asked what happens when student data is exposed. The *grading* policy
scraped in at 0.6307, barely over the floor and about the wrong subject. Having
found something, the system never checked whether restricted material existed.
The incident policy, which actually answers it, scored 0.6775 and was never
consulted.

The rule had been "if nothing you can read matches, check restricted". That
treats matching as yes-or-no when it is a degree. `search_docs` now compares the
best visible match against the best restricted one.

**The margin is deliberately zero and deliberately unfitted.** Sweeping 0.00 to
0.12 gives 52/55 on tune at *every* value: tune contains no case where a weak
visible match competes with a strong restricted one, so it cannot validate the
number. Taking the midpoint of a flat band would be a magic number in the
costume of a fitted one.

**Honest caveat:** this flaw was noticed via a held-out case. The mechanism was
fixed rather than the case, and nothing was fitted on test, but the test split is
marginally less pristine for this change than for the others. The remedy is tune
cases that exercise this pattern, so the next change to it can be validated.

---

## The judge, and why it must be calibrated first

`generate.py` proves something narrow: no restricted content reached a caller
without clearance. It cannot see invention. Asked about a compromised machine,
the assistant once referred a staff member to an "official IT security portal"
that exists in no document and, as far as anyone can tell, does not exist at all.
Fluent, helpful, ungrounded, and scored as a pass.

`judge.py` asks a second model to compare each answer against the exact text its
tools returned. **It refuses to be trusted before passing nine known-answer
calibration cases**, and that guard has already paid for itself twice.

**First failure: it passed 4/4 and was still wrong on a fifth of the corpus.**
Those four sampled two patterns, a faithful answer and a fabricated detail. The
real data is dominated by three others: a refusal restating the question's topic,
a clearance notice, and a tool reporting an action. Calibration only validates an
instrument on the distribution it samples.

**Second failure: the judge could not see the question.** It was shown only the
sources and the answer, so when a user asked about a Wi-Fi outage and the
assistant said "I have logged the Wi-Fi outage", the judge flagged the outage as
invented. From where it sat, it was. It now receives the question and a
description of the system's roles and tools. 9/9.

Both failures were the same mistake: withholding context from a measurement and
then believing the pessimistic number it produced. **A measurement missing
context does not return "unknown". It returns a confident wrong answer.**

---

## Every instrument bug, in order

Kept because the pattern is the lesson.

1. An access-control test passed because nothing could be retrieved at all.
2. The scorer classified responses by string-matching the tool's wording.
3. Tool calls were read from the response, which is always empty in chat mode,
   reporting "tool use 0%" beside "correctness 100%". Two numbers contradicting
   each other is what exposed it.
4. "not available in the provided documents" was not recognised as a refusal.
5. A cache mixing two models was nearly averaged into one score.
6. A class swap silently did not apply, and the resulting table said embeddings
   scored 0/29, which read as a result rather than a bug.
7. The refusal detector could not tell "I will not answer" from "the answer is
   no". Both contain "cannot".
8. Leak canaries fired on words the user typed first.
9. Three of four staff leak canaries appear in a tool description the model is
   shown every turn, so they could never detect a real leak while appearing to.
10. Attempting a forbidden tool was graded a failure, when attempting and being
    refused is the designed flow.
11. The judge's calibration sampled the easy half of the distribution.
12. The judge was never shown the question.

**Eleven of twelve were the ruler, not the thing being measured.** When a product
breaks it tells you. When a ruler breaks it keeps printing plausible numbers and
you make decisions on them for weeks.

---

## Known limitations

**The offline scorer tests the raw question; the live system rewrites it first.**
Asked "What does late enrollment cost?", the model actually searches "late
enrollment fee cost". So the retrieval number is a lower bound. This cannot be
fixed without making it slow and paid, and it is now measured rather than
guessed.

**Grounding proves no restricted content leaked, not that nothing was invented.**
The judge covers the second, and is itself an instrument with a known history.

**Correctness is checked by required facts, not by reading.** `must_include`
catches a missing figure, not a well-written wrong explanation.

**91 cases is small.** A one-case difference is noise, and several decisions here
rest on differences of one or two. Widen the set before trusting any third
decimal place.

---

## Wiring it into the build (Layer 1)

```
python eval/retrieval.py --gate     exit 1 on a regression or a leak
python eval/retrieval.py --accept   record current behaviour as the bar
```

`eval/baseline.json` holds the **set of case ids that pass**, not a score.
ADR-14 has the argument; the short version is that a percentage cannot see a
swap, and it moves whenever a question is added, so a bigger golden set reads as
a regression and the fix for that is a tolerance sized in advance.

New cases are reported and not gated until somebody runs `--accept`. That is a
separate command on purpose: a gate that updates its own baseline when it fails
measures nothing, so lowering the bar has to appear in a diff.

**It has been watched failing.** Raising `MIN_SCORE` from 0.61 to 0.68 produced
exit 1, fifteen named regressions, and four improvements — and that combination
is the case for this design, because a percentage gate would have reported the
net and blocked nothing.

This gate is affordable per push only because the scorer needs no model. The
live evaluation in `eval/generate.py` and `eval/judge.py` costs roughly 250 model
requests against a free tier of 20 per day **per model**, so it runs on demand
from `.github/workflows/eval.yml` and reports. The offline one gates.

### Instrument bug fifteen

The gate passed the first time it was run, on a golden set where every case
already passed. That is the same shape as the access-control test that passed
because nothing could be retrieved, and the tool-use metric that read 100% on a
set containing no case that could fail it. A gate is not known to work until it
has refused something, which is why breaking `MIN_SCORE` on purpose is written
down above as a step rather than remembered as a thing that was probably done.

### Instrument bug sixteen

`test_serve.py` started a server on a fixed port and waited for `/healthz` to
answer. A server left running from an earlier session answers instantly, so the
spawn loop saw a healthy service and the whole suite tested **the stale process**
— its code, and its credentials.

It was caught by a question that should have been impossible. The suite is meant
to prove the system serves retrieval with no API key, and a question with no
cached vector got embedded anyway. Six abandoned servers were listening, one of
them started with a key in its environment.

The port is now requested from the operating system, which removes the collision
rather than detecting it, and the spawn loop notices a server that exits
immediately instead of waiting thirty seconds to say nothing useful.

Same family as the other fifteen. A test that connects to something other than
the thing it started passes for a reason unrelated to the code under test, and
the passing looks exactly like the real thing.

### Instrument bug seventeen

The cluster stage reported `FAIL: service went down during a failed deploy`. The
service had not gone down. The rollout was correctly refused and the pods were
serving; the check was wrong.

`kubectl port-forward svc/kb` reads as though it goes through the Service and
does not — it selects a single pod and tunnels straight to it. So it reported
one pod's health while appearing to report the Service's, and it happened to
survive a pod deletion on one run and die on the next.

Service-level questions are now asked from inside the cluster, where the Service
name resolves through real cluster DNS across real endpoints.

Seventeen now, and the shape has not changed since the first one: **a
measurement that looks like it is watching the thing it names.** An access test
that passed because nothing could be retrieved. A judge that could not see the
question. A metric at 100% on a set with no case that could fail it. A test that
connected to a stale server from an earlier session. A tunnel to one pod
answering a question about a Service.

Every one of them passed while being wrong, which is the only reason the list is
this long — a measurement that fails loudly gets fixed the same afternoon.
