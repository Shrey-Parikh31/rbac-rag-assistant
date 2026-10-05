# Risk assessment, mapped to the NIST AI Risk Management Framework

Against **AI RMF 1.0** (NIST AI 100-1, January 2023) and the **Generative AI
Profile** (NIST AI 600-1, July 2024).

## What this document is, and what it is not

It is a register of the risks this system actually carries, each one pointing at
a file or a number that exists in this repository. **Every control named below
has evidence, and every control without evidence says so in the same row.** That
is the only thing separating a risk assessment from a template with a logo.

It is **not** an organisational governance programme. The AI RMF GOVERN function
assumes an organisation: a risk committee, a legal review, an incident
communication policy, workforce training. There is one person here. Claiming
GOVERN coverage would be the exact failure this project keeps finding, a control
that is reported as present because a document says so.

The corpus is four fictional documents. Nothing here processes real personal
data, and no number below should be read as a claim about a system that does.

## MAP: what this is

| | |
|---|---|
| **Purpose** (Map 1.1) | Answer questions from a university's internal policy documents, returning only material the asker is cleared to read |
| **Users** | Students, staff and administrators, authenticated by bearer token |
| **Task** (Map 2.1) | Retrieval-augmented question answering with role-based access control. Retrieval is semantic (embeddings and cosine similarity); generation is a hosted model (Gemini) |
| **In production right now** | Retrieval only. Generation is disabled on the public deployment (`KB_ASK_DISABLED=1`), so the model path described below is a development and evaluation path |
| **Knowledge limits** (Map 2.2) | A similarity floor of 0.61, fitted on a tune split. Below it, the system reports that nothing matched rather than returning the closest paragraph |
| **Human oversight** (Map 3.5) | None at request time. The system is advisory: it returns passages and cites the source file, and a reader can open that file |
| **Third-party components** (Map 4.1) | Google Gemini (embeddings, generation), Google Cloud Run, GitHub Actions, Grafana Cloud, a `python:3.12-slim` base image |
| **Risk tolerance** (Map 1.5) | One disclosure of restricted material is unacceptable. Availability is explicitly not: the SLO is 99.5%, chosen to be honest about one small container on a free tier |

**The asymmetry in that last row drives everything below.** This is not a system
where risks trade off against each other evenly. Confidentiality has a budget of
zero and availability has a budget of 3.6 hours a month.

## The register

Likelihood and impact are this author's judgement. The evidence column is not.

### R1. Restricted contents reach a caller who may not read them

**Impact: severe. Likelihood: low.** The risk the whole system exists to prevent.

| Control | Evidence |
|---|---|
| Clearance is bound to the request before the model is involved, and is never read from anything the caller can type | ADR-13; `tools.py` binds a `contextvar`, there is deliberately no header or parameter path |
| Chunks above the caller's clearance are removed from the candidate set **before** ranking, so their existence cannot be inferred from a gap or a score | `rag.py` `Index.search`, ADR-2 |
| One implementation of the rule, shared by HTTP, MCP and the agent | `serve.py` calls `tools.search_docs` rather than the index; ADR-1 |
| 91 golden questions scored on every push, zero leaks on the held-out split | `eval/retrieval.py --gate` |
| 51 attacks across five surfaces, 51 delivered, 0 successful | `security/redteam.py --gate`, run in CI |
| An output check on the generated answer, as a second layer | `guardrails.py` |
| An external probe asserts the refusal still holds in production, and pages on a single failure | `observability/probe.py`, `KbAccessControlFailing` |

**Residual:** the attack corpus is 51 attacks written by the person who built the
defence, which is the weakest form of red teaming there is. A stranger with an
hour would try things not in that file.

### R2. The *subject* of a refused document is disclosed

**Impact: high. Likelihood: medium.** The subtle one, and the one that actually
happened.

Layer 3 caught the assistant telling a student to contact "the office
responsible for faculty compensation", a phrase it was never shown. Contents
never left the server. Every control in R1 held. The student learned what the
confidential document was about anyway.

| Control | Evidence |
|---|---|
| The refusal names the clearance level required and nothing else, not the title, subject or owning office | ADR-7 and ADR-12, `tools.py` `RESTRICTED_PREFIX` |
| The system prompt forbids describing refused material except in the user's own words | `agent.py` `SYSTEM` |
| An output check blocks answers naming a refused document's subject, when the word came from neither the question nor a shown passage | `guardrails.py`, `security/test_guardrails.py` |
| `/corpus` counts what it hides and never names it | `serve.py`, probe check `corpus` |

