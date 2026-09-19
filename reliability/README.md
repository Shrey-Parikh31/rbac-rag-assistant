# Reliability

What "working" means in numbers, the alerts that watch those numbers, and a
record of breaking the system on purpose to find out whether they are right.

## Objectives

| SLI | Objective | Budget over 30 days |
|---|---|---|
| `/search` requests that do not return 5xx | **99.5%** | 0.5%, about 3.6 hours of total outage |
| `/search` requests answered within 50ms | **99%** | 1% |

**Why 99.5 and not 99.9.** One region, one small container, a free tier, and a
dependency (the embedding API) with its own daily quota. Promising three nines on
that would be a number chosen to sound good, and an objective nobody can meet is
one nobody looks at. 99.5% is honest about the architecture and still tight
enough to page on a real incident.

**Why `/search` and not `/ask`.** `/ask` spends almost all of its time inside a
model provider, so its latency and availability are mostly someone else's. An
objective on it would measure the vendor. `/search` is the part this repository
controls end to end.

### What the SLIs cannot see

Being clear about the blind spots is most of the work:

- **A dead process.** It records no requests and so no errors. The error ratio
  is undefined rather than high, and a burn-rate alert on it never fires.
  `KbDown` exists for exactly this, and `promtool` tests it.
- **An index that finds nothing.** Every request returns 200. Availability is
  perfect. `KbIndexLooksBroken` watches the outcome mix instead.
- **Time before the server starts its clock.** A Cloud Run cold start, TLS, a
  queue in front of the process. A user who waited three seconds for a container
  to boot is recorded as a fast request. Latency measured at the server is a
  lower bound on what users experience, not a measurement of it.
- **Uneven load.** In experiment 1 one pod served 48,838 requests while its
  replacement served none, and every request succeeded. See postmortem 001.
- **A connection refused at the door.** With the default listen backlog of 5, a
  burst of 200 connections lost 40 before the process saw them: not counted, not
  timed, not logged. Measuring at the server means measuring only the requests
  that got in. See postmortem 003.

## Alerts

Multi-window, multi-burn-rate, from chapter 5 of the Google SRE Workbook. Full
reasoning is in [`alerts.yaml`](alerts.yaml); every claim made there is tested in
[`alerts.test.yaml`](alerts.test.yaml), which CI runs with `promtool` on every
push.

| Alert | Severity | Runbook |
|---|---|---|
| `KbDown` | page | [kb-down.md](runbooks/kb-down.md) |
| `KbAvailabilityBurn` | page / ticket | [availability-burn.md](runbooks/availability-burn.md) |
| `KbLatencyBurn` | page / ticket | [latency-burn.md](runbooks/latency-burn.md) |
| `KbIndexLooksBroken` | ticket | [index-broken.md](runbooks/index-broken.md) |

**What the tests prove**, each as a case that fails if it stops being true:

- healthy traffic (0.1% errors) pages nobody
- 10% errors for 30 minutes pages, then the page **clears** once the short
  windows go clean, while a ticket remains because the budget spent stays spent
- a one-minute burst that takes the 5-minute error ratio to 9% does **not** page;
  a static "page above 5%" rule would have
- a dead service pages even though it never recorded a single error
- an index returning "no match" to 90% of searches is noticed, but only with
  enough traffic that it is not three unlucky questions at 4am

**The tests have been seen to fail.** Making the fast-burn threshold ten times
too sensitive on a throwaway branch failed the blip test, and the image was not
built. A test that has never failed has not yet shown it can.

## Experiments

Each runs against a real cluster, and gets a postmortem whether or not it found
anything.

| # | Experiment | Found | Postmortem |
|---|---|---|---|
| 1 | Kill a pod under load | ignored `SIGTERM` (31s to die, 2 failed requests); then one pod doing all the work beside an idle replacement | [001](postmortems/001-pod-killed-under-load.md) |
| 2 | Break the vector store four ways | two quiet failures served as healthy; one gave the **wrong document for half of all questions** with no alert able to fire | [002](postmortems/002-broken-vector-store.md) |
| 3 | The model provider goes silent | every `/ask` user waited **63.4s** for an error, and so did everyone after them; now 17s for the first 8, instant for the rest | [003](postmortems/003-silent-model-provider.md) |
