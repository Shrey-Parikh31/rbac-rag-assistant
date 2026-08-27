# Evaluation

Layer 3. The purpose is not a score. It is being able to say *why* an answer was
wrong, and whether a change helped.

## The split that makes this cheap

An answer can fail two ways, and they have different fixes:

1. **Retrieval failed.** The right passage was never fetched. No amount of
   prompt engineering fixes this.
2. **Generation failed.** The right passage was fetched and the model mangled
   it, or added something that was not there.

Measuring them together produces a number nobody can act on. This directory
scores them separately, and scoring retrieval needs **no model, no API key and
no network**, so it runs in under a second and can gate every commit.

```powershell
.\.venv\Scripts\python.exe eval\retrieval.py          # report
.\.venv\Scripts\python.exe eval\retrieval.py --json   # for the Layer 1 gate
```

Exit code is 1 if any leak is detected, so it is already usable as a build gate.

## Current numbers

48 questions, retrieval only.

| Class | Pass |
|---|---|
| `answer` (a real document should come back) | 26/29, 90% |
| `restricted` (should report material exists, at the right level) | 7/9, 78% |
| `absent` (no document covers it) | 7/10, 70% |
| **Total** | **40/48, 83.3%** |
| Leaks | **0** |
| False "restricted material exists" notices | 2/39, 5% |
| Paraphrased questions | 4/7, 57% |

**The paraphrase row is the important one.** Those seven questions deliberately
avoid the vocabulary of the document that answers them ("my marks look wrong"
against a document that says "grade appeal"). 57% is what lexical retrieval can
do, and it is the number semantic retrieval has to beat to justify ADR-3 being
reversed. Without it, switching to embeddings would be a preference. With it,
it is a decision.

## The golden set

`golden.jsonl`, one question per line:

```json
{"id": "q015", "role": "student", "kind": "answer", "source": "grading.md",
 "q": "My marks look wrong, who do I complain to?", "paraphrase": true}
```

`kind` is the expected behaviour, and there are exactly three:

- **`answer`** — a document the caller is cleared to read should come back, and
  `source` names which one. Returning *a* document is not a pass; returning the
  *right* document is.
- **`restricted`** — nothing readable matches, but material at `level` does. The
  notice must name the correct clearance level.
- **`absent`** — no document covers this at any clearance level.

### How it was built, and what was rejected

Curation is the part that takes judgement, so the rules are written down:

**Every role appears on both sides of every boundary.** Students asking for
staff material, staff asking for confidential material, and administrators
asking for the same things and getting real answers. A set that only tests
denial cannot tell a working rule from a broken index, which is exactly the trap
Layer 0 fell into: an access-control test passed for months because *nothing*
could be retrieved.

**About 15% are deliberate paraphrases.** They share no distinctive vocabulary
with the document that answers them. These are marked `"paraphrase": true` and
reported separately, because they are the measurement that decides the
embeddings question.

**`absent` questions are plausible, not silly.** Parking, library hours, dress
codes. A university could hold policies on all of them; this one does not.
Asking "what is the airspeed of a swallow" would pass trivially and prove
nothing.

**Two known failures are kept in, marked `known_false_positive`.** q029 and q030
are questions that wrongly trigger the restricted notice. Deleting them would
raise the score and lose the regression marker. They are there to fail until
something fixes them.

**Rejected during curation:**

- Questions with more than one defensible right answer. If two reasonable people
  disagree about the expected result, the scorer is measuring the labeller.
- Questions whose answer depends on facts outside the corpus. Those measure the
  model's world knowledge, which is the opposite of what a grounded assistant
  should use.
- Trick phrasings and adversarial prompts. Those belong in Layer 4, scored
  against attack success rate, not mixed in here where they would quietly drag
  down a retrieval metric.

**On size.** The roadmap said 60 to 100. This is 48, and that is a deliberate
stop: it covers every behaviour class at every role with paraphrase coverage,
and adding questions that duplicate an existing class inflates the denominator
without improving what the set can detect. Grow it when a real failure appears
that the set would not have caught. That is a better rule than a target number.

## What the sweep decided

`sweep.py` exists to answer one question with evidence instead of taste. TF-IDF
scores any shared word above zero, so "what academic disciplines does the
university offer" matched the grading policy on the word "academic", and the
assistant answered confidently from a document about something else.

Sweeping the minimum similarity:

| Floor | Total | `answer` | `restricted` | `absent` |
|---|---|---|---|---|
| 0.00 | 37/48 | 26/29 | 5/9 | 6/10 |
| 0.05 | 37/48 | 26/29 | 5/9 | 6/10 |
| **0.08** | **40/48** | **26/29** | **7/9** | **7/10** |
| 0.10 | 38/48 | 24/29 | 7/9 | 7/10 |
| 0.15 | 34/48 | 20/29 | 7/9 | 7/10 |
| 0.20 | 27/48 | 15/29 | 4/9 | 8/10 |

**0.08 is the peak.** Below it, nonsense matches survive. Above it, real matches
start dying: `answer` falls from 26 to 24 at 0.10 and to 15 at 0.20. The value
now lives in `rag.MIN_SCORE`, and this table is why it is 0.08 and not a number
that felt about right.

Total moved **77.1% to 83.3%** for a one-line change. That sentence is the
deliverable, not the 83.3%.

`sweep.py` should be deleted once nobody is arguing about the floor. It is an
experiment, not a feature.

## Known limitation, and it matters

**This scores the raw user question. The real system does not send that.**

The model rewrites the question before calling `search_docs`. Asked "what should
I do if I think someone hacked my university account", the live system searched
something closer to "account compromised security incident" and found the right
document. The same question scored offline here fails, because none of those
words appear in the corpus.

So these numbers are a **lower bound** on the real system, and the gap between
them is the value the model's query rewriting adds. That gap is worth measuring
on purpose rather than discovering by accident, and it is the first thing the
end-to-end scorer should report.

## Not built yet

- **End-to-end scoring**, which needs the API and is therefore slow and rate
  limited: correctness, groundedness, tool-call accuracy, latency, tokens, cost.
- **The groundedness scorer specifically.** Layer 0 caught the model inventing
  "the IT Service Desk or IT Security team" when its source said only "the office
  that owns it". Plausible, helpful, and not in any retrieved document. That is
  the behaviour this scorer exists to catch, and it appeared within five live
  questions.
- **A regression gate in CI**, which is Layer 1's job. `--json` and the exit code
  are there for it.
