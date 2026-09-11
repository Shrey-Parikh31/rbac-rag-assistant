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

| Class | Word matching (was) | Meaning (now) |
|---|---|---|
| `answer` (a real document should come back) | 26/29 | **28/29, 97%** |
| `restricted` (report it exists, at the right level) | 7/9 | **8/9, 89%** |
| `absent` (no document covers it) | 7/10 | **9/10, 90%** |
| **Total** | 40/48, 83.3% | **45/48, 93.8%** |
| Leaks | 0 | **0** |
| False "restricted material exists" notices | 2/39 | **1/39, 3%** |
| Paraphrased questions | 4/8, 50% | **6/8, 75%** |

**The paraphrase row is what decided ADR-3.** Those eight questions deliberately
avoid the vocabulary of the document that answers them ("my marks look wrong"
against a document that says "grade appeal"). Word matching managed 50%. That
number was recorded *before* embeddings were tried, precisely so the comparison
could not be argued after the fact. Meaning matching reached 75%.

**Nothing was traded for it.** ADR-10 had found that character n-grams bought
better finding at the cost of much worse refusing, and that trade was expected
to repeat. It did not. Every category improved at once, including the ability to
say "no document covers that", which went from 7/10 to 9/10. The prediction was
wrong, and only the golden set could have shown that.

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

## What the sweeps decided

Three experiment scripts were written to settle three arguments with numbers
instead of taste, and all three have been **deleted now that they have answered**.
Their tables are below and in the decision record; the scripts are in git
history. An experiment that has answered its question is not a feature.

### The floor

Word matching scored any shared word above zero, so "what academic disciplines
does the university offer" matched the grading policy on the word "academic",
and the assistant answered confidently from a document about something else.

Sweeping the minimum similarity, under word matching:

| Floor | Total | `answer` | `restricted` | `absent` |
|---|---|---|---|---|
| 0.00 | 37/48 | 26/29 | 5/9 | 6/10 |
| 0.05 | 37/48 | 26/29 | 5/9 | 6/10 |
| **0.08** | **40/48** | **26/29** | **7/9** | **7/10** |
| 0.10 | 38/48 | 24/29 | 7/9 | 7/10 |
| 0.15 | 34/48 | 20/29 | 7/9 | 7/10 |
| 0.20 | 27/48 | 15/29 | 4/9 | 8/10 |

**0.08 was the peak**, and total moved **77.1% to 83.3%** for a one-line change.
That sentence was the deliverable, not the 83.3%.

The floor is now **0.635**, because the retriever changed and the old number was
swept for a different representation. A floor carried across that change would
have been quietly wrong rather than obviously so.

**And the first embedding sweep was too coarse.** Steps of 0.05 picked 0.65,
which cost q046: its correct document ranks *first* at 0.6423 and was excluded
by eight thousandths. A finer sweep shows a plateau and steep cliffs either
side:

| Floor | 0.62 | **0.63** | **0.64** | 0.65 | 0.66 |
|---|---|---|---|---|---|
| Total | 44/48 | **45/48** | **45/48** | 44/48 | 40/48 |

The shipped value is **0.635, the middle of the plateau rather than an edge**,
so one noisy question cannot push it off. A plateau this narrow, measured on 48
questions, is a fragile number: widen the golden set before trusting the third
decimal place.

### Spelling, meaning, and the trade that was not there

| Strategy | Total | finds doc | refuses right | says "none" | paraphrase | plural |
|---|---|---|---|---|---|---|
| word matching | 40/48 | 26/29 | 7/9 | 7/10 | 4/8 | fails |
| character n-grams | 37/48 | 28/29 | 7/9 | **2/10** | 6/8 | passes |
| **meaning (embeddings)** | **45/48** | **28/29** | **8/9** | **9/10** | **6/8** | **passes** |
| hybrid, words then meaning | 41/48 | 26/29 | 8/9 | 7/10 | 4/8 | passes |

Character n-grams are the cautionary row: better at finding, and they collapse
the ability to refuse, because when everything looks a bit similar to
everything, nothing looks like nothing. That result is why embeddings were
expected to cost something too. They did not.

The hybrid is the other useful negative. Consulting embeddings only when word
matching found nothing scores *worse* than embeddings alone, because word
matching keeps confidently "finding" wrong documents, so the fallback rarely
runs.

**Dimensionality.** 768 scored 44/48 against 45/48 at the full 3072. One
question out of 48, inside noise, for a quarter of the storage and arithmetic.
768 it is, and the 518 KB cache lives in the repository.

## The cache, and why the offline property survived

Word matching needed no network, and that is what let the tests and this scorer
gate every commit for free. Embeddings would have destroyed it: every search
becomes an API call.

The fix is `vectors.json`, committed, covering the corpus and every question in
the golden set. Tests and retrieval scoring still run offline and instantly. A
genuinely new query costs one API call and is then cached.

`rag.embed()` **raises rather than falling back** when a vector is missing and
no key is set. A silent downgrade to word matching would change what the tests
measure without announcing it, which is the single failure mode this project has
tripped over most often.

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

## End-to-end numbers

48 questions, live, `gemini-3.1-flash-lite`.

```powershell
.\.venv\Scripts\python.exe eval\generate.py               # run and score
.\.venv\Scripts\python.exe eval\generate.py --score-only  # re-score, free
```

