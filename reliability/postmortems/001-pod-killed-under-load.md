# Postmortem 001: a pod killed under load

**Type:** deliberate experiment, run in CI against a real Kubernetes cluster.
**Status:** resolved. The experiment now runs on every push and fails the build
if a single request fails.

## Summary

Killing one of two pods while ten clients searched continuously looked
harmless, and was not. It passed at first only because it stopped watching too
early. Run properly, it found three separate problems, one layered under the
next, and each fix exposed the one below it:

1. The server ignored the stop signal entirely, so every pod took 31 seconds to
   die and was then force-killed mid-request. **2 of 147,821 requests failed.**
2. Fixing that made all traffic pile onto one pod. **The replacement served 0
   requests** for the rest of the run, and throughput fell by 59%. No request
   failed, so no error metric or availability SLO would ever have seen it.
3. Underneath both, the experiment's first version **passed for the wrong
   reason**, and it would have kept passing.

## Timeline

| Run | Load | Victim died in | Failed | Replacement served | Throughput |
|---|---|---|---|---|---|
| 1 | 40s | not observed | 0 of 51,035 | not observed | 1,275/s |
| 2 | 75s | **31s** | **2 of 147,821** | not observed | 1,970/s |
| 3 | 75s, graceful shutdown | 6s | 0 of 75,999 | not observed | **1,013/s** |
| 4 | 75s, same code, counted per pod | 6s | 0 of 60,134 | **0** (survivor: 48,838) | 802/s |
| 5 | 75s, connection recycling | 7s | **0 of 137,882** | **55,537** (survivor: 70,035) | **1,838/s** |

For comparison, the single-container load test in the same runs held steady at
about 2,275 requests a second, which rules out the runner as the cause of any
throughput change above.

## What happened, in order

### Run 1: a pass that meant nothing

Load ran for 40 seconds and the pod was deleted at 15. Zero failures. The
cluster state printed after the run listed the deleted pod as
`1/1 Terminating`, still alive after the load had finished.

The server runs as process 1 in its container, and Linux does not deliver
`SIGTERM` to process 1 unless it has installed a handler for it. Python had not.
So the pod ignored the request to stop, Kubernetes waited its 30-second grace
period, and the `SIGKILL` that would have cut connections landed at 45 seconds,
five seconds after the experiment ended.

**The experiment finished before the failure it was designed to observe.**

### Run 2: the real baseline

Load extended to 75 seconds, and the step now measures how long the victim takes
to die: **31 seconds**. Two requests failed, both at the moment of `SIGKILL`, on
connections that had been pinned to the dying pod for half a minute and were
never told to move.

Two out of 147,821 is small. It is also two real people, on an event that happens
on every deploy, every node drain and every autoscale-down. And 31 seconds per
pod means a rolling update of ten replicas spends five minutes waiting for
processes that are not listening.

### Run 3: fixed, and slower

The server now handles `SIGTERM` by draining:

1. `/healthz` returns 503, so anything routing on readiness stops sending.
2. Every response carries `Connection: close`, so clients holding a keep-alive
   connection reconnect, through routing that no longer includes this pod.
3. It keeps serving for five seconds while routing updates on every node.
4. It exits cleanly.

Victim now dies in 6 seconds, and nothing failed. But throughput dropped from
1,970 to 1,013 requests a second, while the single-container load test in the
same run was unchanged. So the drop was caused by the change.

### Run 4: the confirmation

The hypothesis: Kubernetes balances *connections*, not requests. A Service
chooses a pod when a connection opens and never again. During the drain, every
client reconnected at once, and at that moment the replacement pod was still
starting, so the survivor was the only ready endpoint. Everyone landed there, and
keep-alive held them there.

Every pod counts its own requests, so this was checkable directly:

```
kb-...-ldg22   the replacement   served 0
kb-...-znmnd   the survivor      served 48,838
```

The survivor sat at its 500m CPU limit for a minute while a healthy, ready pod
beside it did nothing.

### Run 5: resolved

The server now asks every connection to reconnect after 100 requests, as nginx
does after 1000. Under load that is about once a second per client, so a new pod
picks up traffic almost immediately. An idle connection is never disturbed,
because the limit counts requests, not time.

The replacement served 55,537 requests, throughput recovered to 1,838 a second,
nothing failed, and the single-container load test's p95 stayed at 8.27ms.

## What went well

- **Per-pod counters existed before they were needed.** They were added for
  Prometheus. Without them, run 4 is a guess about a throughput number.
- **The comparison run.** Having an unrelated load test in the same job, against
  a single container, is what separated "the runner was slow" from "the change
  was slow" in one line.

## What went wrong

- **The experiment was designed around the fix I expected, not the failure.** I
  predicted Python would exit immediately on `SIGTERM` and drop requests; the
  window was sized for that. It did not exit at all, and the window was too
  short to see what it did instead.
- **The first fix caused a regression that no alert here could see.** Every
  request succeeded. The availability SLO was untouched. The latency SLO, measured
  at the server, would have seen only the survivor's queueing as it approached its
  CPU limit, and only if the load had been higher.

## Lessons

**An experiment has to outlast the thing it is testing.** A grace period, a
timeout, a retry budget, a cache lifetime: if the observation window is shorter,
the experiment measures the part before anything happens.

**"Nothing failed" is not "nothing is wrong".** The worst state in this
postmortem, one pod doing all the work beside an idle one, returned a 100%
success rate. That is why the retrieval index has its own alert
(`KbIndexLooksBroken`) for the case where everything returns 200 and nothing is
found: a system can fail entirely in ways that never produce an error.

**A fix is a change, and changes get measured.** Graceful shutdown was correct
and made throughput worse. Had the experiment only checked for failed requests,
the regression would have shipped with a green build.

## Action items

| | Status |
|---|---|
| Handle `SIGTERM`; drain before exit | done |
| Recycle connections after 100 requests | done |
| Experiment runs 75s, past the grace period | done |
| Experiment fails the build on a single failed request | done |
| Per-pod request counts printed after every run | done |
| Alert on per-pod imbalance under load | done: `KbPodImbalance`, with run 4's numbers as its promtool test |

## Closed in Layer 5

The alert that run 4 needed now exists. Its test is this postmortem: one pod
serving 800 requests a second while its replacement serves none, asserted to
file a ticket, **and asserted not to fire `KbAvailabilityBurn` or `KbDown`**,
because the whole difficulty was that a 100% success rate is not a healthy
service.

Two guards were needed to make it usable. A traffic floor, because the ratio is
noise at low volume, and a pod count above one, because on Cloud Run the single
instance takes 100% of traffic correctly and would otherwise page every night.

The per-pod counters this depended on are also a panel now, in
`observability/dashboards/service.json`. Run 4 was answered by reading two
numbers out of a log; the same question is a graph.
