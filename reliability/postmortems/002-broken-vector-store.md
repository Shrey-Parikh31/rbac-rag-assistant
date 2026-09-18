# Postmortem 002: a broken vector store

**Type:** deliberate experiment, runnable anywhere with
`python reliability/experiments/broken_index.py`. Runs in CI on every push.
**Status:** resolved.

## Summary

The vector store was broken four ways. The two loud failures were already
handled. The two quiet ones were not: the server started, reported itself
healthy, and served wrong results to everyone. One of them handed out **the wrong
document for half of all questions**, confidently, with no error anywhere and
no alert that could fire.

## Results

Eight golden-set questions per run, each with a known correct document, asked as
a student. The questions are drawn only from cases the recorded baseline says
currently pass, so the control scores 8/8.

**Before**

| Index | Starts | Reports healthy | Right document | Wrong document | Nothing |
|---|---|---|---|---|---|
| healthy | yes | yes | 8/8 | 0/8 | 0/8 |
| file missing | no | | | | |
| file truncated | no | | | | |
| document vectors from the wrong model | **yes** | **yes** | 0/8 | 0/8 | 8/8 |
| every document holding a neighbour's vector | **yes** | **yes** | 0/8 | **4/8** | 4/8 |

**After**

| Index | Starts | Why not |
|---|---|---|
| healthy | yes | |
| file missing | no | `4 text(s) are not in missing.json and no GEMINI_API_KEY is set` |
| file truncated | no | `JSONDecodeError` |
| wrong model | **no** | canary: `wanted salary-bands.md, got nothing` |
| shuffled | **no** | canary: `wanted salary-bands.md, got incident-response.md` |

## What each failure looked like from outside

**Missing and truncated** failed at startup with a clear message. In Kubernetes
that is a crash loop, the rollout is refused, and the previous pods keep serving,
which the cluster stage in CI already proves. Nothing to fix. This is the payoff
of an earlier decision: `rag.embed` raises rather than quietly falling back to
something weaker when a vector is missing.

**Wrong model** is what happens if the corpus is re-embedded with one model and
queries are embedded with another. Every score drops below the 0.61 floor and
every search returns "no match", with status 200. `KbIndexLooksBroken` would
eventually file a ticket: after 15 minutes of traffic, then 10 minutes pending.
So roughly half an hour of every user being told nothing exists.

**Shuffled** is the worst failure in this project so far. It is not an exotic
case: an off-by-one when rebuilding the index, or a cache keyed on something that
drifted, produces exactly this. Scores stay high because the vectors are real, so
results clear the floor, and they come back **from the wrong document**. A
student asking about late enrollment receives the grading policy, cited as
though it answers the question.

No alert could see it:
- availability: every request succeeded
- latency: unchanged
- `KbIndexLooksBroken`: a wrong answer is counted as an answer, not a miss

Only a question whose correct answer was already known could tell.

## The fix

The server now asks four canary questions at startup, one per document, each as
the role cleared to read it, and **refuses to start** if any comes back from the
wrong document or from none. Each canary is the golden-set case with the widest
margin over the similarity floor, so a failure means the index is wrong rather
than that a borderline question has drifted.

Exiting rather than starting unready matches how a missing `KB_TOKENS` is
handled. A crash loop is loud, a rolling update that ships it is refused at the
first pod, and the old pods keep serving.

## Lessons

**Health checks answer the question they are asked.** `/healthz` said "the
process is up and the index is built", which was true in every quiet failure. A
readiness check that does not exercise the thing users depend on reports on the
wrong thing, and does it reliably.

**The worst failure returns a plausible answer.** "No match" is at least honest;
a user can go and ask someone. The wrong document, cited, is believed. This is
the retrieval version of what Layer 3's judge found in the model: the dangerous
output is the confident one.

**Counting outcomes is not checking them.** The outcome metric and its alert were
built for this layer and are still worth having. But they count *kinds* of
answer. Knowing an answer is wrong requires already knowing the right one, and
the only place that knowledge exists is the golden set. The canaries borrow four
of its cases.

## Action items

| | Status |
|---|---|
| Canary questions at startup, one per document | done |
| Experiment runs in CI as a regression test | done |
| A canary per chunk once any document spans more than one | **open**: recorded in `serve.py` where it would need to change |
| Periodic canaries while running, not just at startup | **open**: the index is immutable after startup today, so a startup check covers it; it stops covering it the day the index can be reloaded without restarting |