| Metric | Word matching | Meaning |
|---|---|---|
| correctness | 36/38, 94.7% | **36/38, 94.7%** |
| refusal | 45/48, 93.8% | **46/48, 95.8%** |
| grounding (no restricted content disclosed) | 48/48, 100% | **48/48, 100%** |
| tool use | 48/48, 100% | **48/48, 100%** |
| tokens | 35,187 | 34,982 |
| cost | $0.00 | $0.00 |

### The uncomfortable result: the benchmark moved and the product did not

Retrieval scoring improved **83.3% to 93.8%**, a ten point jump. End-to-end
correctness moved **94.7% to 94.7%**. Not a rounding difference; the same two
questions fail before and after.

That is worth sitting with, because it is the opposite of what the retrieval
number predicts, and it has a clean explanation: **the model was already
compensating.** It rewrites the question before searching, and those rewrites
were doing the work that better retrieval now does. Improving a component that
something else was already covering for produces a smaller end-to-end gain than
the component's own score suggests.

The gains are real but narrower than they look: refusal improved, q046 was
recovered, the plural bug is fixed, and retrieval no longer depends on the model
happening to rewrite well. What did *not* happen is a jump in answer quality.

**The general lesson, and the reason both scorers exist:** a component benchmark
measures the component. It does not measure the system, and the difference is
exactly the work other parts of the system are quietly doing.

### Latency, and a wrong alarm

An early reading of this run said retrieval had made the system eight times
slower. That was wrong, and it is recorded because the mistake is instructive:
it compared two runs on different days against an API whose latency varies by
more than the effect being measured.

Measured properly, by timing the embedding call separately inside `agent.ask`:

| | |
|---|---|
| median retrieval | **0.00s** (most rewritten queries hit the cache) |
| median model | **3.46s** |
| retrieval share of total | **7%** |

The one alarming number, 14.4s for an embedding call, was **client startup**,
paid once per process, not per query. Warm calls run 1.4 to 2.5s and most cost
nothing at all.

`agent.ask` now returns `embed_s` and `model_s` alongside the total, because
"nine seconds" does not say which half to fix, and guessing got it wrong once.

### The headline: the model does real retrieval work

| | Score |
|---|---|
| Retrieval alone, raw question | 40/48, **83.3%** |
| End to end, correctness | 36/38, **94.7%** |

The offline scorer was called a lower bound before this run existed, on the
theory that the model rewrites the question before searching. That gap is the
theory being paid off. The model turns "What does late enrollment cost?" into
"late enrollment fee cost" and "What should I do if I think someone hacked my
account?" into "hacked account what to do", and several questions the raw
retrieval scorer marks as misses are answered correctly in practice.

The two are not the same denominator, so this is a direction rather than a
precise delta. The direction is what matters: **query rewriting is a real
component of the system, not a detail**, and any future retriever has to be
compared against the rewritten query, not the user's words.

### Every remaining failure is the same failure

q006, q017 and q036 are the *only* end-to-end failures, and all three were
already flagged offline as paraphrase misses. Not one is a generation problem.
The model answers correctly whenever retrieval hands it the right passage.

**Retrieval is the bottleneck, and this run measured that rather than assumed
it.** That is the strongest possible argument for reversing ADR-3 and trying
semantic retrieval, and it is now an argument backed by which questions failed
rather than by which technology is fashionable.

### Latency has a long tail

Median 1.2s, p95 13.0s. A tenfold spread on identical work. Nothing here
explains it and nothing here needs to yet, but an SLO written on the median
would be wrong for one request in twenty. That is Layer 2's problem and it now
has a number waiting for it.

### What grounding at 100% does and does not mean

It means **no restricted content reached anyone not cleared for it**, across
all 48 questions. That is the property worth having and it holds.

It does **not** mean the answers contain nothing invented. In this run the
model referred a staff member to an "official IT security portal" that exists
in no document. Staff are cleared for that material, so nothing tripped. The
scorer detects *disclosure*, not *invention*.

Also worth recording: the "IT Service Desk" invention seen on
`gemini-3.6-flash` did not recur on `gemini-3.1-flash-lite`. Hallucination
behaviour is model-specific, which is an argument for re-running this whole set
on any model change rather than assuming it transfers.

## On the model, and why it changed mid-layer

Layer 0 ran on `gemini-3.6-flash`. Partway through building this, that model
began refusing every request with a quota error that never cleared, while
`gemini-3.5-flash` and `gemini-3.1-flash-lite` served normally on the same key
and the same code. The free tier's ceiling is per model, and the newest model is
the most contended.

The evaluation therefore runs on `gemini-3.1-flash-lite`, pinned. It is also
three times faster (1.2s median against 3.5s) and uses about 25% fewer tokens.

`generate.py` **refuses to score a cache containing answers from more than one
model.** Six answers from the old model were already cached when the switch
happened, and averaging them with the new ones would have produced a number
describing neither, with the report confidently printing one model's name at the
top. That guard exists because it nearly happened.

## Not built yet

- **A judge-based groundedness scorer**, to catch invention rather than only
  disclosure. Needs a second model call per question and is the one place a
  judge genuinely earns its cost.
- **A regression gate in CI**, which is Layer 1's job. `--json` and the exit
  code are there for it.
- **The provider comparison promised in ADR-6.** The harness is now capable of
  it: set `KB_MODEL`, clear the cache, re-run, compare. It is a bill and twenty
  minutes, not a rewrite.
