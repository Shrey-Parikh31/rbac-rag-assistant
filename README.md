# rbac-rag-assistant

Related: [drivescore-cloud](https://github.com/Shrey-Parikh31/drivescore-cloud), a Go
service on Kubernetes with Terraform and an incident log.

An internal knowledge assistant for a university: staff and students ask
questions in plain language, and get answers grounded in policy documents they
are cleared to read. Retrieval-augmented, tool-calling, role-aware, and exposed
over MCP so any client can use the same tools under the same rules.

This is Layer 0 of a five-layer system. Layers above it add the delivery
pipeline, reliability engineering, evaluation, security testing and
observability, each on top of this application rather than beside it.

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

Both print `ok` and need no API key. Retrieval and the access rules are testable
offline, which is the point of keeping them out of the model.

Ask it something (needs `GEMINI_API_KEY`):

```powershell
.\.venv\Scripts\python.exe agent.py student "I have a 1.8 GPA, what happens?"
.\.venv\Scripts\python.exe agent.py staff "the wifi is down in the east wing, log it"
```

Retrieval on its own, no key required:

```powershell
.\.venv\Scripts\python.exe rag.py admin "adjunct pay per credit hour"
```

## How it fits together

| File | What it owns |
|---|---|
| `rag.py` | Parsing, chunking, the TF-IDF index, and the clearance filter |
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

**TF-IDF, not embeddings.** This is the baseline Layer 3 exists to beat. Swapping
in a semantic retriever before there is a golden set would be a guess; doing it
after produces a number that justifies the change. The swap point is `Index`.

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
| `KB_ROLE` | `student` | MCP server only |
| `KB_DOCS` | `docs` | Corpus directory |
| `KB_TICKETS` | `tickets.jsonl` | Ticket log |
