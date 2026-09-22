# rbac-rag-assistant

Other projects: [highschool-rag-chatbot](https://github.com/Shrey-Parikh31/highschool-rag-chatbot) ·
[drivescore-cloud](https://github.com/Shrey-Parikh31/drivescore-cloud)

An internal knowledge assistant for a university: staff and students ask
questions in plain language, and get answers grounded in policy documents they
are cleared to read. Retrieval-augmented, tool-calling, role-aware, and exposed
over MCP so any client can use the same tools under the same rules.

This is Layer 0 of a five-layer system. Layers above it add the delivery
pipeline, reliability engineering, evaluation, security testing and
observability, each on top of this application rather than beside it.

## Try it

<https://kb-zv5i45k6sq-uc.a.run.app>

The student token is public on purpose, so anyone can see the access rules work.
A student may read public material and is told when something exists that they
are not cleared for; staff and administrator tokens are not published.

```bash
# a public policy: answered
curl -H "Authorization: Bearer demo-student"   "https://kb-zv5i45k6sq-uc.a.run.app/search?q=how+late+can+I+enroll"

# a confidential one: the existence is disclosed, the content is not
curl -H "Authorization: Bearer demo-student"   "https://kb-zv5i45k6sq-uc.a.run.app/search?q=faculty+compensation+bands"

# no token at all
curl -i "https://kb-zv5i45k6sq-uc.a.run.app/search?q=anything"
```

It sleeps when nobody is using it, so the first request after a quiet spell
takes a second to wake it. Generated answers (`POST /ask`) are switched off
here: the key it holds is for looking up questions, and the free tier allows 20
generations a day, which one visitor could spend. `deploy/GOOGLE_CLOUD_SETUP.md`
is the whole setup, and the bill for this is $0.00.

## Run it

Python 3.13. Note that `python` on this machine resolves to the msys2 build,
which has none of these packages, so call the venv interpreter directly.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```powershell
.\.venv\Scripts\python.exe test_rag.py
.\.venv\Scripts\python.exe test_tools.py
```

Both print `ok` and need no API key: `vectors.json` is committed and covers the
corpus and every question in the golden set. Retrieval and the access rules stay
testable offline, which is the point of keeping them out of the model.

Ask it something (needs `GEMINI_API_KEY`):

```powershell
.\.venv\Scripts\python.exe agent.py student "I have a 1.8 GPA, what happens?"
.\.venv\Scripts\python.exe agent.py staff "the wifi is down in the east wing, log it"
```

Retrieval on its own, no key required:

```powershell
.\.venv\Scripts\python.exe rag.py admin "adjunct pay per credit hour"
```

Over HTTP. `KB_TOKENS` is mandatory, because a server that cannot identify a
caller would have to refuse every request; ADR-13 covers why the role is never
read from a header the caller writes:

```powershell
$env:KB_TOKENS="devstudent:student,devstaff:staff,devadmin:admin"
.\.venv\Scripts\python.exe serve.py
```

```bash
curl -H "Authorization: Bearer devstudent" "localhost:8080/search?q=how+late+can+I+enroll"
```

| Route | Auth | Needs a model |
|---|---|---|
| `GET /health` | no | no |
| `GET /search?q=` | bearer token | no |
| `POST /ask` | bearer token | yes, else `503` |

`/healthz` answers identically, for Docker and Kubernetes where that name is the
convention. It is unusable on Cloud Run: Google's frontend answers the literal
path `/healthz` with its own 404 before the request reaches the container, which
is how the first deploy came up serving `/search` correctly while its health
check 404ed.

## The pipeline

`.github/workflows/ci.yml` runs on every push and can refuse to publish. Three
things make it a gate rather than a report:

| Gate | Fails the build when |
|---|---|
| `eval/retrieval.py --gate` | a golden case that used to pass stops passing, or anything leaks |
| `perf/latency.js` (k6) | p95 on `/search` exceeds 30ms, or the error rate exceeds 1% |
| Trivy | a critical CVE with an available fix is in the image |

**Both scanners caught something real on their first run.** Trivy refused to
publish over three criticals in `perl-base`: a heap overflow compiling regular
expressions, a path traversal in `Archive::Tar`, all with a patched version
already released, inherited from a `python:3.12-slim` base rebuilt on somebody
else's schedule. semgrep found SHA1 in the embedding cache key, which was
changed to SHA256 rather than suppressed (ADR-17).

**And the load test found a bug nothing else could see.** The first k6 run
reported p95 40.94ms, p90 40.93ms, median 40.91ms, min 1.75ms. A distribution
with no spread is not a workload, it is a constant, and ~40ms is the Linux
delayed-ACK timer: `http.server` flushes headers in one write and the body in
another, so Nagle held the second segment waiting for an acknowledgement the
client would not send for 40ms because it was waiting for more data. Retrieval
itself takes under 2ms, so nearly all of the measured latency was two TCP timers
arguing with each other.

| | before | after `TCP_NODELAY` |
|---|---|---|
| p95 | 40.94 ms | **9.01 ms** |
| median | 40.91 ms | **3.71 ms** |
| throughput | 242 req/s | **2,287 req/s** |

The budget is set at 30ms rather than a round 250ms for that reason: it sits
*below* the delayed-ACK constant, so reintroducing that bug fails the build
instead of being absorbed by generous headroom.

## Where it runs

A managed Kubernetes control plane costs about $73/month whether anyone visits
or not. Both halves of what a cluster would prove are available for nothing, so
the pipeline does both (ADR-19).

**A real cluster, for two minutes.** `kind` builds one inside the CI runner,
deploys `deploy/k8s.yaml` to it, and asserts three things by watching them
happen:

```
deleting pod/kb-5cd8dc8b86-xzjpx  →  back to 2/2 ready, service healthy
error: deployment "kb" exceeded its progress deadline
rollout refused, as it should be  →  service stayed up throughout
deployment.apps/kb rolled back    →  serve: ok
```

The rollback step inverts the exit code of `kubectl rollout status` on purpose:
a rollback test that never observes a *failed* rollout proves nothing. It also
gives Layer 2 somewhere to run chaos experiments: "kill pods mid-request" is
not a sentence that means anything on a serverless host.

**A URL, on Cloud Run.** The container runs only while somebody is asking it
something and sleeps at zero otherwise, inside a permanent free allowance of two
million requests a month. A new revision deploys carrying **no traffic**, the
end-to-end suite runs against it on its own tagged URL, and only then does
traffic move, so a broken revision is never in front of a user and there is
nothing to roll back from. Setup is `deploy/GOOGLE_CLOUD_SETUP.md`; the job is
skipped entirely until `GCP_PROJECT_ID` exists, so this repository stays green
for anyone who clones it without a cloud account.

Authentication is Workload Identity Federation: GitHub signs a statement naming
the repository and the run, Google is configured to accept exactly that, and no
long-lived key is stored anywhere.

The quality gate compares **cases, not percentages**, because a percentage
cannot see a swap: one case fixed, one broken, score unchanged (ADR-14). It was
verified by breaking it on purpose: raising the retrieval floor from 0.61 to
0.68 made it exit 1, name fifteen regressed cases, and report four improvements
that a percentage would have netted off.

Everything on that path runs without an API key, which is what makes it
affordable per push: the free tier allows 20 model requests per day **per
model**, so a live evaluation in CI would be exhausted before lunch. The live
run is `.github/workflows/eval.yml`, started by hand.

```powershell
.\.venv\Scripts\python.exe eval\retrieval.py --gate     # what CI runs
.\.venv\Scripts\python.exe eval\retrieval.py --accept   # deliberately move the bar
.\.venv\Scripts\python.exe test_serve.py                # end to end over HTTP
```

## How it fits together

| File | What it owns |
|---|---|
| `rag.py` | Parsing, chunking, the embedding index, and the clearance filter |
| `tools.py` | The three tools, their access rules, and argument validation |
| `agent.py` | The tool-calling loop, and the only file that knows which model vendor is used |
| `mcp_server.py` | The same tools over MCP. No implementation of its own |
| `docs/` | The corpus. Front matter carries each document's `role` |

## Three decisions worth defending

**The caller's role is not a tool parameter.** It is bound out of band before
the turn starts. Had `search_docs(query, role)` existed, the model would hold
its own clearance, and a sentence buried in a retrieved document telling it to
"use role=admin" would be an escalation path. There is nothing to pass, so
there is nothing to talk it into.

**The clearance filter runs on the candidate set, not the results.** A chunk the
caller may not see never enters the ranking, so its existence cannot be inferred
from a gap in the results or from a score that moved.

**Retrieval by meaning, and the number that justified it.** This started as word
matching on purpose, as the baseline the evaluation layer existed to beat.
Swapping before the golden set existed would have been a guess. Swapping after
produced a table: 83.3% to 91.7% overall, paraphrase matching 50% to 75%, and no
category got worse. ADR-3.

## MCP

`mcp_server.py` registers the functions from `tools.py` and adds nothing else,
which is the argument for MCP in one file: one implementation, one set of access
rules, many clients. Add to a client's config:

```json
{
  "mcpServers": {
    "rbac-rag-assistant": {
      "command": "C:\\Users\\Shrey\\Documents\\CLAUDE CODE\\rbac-rag-assistant\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\Shrey\\Documents\\CLAUDE CODE\\rbac-rag-assistant\\mcp_server.py"],
      "env": { "KB_ROLE": "staff" }
    }
  }
}
```

An MCP client has no notion of a university role, so `KB_ROLE` is read once at
startup and applies to the whole connection. The role belongs to the server
process rather than to the conversation, which is what keeps the model from
choosing it.

## Behaviour when access is denied

A student asking about staff-only material is told the material exists and that
they are not cleared for it, rather than being told nothing exists (ADR-7).
Verified live:

> Guidance on what to do if you suspect your account has been compromised exists
> in the university policy documentation, but accessing it requires staff
> clearance. Please contact the office that owns this policy.

What crosses the boundary is a classification label and nothing else. No
passage, no title, no filename. A question no document covers gets a different
answer, which is the whole point of the distinction.

## What surprised me

**One.** `faculty compensation bands` returned nothing for an administrator,
while `professor salary` returned the same document at 0.29. Those three words
appear only in the document's title, and `parse()` lifts the title out of the
body into metadata before anything is indexed. Users search with a document's
title far more often than its wording, so the most natural query for the most
sensitive document was the one guaranteed to fail. One line to fix: index
`title + text`, store `text`.

Worth recording because of *where* it was found. The access-control tests passed
throughout, including the one asserting a student never sees confidential
content, which was vacuously true when nobody could retrieve the document at
all. A test that passes because nothing was returned looks identical to a test
that passes because the rule works.

**Two, found while implementing ADR-7.** The "restricted material exists" notice
fires on innocent questions. "Who is a full professor here" scores **0.28**
against the compensation bands, which is *higher* than a legitimate staff match
at 0.19. No threshold separates them, so this is not tunable; it is lexical
matching being unable to tell a shared word from a shared meaning. Nothing
leaks, and the answer is still wrong. First thing for Layer 3 to measure.

**Three.** The model fills small gaps the documents do not cover. Asked about a
compromised account it suggested contacting "the IT Service Desk or IT Security
team" when the tool had only said "the office that owns it". Plausible,
harmless here, and not grounded in anything retrieved. That is exactly the
behaviour a groundedness scorer exists to catch, and it appeared within the
first five live questions.

## Environment

| Variable | Default | |
|---|---|---|
| `GEMINI_API_KEY` | | Required by `agent.py` only |
| `KB_MODEL` | `gemini-3.6-flash` | Pinned, not an alias. ADR-8 |
| `KB_EMBED_MODEL` | `gemini-embedding-001` | Retrieval. Vectors cached in `vectors.json` |
| `KB_ROLE` | `student` | MCP server only |
| `KB_DOCS` | `docs` | Corpus directory |
| `KB_TICKETS` | `tickets.jsonl` | Ticket log |
| `KB_TOKENS` | | **Required by `serve.py`.** `token:role` pairs, comma separated |
| `PORT` | `8080` | `serve.py` |
| `KB_PACE` | `25` | Seconds between live evaluation questions, to stay under the rate limit |
