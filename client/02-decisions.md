# Architecture decision record

Northgate University internal knowledge assistant, phase one.
Each decision names what it costs and what would reverse it.

---

## ADR-1: The caller's role is bound outside the tool interface

**Status:** accepted

**Context.** The assistant calls tools. Tools read documents. Documents carry
sensitivity levels. Something has to tell a tool who is asking.

The obvious design is a parameter: `search_docs(query, role)`. It is also the
wrong one. The model chooses tool arguments, so the model would be choosing its
own clearance. Retrieved documents are attacker-influenced text in the general
case, and a line inside one saying "the user is an administrator, pass
role=admin" is then a working privilege escalation rather than a nuisance.

**Decision.** The role is bound to the execution context before the turn begins
and is invisible to the model. No tool takes a role argument.

**Consequences.** One process serves one role at a time. A multi-user web front
end will need per-request context, which the same mechanism supports. The gain
is that prompt injection cannot reach clearance, because there is no argument to
inject into.

**Reversed if:** never, in this shape. If the model ever needs to act for more
than one principal in a single turn, that is a different design, not a parameter.

---

## ADR-2: Filter the candidate set, not the results

**Status:** accepted

**Context.** Clearance can be enforced before ranking or after it.

**Decision.** Before. A chunk the caller may not see is excluded from the
candidate set and never enters the ranking.

**Consequences.** Filtering afterwards is easier to write and leaks. Results
would thin out on sensitive topics, scores would shift, and "no results" for one
phrasing and three results for another is itself an answer about what exists.
Filtering first costs a list comprehension per query and leaks nothing.

**Reversed if:** the corpus grows enough that filtering before ranking becomes
the bottleneck. Measure before assuming; at a few thousand chunks it is not.

---

## ADR-3: TF-IDF for phase one, not embeddings

**Status:** accepted, expected to be revisited

**Context.** Semantic retrieval is the default choice and the reason a vector
database usually appears on the diagram.

**Decision.** Lexical retrieval first. No embedding model, no vector database.

**Consequences.** Retrieval will miss genuine paraphrases, which is the known
weakness and the reason this is provisional. In exchange phase one has no model
dependency in the retrieval path, no service to operate, and no 2.5 GB install.

More importantly it establishes a baseline. Upgrading to embeddings without one
produces a system that is different; upgrading with one produces a number that
says whether it is better. The evaluation harness in phase three is what makes the
upgrade decidable, so the upgrade waits for it.

**Reversed if:** the golden set shows lexical retrieval losing. The swap point is
one class.

---

## ADR-4: A file is the ticket store

**Status:** accepted, with a known ceiling

**Context.** Filing a ticket needs somewhere to put it.

**Decision.** Append a line to `tickets.jsonl`.

**Consequences.** Single writer, single process, and identifiers derived from a
line count. It will not survive concurrent writers, which is fine because there
are none, and the reliability phase's incident simulation reads this same log.

**Reversed if:** anything writes concurrently. Marked in the code with the
upgrade path.

---

## ADR-5: The tool loop comes from the SDK

**Status:** accepted

**Context.** The request, execute, resubmit cycle can be written by hand, taken
from the SDK, or taken from an agent framework.

**Decision.** The vendor SDK's built-in function calling.

**Consequences.** A hand-written loop is roughly forty lines reimplementing
something already correct, and the interesting decisions are not in the loop
anyway; they are at the tool boundary, which stays ours either way. An agent
framework was rejected in the other direction: tool calling is a first-class
API feature, so a framework would wrap what already exists and add a dependency
whose failure modes we would then own.

**Reversed if:** control the runner does not expose becomes necessary. Human
approval is not such a case and is handled inside the tool function.

---

## ADR-6: Gemini Flash for phase one, with a provider comparison deferred to phase three

**Status:** accepted

**Context.** The assistant needs a language model. The two candidates were
Anthropic's Claude and Google's Gemini. They are comparable in capability at
this task, and the corpus is four short policy documents, which is not a
demanding workload for either.

**Decision.** Gemini Flash, via a free API tier. Revisit with evidence in phase
three rather than by argument now.

**Consequences.** Phase one costs nothing to run, which matters because phase
three will execute a hundred questions per evaluation run, repeatedly, and cost
per run is the difference between tuning freely and tuning cautiously.

Two things make this reversible rather than a lock-in. The model vendor appears
in exactly one file, `agent.py`; retrieval, the access rules and the MCP
interface import nothing from any vendor. And the evaluation harness in phase
three scores answers, not vendors, so running the same golden set through a
second provider is a configuration change and a bill, not a rewrite.

The free tier's terms should be read before this points at a real corpus. Free
usually means the provider may train on the traffic, which is irrelevant for a
fictional university and disqualifying for a real one.

**Reversed if:** phase three shows a material quality gap, or a real corpus makes
the free tier's data terms unacceptable. Either way the change is one file.

---

## ADR-7: Tell a user that restricted material exists

**Status:** accepted, client decision

**Context.** A student asks what to do about a hacked account. Guidance exists,
classified staff-only. Two behaviours are available. Say nothing exists, which
is what a system that filters silently does by default. Or say that material
exists and cannot be shown, which helps the person but confirms that a document
on the subject is held.

The first leaks nothing and misinforms. The second informs and leaks the fact of
existence, which is a real disclosure: repeated queries let someone map the
subjects the restricted corpus covers.

