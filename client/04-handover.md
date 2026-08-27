# Handover: phase one

Northgate University internal knowledge assistant
To: Office of the Registrar, IT Services
From: Shrey Parikh

## What was delivered

A question-answering assistant over your policy documents that respects who is
asking. Ask in plain language, get an answer with the source document cited, or
get told plainly that the documents do not cover it.

Three things it can do: look up policy, apply the academic standing rule, and
file an IT ticket. Ticket filing is restricted to staff and administrators,
because it is the only one with an effect outside the system.

The same three capabilities are available over MCP, so tools you already use can
reach them without a second implementation and without a second copy of the
access rules to keep in step.

All five acceptance criteria are met. Criteria one and two are enforced by
automated tests that run without a network, which matters more than the criteria
themselves: the clearance rules are checkable by you, at any time, without
trusting a demo.

## What it does not do

Identity is configured per deployment, not per user. The system enforces
clearance correctly once told who is asking, and is not yet told by anything
trustworthy. **It should not be exposed to students in this state.**

Answer quality is unmeasured. It behaves well on the questions we tried, which
is not evidence, and no accuracy figure should be quoted to anyone until phase
three exists.

Retrieval is lexical. It will miss paraphrases that a person would consider
obvious. This is a deliberate stopping point rather than an oversight; the
reasoning is in ADR-3.

There are no logs. If someone reports a bad answer next week, there is currently
no artifact that would let us find out why.

## One behaviour you chose, now built

A student asking about staff-only material is told that guidance exists and that
they are not cleared to read it, rather than being told nothing exists. Your
reasoning is recorded in ADR-7: the office holding the material controls access
regardless, and where it is harmless they would point the student there
themselves.

It works, and it comes with a caveat you should know before anyone relies on it.
The notice also fires on innocent questions that merely share vocabulary with a
restricted document. "Who is a full professor here" matches the compensation
bands more strongly than a genuine staff question matches its own document, so
no simple threshold fixes it. Nothing confidential leaks in either case. The
answer is just wrong, and it will be wrong often enough to notice.

That is a retrieval-quality problem, not a policy one, and it is the first thing
phase three should put a number on.

## The finding worth your attention

Late in phase one, a query using a document's own title returned nothing for a
user cleared to read it, while a differently worded query returned the same
document immediately. Titles were being lifted into metadata before indexing, so
the most natural way to search for a document was the one way guaranteed to
fail. One line to fix.

What matters is where it was caught. The access-control tests had been passing
the whole time, including the one asserting that students never see confidential
material. That test was passing because nothing could retrieve the document at
all. It looked identical to the test passing because the rule worked.

Two consequences you should carry into later phases. A security test that
asserts an absence needs a matching test asserting the presence, or it can pass
for the wrong reason indefinitely. And retrieval failures are silent by
construction: the system returns fewer results, not an error, so nothing
anywhere reports a problem. That is the argument for phase three before any
tuning, and phase five before any users.

## Recommended order from here

1. **Evaluation.** Sixty to a hundred questions with reference answers, and
   scorers for correctness, groundedness and cost. Everything after this is
   guesswork without it, including whether embeddings help.
2. **Delivery pipeline.** Tests, scans and a load budget that block a release
   rather than reporting on one.
3. **Reliability.** Service objectives, deliberate failure injection, and a
   runbook per alert.
4. **Observability.** End to end tracing of retrieval, tool calls and cost.
5. **Security.** Adversarial testing against the OWASP LLM list, then guardrails,
   then the same corpus re-run so the difference is a number.

Identity belongs in whichever of these you reach first once a real user is
involved, and before any student sees it.

## Handover checklist

- Source, tests and this documentation set are in the repository
- `README.md` covers running it; `client/03-deployment.md` covers installing it
- Decisions and their reversal conditions are in `client/02-decisions.md`
- Both test suites pass offline and are the acceptance evidence
- Known ceilings are marked in the code where they are, not only in this document