**Residual, and it is stated as a passing test rather than a footnote:** the
output check matches known words, not meaning. "The office that handles what
teaching staff are paid annually" passes. Catching that needs the LLM-as-judge,
which costs a model call per answer and therefore runs on a schedule rather than
inline.

### R3. Confabulation: an answer invented when nothing matched

**Impact: high. Likelihood: medium.** GenAI Profile: *Confabulation*,
*Information Integrity*.

| Control | Evidence |
|---|---|
| A similarity floor, so "nothing found" is a possible outcome rather than a ranking artifact | `rag.MIN_SCORE = 0.61`, fitted on the tune split only; ADR-9 |
| The system prompt instructs answering only from retrieved passages, and citing the source file | `agent.py` |
| A second model judges whether each claim is supported by what was retrieved | `eval/judge.py` |
| The outcome mix is a metric, and an index that silently starts finding nothing files a ticket | `kb_search_outcomes_total`, `KbIndexLooksBroken` |

**Residual:** the golden set now scores 100% on the model-facing metrics, which
means it has stopped discriminating. A measure that cannot fail is not currently
measuring anything, and harder cases are outstanding work.

### R4. Direct prompt injection

**Impact: severe if it worked. Likelihood: low.** GenAI Profile: *Information
Security*.

| Control | Evidence |
|---|---|
| Structural, not instructional: there is no code path that reads a role from caller-supplied text, so there is no sentence that grants access | ADR-13 |
| 15 injection attacks, including forged system turns, claimed authority, delimiter escapes and role-play | `security/attacks.jsonl`, category LLM01 |

**Residual:** none identified at the retrieval layer, which is a strong claim and
rests on the architecture rather than on filtering, which is why it is credible.

### R5. Indirect prompt injection, through document contents

**Impact: high. Likelihood: unknown. THIS IS THE LARGEST OPEN RISK.**

An instruction hidden inside a document the model is legitimately shown, telling
it to do something it should not. The system prompt tells the model that passage
text is quoted material and never an instruction. **That instruction has never
been tested.**

| Control | Evidence |
|---|---|
| The system prompt states that retrieved text is quoted material | `agent.py` `SYSTEM`. **No test.** |
| No tool can escalate clearance even if the model were persuaded to try, so the blast radius is bounded by R1's controls | ADR-1, ADR-13 |
| The corpus is four files under version control, so planting text requires a reviewed commit | git history |

**Why it is open:** testing it needs real generated answers, which cost money
against a prepaid budget shared with another project. It is scoped and priced
work, not an oversight. The bound in the second row is the reason it is not
severe: an obeyed injection could make the assistant say something foolish, but
it cannot make retrieval return a document the caller may not read.

### R6. A control that looks like it works and does not

**Impact: severe. Likelihood: high. This is the dominant risk in this system,
and it is the one with a measured base rate: 22 occurrences.**

Every entry in this register is a claim backed by a measurement. The measurement
being wrong is therefore a meta-risk that compromises every other row at once,
and in this project it has been the most common defect by a wide margin.

| Control | Evidence |
|---|---|
| Every gate has been made to fail on purpose before being trusted | throughout: `security/test_redteam.py` removes one control at a time; `alerts.test.yaml` includes negative cases |
| The leak scorer holds its own clearance table that the system cannot change, and a disagreement fails the build | `eval/retrieval.py` `AUDIT_CLEARANCE`, `clearance_drift()` |
| The guardrail keeps a separate copy of the restricted vocabulary, with a test asserting it still covers the scorer's | `security/test_guardrails.py` |
| An attack that cannot be delivered is reported separately and fails the gate, never counted as defended | `security/redteam.py` |
| The red-team run refuses to report at all unless the corpus loaded and something in it is restricted | `assert_corpus_loaded()` |
| A dead man's switch, because a monitor that stops reporting looks like a system with nothing to report | `KbProbeStopped` |

**Residual:** this is managed, not solved. The base rate says the next one
already exists and has not been found yet.

### R7. The model provider becomes unavailable or slow

