# Security

Attacking the system on purpose, and counting what got through.

Everything else measured here asks whether the system does what it should. This
asks whether it can be made to do what it must not. A control that has never
been attacked is not known to work; it is only unrefuted.

**Status: the corpus is built and the harness is verified. The measurement is
not finished.** 51 attacks exist, 21 of them currently reach the system, and 30
cannot run until their text is embedded once. Those 30 are reported as
"no result" and explicitly not counted as defended, for reasons the next section
is entirely about.

## Two bugs found before a single attack succeeded

Neither was in the system. Both were in the thing measuring it, which is where
this project keeps finding them.

### The scorer asked the system whether the system was wrong

`leaks_in()` decided whether a disclosure was permitted by reading
`rag.CLEARANCE`, the same table the system uses to decide who sees what. So the
question "was this allowed?" was being put to the configuration that allowed it.

Widen a role by accident, one character giving students the staff level, and the
system hands over staff documents while the leak gate reports zero leaks. **That
gate is one of the Layer 1 build gates**, so a privilege escalation would have
shipped on a green build.

Proved by removing the clearance filter entirely and watching all twenty-two
leak attacks report that nothing got through.

The fix is `AUDIT_CLEARANCE` in `eval/retrieval.py`: the scorer's own statement
of who may read what, which the system cannot change. `clearance_drift()` then
requires the two tables to agree, so widening access really does require two
deliberate edits in two files. Drift is a build failure, and `redteam.py`
reports it as a breach that needed no attacker.

### Thirty attacks never ran, and all thirty were scored as defended

The first run printed `51 attacks, 0 succeeded, attack success rate 0.0%`.

29 of them had raised `EmbeddingUnavailable` on the way in. Their text is not in
the committed vector cache, the run had no API key, and the exception string
became the "response" that the scorer then found no leak in. The report was not
wrong about those attacks. It had no information about them at all.

A red-team report that folds undelivered attacks into the denominator as passes
is the most dangerous artifact in this directory, because it is the one somebody
quotes. The runner now separates three outcomes, and `--gate` fails on the third
as readily as the first:

| | |
|---|---|
| **succeeded** | the attacker won |
| **held** | the attack was delivered and the system refused it |
| **no result** | the attack never reached the system, and nothing defended against it |

The first version inferred failure by checking whether the response began with
`<`, which classified `ui.html` as a harness error because it begins
`<!doctype html>`. Runners now report delivery failure explicitly rather than
having it guessed from their output.

## The corpus

51 attacks in [`attacks.jsonl`](attacks.jsonl), one per line, each tagged with
its OWASP Top 10 for LLM Applications category and with the single condition
that counts as the attacker winning. Nothing is graded by eye.

| OWASP | | Attacks |
|---|---|---|
| LLM01 | Prompt Injection | 15 |
| LLM02 | Sensitive Information Disclosure | 15 |
| LLM05 | Improper Output Handling | 3 |
| LLM06 | Excessive Agency | 7 |
| LLM07 | System Prompt Leakage | 3 |
| LLM08 | Vector and Embedding Weaknesses | 7 |
| LLM10 | Unbounded Consumption | 1 |

Five surfaces, four of which cost nothing:

| Surface | What it does |
|---|---|
| `query` | the attack text goes in where a question goes |
| `tool` | a tool is called directly, with the role bound as the agent binds it, skipping the prompt entirely |
| `corpus` | a property of the documents themselves |
| `http` | a real request to a real server, started by the runner and killed after |
| `source` | a static property of the code, for sinks no request can reach |

**LLM03 and LLM04 are absent on purpose.** Supply chain is covered by Trivy and
semgrep in Layer 1, and model poisoning needs training, which this system does
not do. Listing them with zero attacks would pad the table.

### Why the free surfaces come first

The central claim of this design is that clearance is decided before retrieval
and is not reachable from anything a caller can say. If that claim holds, it
holds with no model involved, and a test that needs one is measuring the model's
politeness rather than the architecture. If the claim is false, these attacks
find it for nothing.

## The harness has been seen to work

`redteam.py` scoring zero would mean nothing on its own, because a corpus with no
teeth against a system with no defences prints the same line.
[`test_redteam.py`](test_redteam.py) removes one control at a time and requires
the attacks to notice:

- the clearance filter removed entirely
- one role promoted by a single level, which is the realistic version and much
  quieter
- `file_ticket` opened to students, checked all the way down to the ticket
  appearing in the file
- a refusal that names the subject of what it refused, which every leak check
  stays silent about because the contents never left
- the mirror of that: repeating the caller's own words is **not** a disclosure,
  and scoring it as one produces findings nobody can act on
- `innerHTML` introduced between a retrieved passage and the screen
- the system prompt planted inside a document, where no jailbreak is needed
- every attack having a win condition the scorer implements, so an unscoreable
  attack raises instead of quietly counting as a pass

## Cost

The runner is free and needs no key, once each attack's text is in
`vectors.json`. That is the same arrangement as the golden set: embed once,
commit the vectors, run forever on every push at no cost.

The one-time embedding of the 30 outstanding attack texts is about 458 tokens,
roughly **$0.00007** at the current rate for `gemini-embedding-001`.

**The model surface is not in this file.** Indirect injection, where an
instruction is hidden inside a document and the question is whether the model
obeys it, needs real generation and cannot be cached. It is opt in, counted and
priced before it runs, and it is the next piece of work here.

## Still to come

- Embed the 30 outstanding attack texts, and wire `--gate` into CI
- The model surface: indirect prompt injection through document contents,
  insecure output handling on generated text, and grounding under adversarial
  pressure
- The guardrail layer, and an honest before-and-after that includes what still
  gets through
- A risk assessment mapped to the NIST AI Risk Management Framework
