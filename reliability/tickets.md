# Support tickets

Incidents as they would arrive at a support desk: a person's description of what
went wrong, in their words, with none of the information the system has about
itself. Each is worked from that symptom to a cause using only what a support
engineer could reach: the service's own endpoints, its logs, and `kubectl`.

**Honest framing.** Every fault here was one this project injected on purpose in
a chaos experiment, so the cause was known before the ticket was written. What
these demonstrate is not detection but **the evidence trail**: which observation
rules out which explanation, and which single piece of data turns a guess into a
diagnosis. The evidence quoted is real output from the experiments, not
illustration.

---

## INC-1042 — "search is slow since this morning's deploy"

> *Since the update this morning search feels sluggish. Not broken, everything
> loads, just slower. A couple of us noticed around the same time.*

**First questions.** Everyone, or some people? Since a deploy, or since a time of
day? "Not broken, just slower" rules out the error path and points at capacity or
latency.

**Evidence, in the order it was gathered**

1. Error rate. `kb_requests_total` for `/search` by code: all 200s. The
   availability SLO is untouched. **This rules out anything failing.**
2. Latency at the server, from `kb_request_duration_seconds`: the median has
   risen, but the tail has risen further. The work per request has not changed,
   so requests are waiting on something.
3. Pods: `kubectl get pods -l app=kb`. Both Running, both ready, one of them much
   younger. The deploy replaced one.
4. **The observation that turned it:** requests served *per pod*, from each pod's
   own `/metrics`:

   ```
   pod/kb-...-ldg22  (the new one)   served 0
   pod/kb-...-znmnd  (the old one)   served 48,838
   ```

   One pod doing all the work, pinned at its CPU limit, beside a healthy idle one.

**Cause.** Kubernetes balances connections, not requests. When the old pod
drained during the deploy, every client reconnected at once, when only the
survivor was ready, and keep-alive held them there.

**Why it was hard to see.** Every aggregate looked healthy. Error rate, readiness,
pod count, and the Service's total throughput were all normal. Only a per-pod
breakdown showed it.

**Resolution.** Connections are recycled every 100 requests.
[Postmortem 001](postmortems/001-pod-killed-under-load.md).

---

## INC-1043 — "I rotated my token and now nothing works"

> *My token was visible in a screenshot I shared, so I asked for it to be
> rotated. IT says it's done. My new token gets "bearer token required" and I've
> been locked out for an hour. Weirdly my old one still works?*

**First questions.** The last sentence is the whole ticket. A revoked token that
still works is a **security incident**, not a support one, and it takes priority
over the lockout.

**Evidence**

1. Probe both tokens through the Service, several times, from inside the cluster:

   ```
   old: 200 200 200 200 | new: 401 401 401 401
   ```

   Every pod agrees: old accepted, new refused. That rules out one bad pod, and
   rules out a partial rotation.
2. `kubectl get secret kb-tokens -o yaml`: the Secret does contain the new token.
   So the rotation was stored correctly.
3. `kubectl get pod <pod> -o yaml`: the pods take the tokens through `envFrom`,
   and their start time is before the rotation.

**Cause.** A pod's environment is fixed at start. The Secret changed and no
running process could see it.

**Immediate action.** `kubectl rollout restart deploy/kb`, then probe both tokens
again to confirm the old one is refused everywhere. Tell the reporter the leaked
token was valid until that moment, so they can judge what else to rotate.

**Resolution.** The tokens are now mounted as a file and reloaded within a
second; a rotation takes effect everywhere in about a minute with no restart.
[Postmortem 004](postmortems/004-revoking-a-leaked-token.md).

---

## INC-1044 — "it keeps giving me the wrong policy"

> *I asked how late I can add a class and it gave me something about grade
> appeals. Asked again differently, same thing. Is this thing broken? It says
> "enrollment.md" on some answers and "grading.md" on others that make no sense.*

**First questions.** Wrong content with a **confident citation** is worse than an
error, because people believe it. Is it one question or many? A citation that
doesn't match its own passage is the tell.

**Evidence**

1. Error rate, latency and `KbIndexLooksBroken`: all normal. The outcome metric
   counts these as `answer`, so the index looks healthy to monitoring.
