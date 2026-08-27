# Northgate University: internal knowledge assistant

**Requirements note**
Prepared for: Office of the Registrar and IT Services, Northgate University
Prepared by: Shrey Parikh
Status: agreed scope for phase one

*Northgate University is a fictional client used to give this system a real
brief. The constraints below are the ones a university would actually impose.*

## The problem, as stated

Staff answer the same policy questions repeatedly, by hand, from documents
scattered across a shared drive and three intranet pages. Students do not find
the answers themselves because the wording they use is not the wording the
policy uses. Nobody trusts a search box that has failed them before.

## The problem, restated

Two separable failures are hiding inside one complaint.

1. **Retrieval.** Someone asks "how late can I add a class" and the document
   says "late enrollment requires the instructor's written approval." Keyword
   search connects those poorly.
2. **Confidentiality.** Some of the same shared drive holds compensation bands
   and incident procedures. A search tool that ignores who is asking is worse
   than no search tool, because it fails silently and at scale.

Phase one addresses both. The second is the reason this cannot be a wiki with a
better search box.

## In scope

| | |
|---|---|
| Corpus | One document set, markdown, each document tagged with a sensitivity level |
| Roles | Three: student, staff, administrator |
| Interface | Question in, cited answer out. Command line for phase one |
| Actions | Look up policy, determine academic standing, file an IT ticket |
| Integration | Tools exposed over MCP so existing clients can reach them |

## Out of scope, deliberately

Single sign-on, a web front end, multi-tenancy, an admin console, document
upload, and any write access to systems of record. Each is a real requirement
eventually. None of them changes whether the core idea works, and phase one
exists to answer that question.

## What the client must accept

**The assistant will decline to answer.** When the documents do not contain an
answer it will say so rather than produce a plausible one. This will occasionally
look worse than a competitor demo. A confident wrong answer about academic
suspension is the failure mode that ends the project, so the system is tuned
against it.

**Role assignment is not solved here.** The system enforces clearance correctly
once it knows who is asking. Establishing that identity is the SSO work in phase
two. Until then the role is configured per deployment, not per user, and the
system must not be exposed to students in that state.

**Quality is not yet measured.** Phase one shows the shape. Phase two builds the
evaluation harness that turns "it seems better" into a number. No claim about
accuracy should be made to stakeholders before that exists.

## Acceptance for phase one

1. A student query never returns staff or confidential content, under any
   phrasing, demonstrated by an automated test rather than by inspection.
2. An unknown or misspelled role receives the least privilege, not the most.
3. Every answer cites the source document it came from.
4. A question the corpus does not cover produces a refusal, not a guess.
5. The same three tools are reachable from an MCP client with identical rules.
