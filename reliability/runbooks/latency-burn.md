# KbLatencyBurn — search is too slow

**SLO:** 99% of `/search` requests complete within 50ms, measured at the server,
over 30 days. Same burn-rate structure as availability: 14.4x or 6x pages, 1x
files a ticket.

**What this cannot see:** anything before the server starts its clock. A Cloud
Run cold start, TLS setup, a slow client, a queue in front of the process. A
request that waited three seconds for a container to boot is recorded here as
fast. If users report slowness and this alert is quiet, measure from the client
before concluding nothing is wrong.

## First five minutes

1. **Every request, or some?** Compare `histogram_quantile(0.5, ...)` with
   `histogram_quantile(0.99, ...)` over `kb_request_duration_seconds_bucket`. A
   higher median means everything slowed. A normal median with a bad tail means
   some requests are waiting on something.

2. **Is it the embedding call?** A question not in the vector cache calls an
   external API synchronously, on the request path: hundreds of milliseconds even
   when the provider is healthy. Traffic shifting towards new questions raises
   the tail with nothing broken.

3. **Is it CPU?** `kubectl top pods -l app=kb`. The limit is 500m and the server
   is one process with a thread per request, so under load threads queue for the
   interpreter lock. Measured on a laptop: p95 about 24ms at 10 concurrent
   callers, about 536ms at 30. More replicas help; a bigger CPU limit helps less
   than it looks.

4. **Did 40ms come back?** A median sitting almost exactly on 40ms with almost no
   spread is the delayed-ACK interaction that `TCP_NODELAY` fixed (ADR-18).
   Check `disable_nagle_algorithm` survived the last change to `serve.py`. The
   30ms k6 budget exists to stop exactly this in CI, so if it reached production,
   find out how the gate was bypassed.