2. `/healthz`: `{"ok": true, "chunks": 4}`. The index is built and the process is up.
3. Copy `vectors.json` out of a running pod (`kubectl cp`) and run the offline
   gate against it: `KB_VECTORS=./from-pod.json python eval/retrieval.py --gate`.
   It names every golden case that now comes
   back from the wrong document. **This is the step that turned it**: known
   questions with known answers, asked of the index directly.
4. With the golden set's answers in hand, the pattern was systematic: each
   document was being returned for its neighbour's questions.

**Cause.** Document vectors shuffled one position: every document held another's
embedding. Real vectors, real magnitudes, high similarity scores, wrong
document.

**Why it was hard to see.** Nothing failed. Before the fix, this ran for as long
as it took somebody to notice the answers were wrong, and write in.

**Resolution.** The server now asks one canary question per document at startup
and refuses to serve if any returns the wrong document, so this configuration
never receives traffic. [Postmortem 002](postmortems/002-broken-vector-store.md).

---

## INC-1045 — "Ask spins for a minute then errors, every single time"

> *The Ask feature hangs for about a minute and then says it couldn't reach the
> model provider. I tried five times. Search still works fine. Is it just me?*

**First questions.** "Search still works" is useful: the failure is on the path
that calls out to the model provider, not the service itself.

**Evidence**

1. `/ask` responses: HTTP 502, status `transport_error`, each after about 63
   seconds. Never faster, never a success.
2. `/search` latency during the same window: p50 3.9ms before and during. The
   service is healthy; the dependency is not.
3. **What turned it:** the provider connection *opens* but nothing comes back.
   A refused connection fails in milliseconds; a 63-second wait is a timeout on a
   connection that was accepted and then went silent.
4. Five tries, five minutes of waiting. Every user after the first was paying the
   full timeout to rediscover what the server had already seen fail.

**Immediate action.** Nothing on this side fixes a provider outage. Tell users
`/search` works, and check the provider's status page.

**Resolution.** Timeout 60s to 15s, a circuit breaker that fails fast after three
consecutive failures, and a bulkhead of eight. The fifth attempt in this ticket
would now be answered in zero seconds with an explanation.
[Postmortem 003](postmortems/003-silent-model-provider.md).

---

## INC-1046 — "connection reset during registration"

> *When registration opened at 9 a.m. a bunch of us got "connection reset" or
> "couldn't connect" trying to look up the add/drop deadline. Refreshing usually
> worked. Your status page said everything was fine.*

**First questions.** "Your status page said everything was fine" deserves to be
taken literally: it was probably right, by its own measurements. A burst at a known
time, failures that clear on retry, and **no server-side error** is the signature
of something failing before the request reaches the application.

**Evidence**

1. Server-side 5xx at 9 a.m.: none. Latency: normal. By every SLI this was a
   perfect hour. **Believe that.** Then ask what the SLIs cannot see.
2. The complaint is about *connecting*, not about a response. A request refused
   at the door is never counted, timed or logged by the process.
3. Reproduce the shape: 200 connections at once to `/healthz`:

   ```
   200 simultaneous connections -> failures: 40
   ```

4. `socketserver.TCPServer.request_queue_size` is **5**.

**Cause.** The listen backlog. At most five connections can wait for the process
to accept them; a burst beyond that is refused by the kernel.

**Why it was hard to see.** The failure happened before the first line of this
service's code ran. Measuring at the server means measuring only the requests
that got in.

**Resolution.** Backlog raised to `SOMAXCONN`; a 200-connection burst test in CI.
A client-side availability probe is an open action item for Layer 5, because the
server will never be able to count what it never received.
[Postmortem 003](postmortems/003-silent-model-provider.md#found-on-the-way-one-caller-in-five-turned-away-at-the-door).

---

## What the five have in common

In four of the five, **every standard health signal was green**: error rate,
latency, readiness, availability. The fifth was green on everything except the
feature the user was complaining about.

The diagnosis came each time from a measurement nobody would build a dashboard
for until they had needed it once: requests per pod, both tokens probed side by
side, known answers asked of the index directly, connection time versus response
time, and a burst reproduced from the client's side. That is the case for Layer 5
building those in advance, and the reason support starts from the user's words
rather than the dashboard: the dashboard only shows what someone already thought
to measure.
