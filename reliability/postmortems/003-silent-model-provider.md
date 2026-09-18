# Postmortem 003: the model provider goes silent

**Type:** deliberate experiment, runnable anywhere with
`python reliability/experiments/slow_provider.py`. Runs in CI on every push.
**Status:** resolved.

## Summary

The model provider was replaced with a tarpit: a socket that accepts every
connection and never sends a byte. It covers two items from the roadmap,
"inject latency" and "time out the model provider", because from this service's
side they are the same event.

`/search` was never affected, and that was already true before any change. The
problem was `/ask`. **Every user waited 63.4 seconds to be told it had failed,
and so did every user after them.** The server learned nothing from watching the
provider fail.

## Results

**Before:** 10 simultaneous questions.

| | |
|---|---|
| each user waited | **63.4s**, then HTTP 502 |
| connections the dead provider received | 10 of 10 |
| the next user | would wait another 63s |
| `/search` during | p50 3.6 → 4.0ms, 0 errors |

**After:** 30 simultaneous questions, then 10 more.

| | |
|---|---|
| the 8 that reached the provider | waited **17.2s**, then HTTP 502 |
| the other 22 | told at once (median **0.3s**) that 8 questions are already waiting, HTTP 503 |
| connections the dead provider received | **8 of 30** |
| the next 10 users | told in **0.00s** that the provider is not responding, HTTP 503 with `Retry-After`; **0** connections to the provider |
| `/search` during | 0 errors |

`/search` p50 moved from 3.6 to 5.9ms in the second run. The load generator ran on
the same laptop as the server with more than thirty threads of its own, so that
number is not attributed to anything.

## Three fixes, each for a different part of the problem

**The timeout: how long one user waits.** 60 seconds became 15. A complete
successful turn has a p95 of 7.7 seconds across two or three requests, so a
single request still silent at 15 is not slow, it is gone. The measured wait is
17.2 seconds: the 15-second timeout plus client setup.

**The circuit breaker: how many users have to find out.** After three
consecutive slow failures, the server stops calling the provider for 30 seconds
and answers immediately with `Retry-After`. Once the cooldown ends, exactly one
request is let through as a probe: success closes the breaker, failure opens it
again. Rate limits do not count: a 429 comes back in milliseconds, so failing it
faster saves nobody any time.

**The bulkhead: what a burst can hold.** The breaker counts failures as they
finish, so a burst that arrives all at once gets past it before the first timeout
has returned. Each of those requests holds a thread and a socket for the full
timeout. At most eight now wait on the provider together, and the ninth is told
at once rather than queued. `/search` takes no slot and is never refused
because `/ask` is busy.

## What was already right

`/search` was isolated from the start, for a structural reason: a thread blocked
on a socket releases the interpreter lock, so threads waiting on the provider
cost memory, not CPU. The retrieval half of the system does not depend on the
model at all, a decision made in Layer 0 so it could be tested offline. It turns
out to also be what keeps half the service up during a provider outage.

## Lessons

**A timeout protects one request. It does nothing for the next.** With only a
timeout, a provider outage costs every user the full timeout, forever. The number
that mattered was not "how long does a failed request take" but "how many
requests are made to something already known to be down".

**Fast failure is a feature users can feel.** A 503 in zero seconds that says
what is wrong and that `/search` still works is a better outcome than a 502 after
seventeen. The first lets someone do something else. The second wastes their time
and then tells them the same thing.

**The verdict has been seen to fail.** With the breaker disabled, the experiment
reported three failures: requests past the bulkhead, the second wave waiting
16.3 seconds, and the second wave reaching the provider. It was not accepted
until it had caught something.

## Action items

| | Status |
|---|---|
| Request timeout 60s → 15s | done |
| Circuit breaker: 3 consecutive slow failures, 30s cooldown, single probe | done, with a fake-clock unit test of every state |
| Bulkhead: 8 concurrent `/ask` | done |
| Experiment runs in CI and fails on regression | done |
| Breaker state as a metric and an alert, so an open breaker is visible without reading logs | **open** — belongs with Layer 5 |
