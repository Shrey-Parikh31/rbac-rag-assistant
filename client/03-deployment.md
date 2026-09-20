# Deployment guide

Northgate University internal knowledge assistant, phase one.
Audience: whoever at IT Services has to run this without calling me.

## What you are deploying

Four Python files, a folder of markdown, and a committed vector cache. No
database and no vector store to operate: the index is a few hundred vectors held
in memory, built at startup from `docs/` in well under a second. Restarting the
process is the whole of cache invalidation.

## Prerequisites

Python 3.13 and a Google AI Studio API key. Nothing else.

On the current build machine, `python` on the PATH resolves to an msys2 build
that has none of the dependencies. Call the venv interpreter by path and the
problem disappears. This is the single most common way a first install fails.

## Install

```powershell
cd "<install path>\rbac-rag-assistant"
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
| `KB_EMBED_MODEL` | `gemini-embedding-001` | Retrieval embeddings |
| `KB_VECTORS` | `vectors.json` | The committed vector cache |
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
    "rbac-rag-assistant": {
      "command": "<install path>\\rbac-rag-assistant\\.venv\\Scripts\\python.exe",
      "args": ["<install path>\\rbac-rag-assistant\\mcp_server.py"],
      "env": { "KB_ROLE": "staff" }
    }
  }
}
```

As a service, for anything that speaks HTTP:

```bash
docker run -d -p 8080:8080 \
  -e KB_TOKENS="$(cat /run/secrets/kb_tokens)" \
  -e GEMINI_API_KEY="$(cat /run/secrets/gemini_key)" \
  ghcr.io/shrey-parikh31/rbac-rag-assistant:latest
```

`KB_TOKENS` is a comma-separated list of `token:role` pairs, and the container
**will not start without it**. Each caller sends their own token:

```bash
curl -H "Authorization: Bearer <that person's token>" \
     "http://<host>:8080/search?q=how+late+can+I+enroll"
```

Three things to know before this goes in front of anyone:

1. **The token is the clearance.** Anyone holding a staff token is staff. Treat
   the list the way you treat passwords, and issue one token per person rather
   than one per role, so a single revocation does not lock out a department.
2. **Rotation depends on how the tokens are delivered.** From `KB_TOKENS` in the
   environment, the map is read once at startup, so rotating means restarting;
   rotating without restarting revokes nothing (postmortem 004). From a file
   (`KB_TOKENS_FILE`, as the Kubernetes manifest does), the server re-reads it
   within a second of the file changing. On Kubernetes the kubelet takes up to
   about a minute to update the file, so allow a minute, or rotate and then run
   `kubectl rollout restart deploy/kb` if it cannot wait. There is deliberately
   no reload endpoint: an endpoint that changes who can see what is an endpoint
   worth attacking.
3. **This is not an identity provider.** For real use, put your existing SSO in
   front and map a verified group claim to a role. What should not change is that
   the role is derived from something the caller cannot write for themselves.

`GET /health` is unauthenticated and reports only that the process is up with
its index built, so point your load balancer at it. `GET /search` needs no model
credentials at all; only `POST /ask` does, and without them it returns `503` with
an explanation rather than failing.

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
| A long silence, no output | Should no longer happen. The SDK retried a congested endpoint with backoff and no output, which reads as a hang. A 15 second request timeout now converts it into a message, and after three in a row the service stops calling the provider for 30 seconds and says so immediately |
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
