# KbAvailabilityBurn — search is failing too often

**SLO:** 99.5% of `/search` requests do not return a 5xx, over 30 days.

| Severity | Fires when | If it continues |
|---|---|---|
| page | 5xx above 7.2% for the last hour *and* the last 5 minutes, or above 3% for 6 hours *and* 30 minutes | the whole month's budget is gone in 2 to 5 days |
| ticket | above 0.5% for 3 days *and* the last 6 hours | the budget is spent faster than it refills |

A page means *now*. A ticket means *this week*. Silencing a ticket without fixing
it means the next incident starts with no budget left.

## First five minutes

1. **Is it still happening?** The page clears on its own within about 30 minutes
   of errors stopping, because its short windows go clean. If it has cleared, the
   incident is over and the job is the postmortem, not the fix.

2. **Did something just ship?** `kubectl rollout history deploy/kb`. A deploy in
   the last hour is the first suspect. Roll back, then investigate:
   `kubectl rollout undo deploy/kb`.

3. **What are the errors?** The server logs path and status, never the query
   string, and prints a traceback for every 500:

   ```bash
   kubectl logs -l app=kb --tail=200 | grep -B2 -A15 Traceback
   ```

4. **One pod or all of them?** Compare each pod's
   `kb_requests_total{route="/search",code="500"}`. One bad pod is a node or a
   corrupted container: delete it and let the Deployment replace it. All of them
   is the code or its inputs.

## Known causes

- **A question with no cached vector and no model credentials.** `/search`
  returns 500 because the query cannot be embedded. Invisible in CI, where every
  question is cached; real traffic asks new ones. The error rate is proportional
  to how novel the questions are, which is why it looks like a slow leak rather
  than an outage.
- **Embedding provider down or rate limited.** Same symptom, same place in the
  traceback, and the error text names the provider. The free tier allows 20
  requests per day *per model*, so a burst of new questions exhausts it and every
  later novel question fails until the quota resets at midnight Pacific.