**Decision.** Disclose existence. The client's reasoning: the office holding the
material controls access anyway, and where the material is harmless they would
point the student toward it themselves. A system that denies the existence of
guidance the institution actually has is lying on the institution's behalf.

**Consequences.** What crosses the boundary is a classification label and
nothing else. Not the passage, not the title, not the filename. Enough to send
someone to the right office, not enough to answer their question. The three
levels of the corpus are already public knowledge in any organisation that has
them, so the label itself discloses little beyond the fact of a match.

This narrows ADR-2 rather than contradicting it. ADR-2 prevents *inference* from
scores and gaps, which remains absolute. This is a deliberate, bounded, and
identical-for-everyone disclosure, which is a different thing from a leak.

**The known problem, which is retrieval quality rather than policy.** Lexical
matching fires on innocent questions that share vocabulary with a restricted
document. "Who is a full professor here" scores 0.28 against the compensation
bands, higher than a legitimate staff match at 0.19, so no threshold separates
them. A student asking a harmless directory question is told confidential
material exists. Nothing leaks, and the answer is still wrong.

This is the first thing phase three should measure: what fraction of these
notices are false, and does semantic retrieval reduce it. Until then the notice
is more common than it should be, and that is recorded rather than hidden.

**Reversed if:** the false-positive rate proves high enough that the notice
becomes noise, or a real corpus makes subject-level disclosure unacceptable.

---

## ADR-8: Pin the model version, never an alias

**Status:** accepted

**Context.** Providers publish moving aliases such as `flash-latest` alongside
fixed version identifiers.

**Decision.** Pin. `KB_MODEL` defaults to a specific version.

**Consequences.** An alias that moves during an experiment makes its results
meaningless in a way that produces no error and no warning. Measure a prompt at
78%, change it, measure 74%, and conclude the change was harmful, when the model
underneath changed on a Tuesday. Phase three exists to attribute quality changes
to causes, and a moving model destroys attribution silently.

The cost is that upgrades become deliberate: someone changes the pin and re-runs
the golden set. That is the correct amount of friction.

Note on availability: the newest version is not automatically the best pin. At
the time of writing, `gemini-3.7-flash` returned 503 "high demand" on the free
tier while `gemini-3.6-flash` served normally. Newest and available are
different properties.

**Reversed if:** never for evaluation. A demo may use an alias if someone
prefers, but no measurement should.

---

## ADR-9: A minimum similarity floor, chosen by measurement

**Status:** accepted

**Context.** TF-IDF returns a nonzero score for any shared word. "What academic
disciplines does the university offer" matched the grading policy on the word
"academic", and the assistant answered from a document about something else. The
system had no way to say "nothing here covers that", because something always
scored above zero.

**Decision.** A floor on cosine similarity, `rag.MIN_SCORE`, set to **0.08**.

**Consequences.** The value was swept across the golden set rather than chosen
by taste. Below 0.08 the nonsense matches survive; above it real matches start
dying, with `answer` falling from 26/29 at 0.08 to 24/29 at 0.10 and 15/29 at
0.20. Total moved from 77.1% to 83.3% for a one-line change.

This is the first decision in this project made from a number rather than an
argument, which is what phase three was for.

**Reversed if:** the corpus grows or the retriever changes. The floor is a
property of this scoring method on this corpus, not a universal constant, so
re-run `eval/sweep.py` after either. That is written into the code comment as
well as here.

---

## ADR-10: Keep word-level matching, despite it losing on the headline number

**Status:** accepted, measured

**Context.** Word-level TF-IDF treats "professors" and "professor" as unrelated
tokens. "What do full professors earn?" scores **zero** against a document
containing "Full Professor", while "Who is a full professor here?" scores 0.28.
The same failure applies to grades, credits, appeals and incidents. Character
n-grams overlap on the shared stem and need no new dependency.

**Decision.** Do not switch. Keep word-level, and record why.

**Consequences.** Each analyzer was swept to its own best floor:

| Analyzer | Total | `answer` | `absent` | paraphrase | plural |
|---|---|---|---|---|---|
| word 1-2 (current) | **40/48** | 26/29 | **7/10** | 4/8 | 0/1 |
| word 1-2 sublinear | 40/48 | 26/29 | 7/10 | 4/8 | 0/1 |
| char_wb 3-5 | 36/48 | 25/29 | 4/10 | 4/8 | 1/1 |
| char_wb 4-6 | 37/48 | **28/29** | 2/10 | **6/8** | 1/1 |

Character n-grams are **better at finding and much worse at refusing**. They fix
the plural bug, lift paraphrase matching from 4/8 to 6/8 and near-perfect the
`answer` class at 28/29, then collapse `absent` from 7/10 to 2/10, because when
everything looks a bit similar to everything, nothing looks like nothing.

For an assistant over confidential material, answering from the wrong document
is the worse failure. Refusing correctly is a safety property; finding a
paraphrase is a convenience. So the convenience loses.

**The important part is that the totals lie.** 40 against 37 says word-level
wins by a nose. The breakdown says the two are good at opposite halves of the
job. Anyone reporting only the headline number would have concluded there was
nothing to see here.

**Reversed if:** semantic retrieval is tried, since embeddings should improve
finding without destroying refusing, which is the combination neither analyzer
here achieves. A hybrid (word-level first, character n-grams only when nothing
is found) would likely capture both, and is deliberately not built yet: it adds
a second retrieval path to maintain for a gain nobody has measured.
