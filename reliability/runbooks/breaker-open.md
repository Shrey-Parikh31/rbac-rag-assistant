# KbBreakerOpen: /ask is refusing calls to the model provider

**Nothing is broken here.** The breaker being open is the system doing what
postmortem 003 built it to do: the provider has failed three times in a row, so
calls stop going to it and every caller is told immediately, with `Retry-After`,
instead of waiting out a fifteen-second timeout to learn the same thing.

A ticket rather than a page, for one reason: **`/search` does not call the
provider at all.** Retrieval, clearance and refusal all work while this is
firing. Half the service being up is not worth waking anybody for.

## First five minutes

1. **Is it the provider or is it us?** Ask it directly, from anywhere:

   ```
   curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" \
     https://generativelanguage.googleapis.com/
   ```

   Silence or seconds of delay is the provider. An instant answer means the
   failures are on this side, and the next question is which.

2. **Quota is the likeliest cause, and it is not a page.** This project shares a
   small prepaid budget with another. An exhausted key returns 429 quickly, and
   the breaker deliberately does not count 429s: failing fast at something
   already fast saves nobody time. So a breaker stuck open is usually **not**
   quota; it is silence.

3. **Check the state is really stuck, not flapping.** `kb_breaker_state` on the
   service dashboard: a row alternating open, probing, closed is the cooldown
   working, and the alert wants five unbroken minutes. A flat red row is stuck.

4. **Confirm the blast radius is what it should be.** `/search` must still be
   answering.

   ```
   curl -H "Authorization: Bearer demo-student" \
     "$KB_URL/search?q=how+late+can+I+enroll"
   ```

   If `/search` is also failing, the breaker is a symptom and not the story:
   go to the availability runbook.

## What not to do

**Do not raise the timeout or the threshold to make this stop.** That was the
original behaviour, and it cost every user 63.4 seconds each, forever, with the
service learning nothing from the hundred failures before theirs. The alert is
telling you a dependency is gone. The correct response is to wait for it,
switch provider, or say so on the status page.

**Do not restart to clear it.** The breaker closes itself: after the cooldown it
lets exactly one request through, and a success closes it. A restart only
discards what the process learned.