**Impact: medium. Likelihood: high.** Free-tier quota makes this an ordinary
operating state, not an exception.

| Control | Evidence |
|---|---|
| 15-second timeout, circuit breaker after three consecutive slow failures, bulkhead of 8 concurrent calls | postmortem 003, measured 63.4s per user before, 0.00s for the majority after |
| `/search` never calls the provider, so retrieval survives a total outage | postmortem 003, verified during the experiment |
| Breaker state is a metric with an alert | `kb_breaker_state`, `KbBreakerOpen` |

### R8. A token is leaked, and revoking it does not revoke it

**Impact: severe. Likelihood: low.**

| Control | Evidence |
|---|---|
| The token map is mounted as a file and re-read within a second, so rotation takes effect without a restart | postmortem 004, measured 53 seconds across pods, from never |
| A malformed rotation is rejected and logged, with the previous map kept, so a typo during an incident does not lock everyone out | `serve.py` `Tokens`, unit tested |
| Whether every process has picked up a rotation is a query rather than a probe loop | `kb_tokens_version`, `KbRotationIncomplete` |

**Residual, and it is a real one:** the staff and administrator demo tokens have
appeared in a chat transcript and a screenshot during development. They have not
been rotated. The owner judged this acceptable for a demo whose corpus is
fictional, which is a defensible call and is recorded here rather than omitted.

### R9. Supply chain

**Impact: medium. Likelihood: medium.** GenAI Profile: *Value Chain and
Component Integration*.

| Control | Evidence |
|---|---|
| Critical CVEs with an available fix block publication | Trivy in CI; caught three real criticals in `perl-base` on its first run |
| Static analysis blocks on findings | semgrep; found SHA1 in the cache key, changed to SHA256 rather than suppressed (ADR-17) |
| Non-root container, keyless deployment authentication | `Dockerfile` user 10001, Workload Identity Federation |
| The external probe has no dependencies at all, so the thing that watches the service has no supply chain of its own | `observability/probe.py`, standard library only |

**Residual:** HIGH severity vulnerabilities are reported and do not block. A
pinned base image is rebuilt on somebody else's schedule.

### R10. Monitoring is blind, and nobody knows

**Impact: medium. Likelihood: medium.** A monitoring gap causes no incident by
itself; it removes the ability to see the ones that happen.

| Control | Evidence |
|---|---|
| Two independent sources, one inside the process and one outside the deployment | `observability/` |
| Alert rules are unit tested, including negative cases | `alerts.test.yaml`, 19 cases |
| Dashboard queries are parsed in CI and refused if they name a metric nothing emits | `check_dashboards.py` |
| A dead man's switch for the probe itself | `KbProbeStopped` |

**Already happened, and it is why this row exists:** the probe alert windows
were sized from the cron expression rather than from the delivery rate. GitHub
runs the schedule roughly every three hours, not every fifteen minutes, so a
ten-minute window was empty and `KbUnreachableFromOutside` could not fire at all.
Windows are six hours now.

**Residual:** the Grafana credentials are not set, so nothing is currently stored
or alerting on. The probe runs, measures, and discards.

### R11. The user's question is itself sensitive

**Impact: medium. Likelihood: low.** GenAI Profile: *Data Privacy*.

A question can disclose more than an answer. "What happens if I fail this
semester" is information about the person asking.

| Control | Evidence |
|---|---|
| The access log records the path and never the query string | `serve.py` `log_message` |
| Metric labels come from a fixed set, so no question can mint a time series | `Metrics.ROUTES`, `route="other"` for anything else |
| `/metrics` exposes counts and timings, never a question, a token or a passage | `serve.py` |
| Query embeddings on the server are held in memory only and never written back to disk | `KB_READ_ONLY_CACHE=1`, `rag._embed_uncached` |

**Found while writing this document:** the log redaction had **no test**. Every
test spawned the server with stderr discarded, so nothing had ever read what it
wrote. One edit to that format string would have put every question a user typed
into the logs, and nothing would have failed. There is now a test that sends a
distinctive private question and asserts it never reaches stderr.

### R12. Over-reliance on the answer

**Impact: medium. Likelihood: medium.** GenAI Profile: *Human-AI
Configuration*.

