# KbPodImbalance: one pod is doing all the work

One pod is taking over 90% of requests while at least one other is ready and
idle. **Every request is succeeding.** That is not a reason to close this: it is
the reason the alert had to be written.

Postmortem 001, run 4: the survivor sat at its 500m CPU limit for a minute while
a healthy pod beside it served zero requests, throughput fell 59%, and nothing
failed. No error ratio, no burn rate and no availability SLO could see it,
because there was nothing wrong with any individual request. The only signals
were a throughput number nobody was watching and a per-pod counter that happened
to exist.

## What causes it

**Kubernetes balances connections, not requests.** A Service picks a pod when a
connection opens and never again. With keep-alive, a client that connected an
hour ago is still talking to whichever pod it picked then.

So the pattern is always the same: every client reconnected at the same moment,
and at that moment only one pod was ready.

- A rolling deploy, or any drain: `Connection: close` sends everyone back
  through routing at once, and the replacement pod may still be starting.
- A pod that crashed and was replaced, with traffic already established.
- A scale-up. New pods get nothing, because nobody is opening new connections.

## First five minutes

1. **Confirm it is connections and not scheduling.** `kubectl top pods -l app=kb`.
   The busy pod at its CPU limit with the idle one near zero is this. Both busy
   with one much busier is something else.

2. **Check the idle pod is actually ready.** An imbalance with the other pod
   `0/1` is not this alert's problem; it is a pod that will not start, and the
   traffic distribution is correct.

3. **The mitigation is to make clients reconnect.** The server already does this
   every 100 requests, which under load is about once a second per client. If
   the imbalance is persisting anyway, the traffic is slow enough that the
   counter takes a long time to reach 100 while still being above this alert's
   floor. Restarting the busy pod moves everyone, at the cost of doing it again
   the next time.

## Where to look first

`MAX_REQUESTS_PER_CONNECTION` in `serve.py` is the fix from postmortem 001 and
the first thing to check has not been removed or raised. Set to 100, after
nginx's 1000, because the pods here are far fewer and rebalancing matters more
than the handful of microseconds a reconnect costs.

## Cloud Run

Cannot fire, and that is not good news. Cloud Run runs one instance here
(`--max-instances 1`), so there is nothing to balance and the guard on pod count
keeps the alert quiet. The failure mode is specific to running more than one
replica behind a Service.
