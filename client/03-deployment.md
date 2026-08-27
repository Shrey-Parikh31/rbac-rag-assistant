# Deployment guide

Northgate University internal knowledge assistant, phase one.
Audience: whoever at IT Services has to run this without calling me.

## What you are deploying

Four Python files and a folder of markdown. No database, no vector store, no
background service. The index is built in memory at startup from `docs/` and
takes well under a second on the current corpus. Restarting the process is the
whole of cache invalidation.

## Prerequisites

Python 3.13 and a Google AI Studio API key. Nothing else.

On the current build machine, `python` on the PATH resolves to an msys2 build
that has none of the dependencies. Call the venv interpreter by path and the
problem disappears. This is the single most common way a first install fails.

## Install

```powershell
cd "<install path>\kb-assistant"
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Verify before you trust it

```powershell
.\.venv\Scripts\python.exe test_rag.py
.\.venv\Scripts\python.exe test_tools.py
```

Both print `ok`. Neither needs an API key or a network. If either fails, stop:
the second one is the access-control suite, and a failure there means the
clearance rules are not doing what this document claims.

## Configure

| Variable | Default | Notes |
|---|---|---|
| `GEMINI_API_KEY` | none | Required for `agent.py`. Not required for tests or retrieval |
| `KB_MODEL` | `gemini-3.6-flash` | Pinned, not an alias. ADR-8 |
| `KB_ROLE` | `student` | MCP server only. See the warning below |
| `KB_DOCS` | `docs` | Corpus directory |
| `KB_TICKETS` | `tickets.jsonl` | Ticket log, append only |

**`KB_ROLE` is the setting to get right.** It applies to an entire MCP server
process. A server started with `KB_ROLE=admin` will answer compensation
questions for anyone who can reach it. Run one process per role, and treat
`KB_ROLE=admin` as an administrative tool with the access controls you would put
around any administrative tool.

## Run

Command line, one question per invocation:

```powershell
.\.venv\Scripts\python.exe agent.py staff "who do I report a security incident to"
```

As an MCP server, for use from an existing client:

```json
{
  "mcpServers": {
    "kb-assistant": {
      "command": "<install path>\\kb-assistant\\.venv\\Scripts\\python.exe",
      "args": ["<install path>\\kb-assistant\\mcp_server.py"],
      "env": { "KB_ROLE": "staff" }
    }
  }
}
```

## Updating the corpus

Add or edit a markdown file in `docs/`. Front matter sets the sensitivity level:

```markdown
---
role: staff
title: IT Incident Response
---
```

`role` must be `public`, `staff`, or `confidential`. **A file with no front
matter defaults to `public`**, which is the safe default for availability and
the unsafe one for confidentiality. Check new files. Restart to pick up changes.

Write the title carefully. It is indexed along with the text and is often how
people search.

## Operating notes

**Cost.** One question is one API call plus one per tool call, typically two to
four. There is no per-query cost tracking in phase one; that is phase five.

**Failure modes, all observed in testing rather than imagined.**

| What you see | What it is |
|---|---|
| `Rate limit reached on the free tier` | The free tier allows about **5 requests per minute** on this model. Normal, not a fault. Wait and retry |
| `The model provider is busy or timed out` | A 503 or 504. The newest model is the most congested; `gemini-3.7-flash` returned 503 while `gemini-3.6-flash` served normally |
| A long silence, no output | Should no longer happen. The SDK retried a congested endpoint with backoff and no output, which reads as a hang. A 60 second request timeout now converts it into a message |
| `No GEMINI_API_KEY set` | `agent.py` only. `rag.py` still works, so retrieval can be checked without the model |

**The rate limit is a planning constraint, not just an annoyance.** At five
requests per minute, a hundred-question evaluation run takes roughly twenty
minutes. Phase three should batch and pace accordingly, or budget for a paid
tier.

**No logs.** Phase one writes nothing except tickets. Tool calls print to
stderr for debugging. Real observability is phase five, and until it exists you
cannot answer "why was that answer wrong" from artifacts alone.

## Support boundary

This is phase one of five. It is a demonstration that the approach works, not a
system with an availability target. Do not put it in front of students until
identity is real, which is phase two, and quality is measured, which is phase
three.