| Control | Evidence |
|---|---|
| Every answer cites its source file, and the match score is shown | `ui.html`, `/search` response |
| The interface states plainly that it finds passages rather than writing answers, and that a refusal is correct behaviour | `ui.html` |
| Generation is off in production, so what a user sees is document text rather than a paraphrase of it | `KB_ASK_DISABLED=1` |

**Residual:** there is no feedback channel for a user who believes an answer is
wrong (Measure 3.3). For a demo that is acceptable; for a real deployment it is
the first thing to add.

## AI RMF coverage, honestly

| Subcategory | Status |
|---|---|
| Map 1.1 purpose and context documented | **met**, `client/01-requirements.md` |
| Map 1.5 risk tolerance determined | **met**, stated above and visible in the SLO choice |
| Map 2.1 task and methods defined | **met**, `README.md`, ADRs |
| Map 2.2 knowledge limits documented | **met**, the similarity floor and its fitting |
| Map 2.3 TEVV considerations identified | **met**, `eval/README.md` |
| Map 3.5 human oversight defined | **partial**, the system is advisory and cites sources; no reviewer in the loop |
| Map 4.1 third-party risks mapped | **met**, listed above, scanned in CI |
| Map 5.1 impact likelihood and magnitude | **partial**, judged by one person, not reviewed |
| Measure 2.1 test sets and tools documented | **met**, 91 golden questions with a tune/test split |
| Measure 2.3 performance in deployment-like conditions | **met**, k6 load test, chaos experiments on a real cluster |
| Measure 2.4 behaviour monitored in production | **met in design**, blocked on credentials |
| Measure 2.5 validity and reliability limits documented | **met**, four documents and 91 questions, stated everywhere a number is quoted |
| Measure 2.7 security and resilience evaluated | **met**, 51 attacks plus four chaos experiments |
| Measure 2.8 transparency and accountability risks | **partial**, source citation and ADRs; no model card |
| Measure 2.9 model explained and output interpreted in context | **partial**, retrieval is fully explainable and the score is shown; the generation step is not |
| Measure 2.10 privacy risk examined | **met**, R11 |
| Measure 2.11 fairness and bias | **not assessed** |
| Measure 2.12 environmental impact | **not assessed** |
| Measure 2.13 TEVV effectiveness evaluated | **met, and unusually well**, see R6 |
| Measure 3.1 tracking emergent risks | **partial**, CI gates catch regressions; nothing watches for new risk classes |
| Measure 3.3 user feedback and appeal | **not met**, R12 |
| Manage 1.2 risks prioritised | **met**, this document |
| Manage 1.3 responses to high-priority risks planned | **met** for R1 to R4 and R6 to R11; **open** for R5 |
| Manage 1.4 residual risk documented for downstream users | **met**, every section has a residual paragraph |
| Manage 2.3 respond to a previously unknown risk | **met**, four postmortems and five worked tickets |
| Manage 2.4 ability to deactivate | **met**, traffic moves by revision; the previous one is one command away |
| Manage 3.1 third-party risks monitored | **partial**, scanned at build; no runtime monitoring of the model provider's behaviour |
| Manage 4.1 post-deployment monitoring | **met in design**, blocked on credentials |
| Manage 4.3 incident communication and tracking | **partial**, postmortems and runbooks exist; there is nobody to notify |
| **GOVERN, all 19 subcategories** | **not applicable at this scale**, and claiming otherwise would be the failure described at the top |

**Two are not assessed and both deserve naming rather than a dash.** Fairness and
bias (Measure 2.11) is not meaningless here: retrieval quality may differ by
phrasing, and a student who writes less formally could get worse answers than one
who writes like the policy document. That is a measurable question on the
existing golden set and it has not been asked. Environmental impact (Measure
2.12) is unmeasured, though token counts per turn are recorded, which is the
input such an estimate would need.

## If this were going to carry real documents

In order:

1. **Have somebody else attack it.** The single weakest thing about R1 is that
   the attacks were written by the defender.
2. **Close R5**, the model-level injection path, before enabling generation for
   real users.
3. **A feedback and appeal route** (Measure 3.3), because a confidentiality
   system with no way to report a wrong refusal pushes people around it.
4. **Real identity**, not bearer tokens in a file. SSO, with group membership
   as the clearance source.
5. **Ask the fairness question** on the golden set, which costs nothing but time.
6. **An owner for the alerts.** Every runbook here currently ends with the same
   person reading it.
