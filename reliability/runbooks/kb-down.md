# KbDown — nobody is answering

**Severity:** page. **Means:** Prometheus has failed to scrape any instance for
two minutes. Every other alert is blind to this, because a process that is not
running records no errors.

## First five minutes

1. **Is it down, or is monitoring down?** From anywhere outside the cluster:

   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" https://<service-url>/healthz
   ```

   `200` means users are fine and the scrape path is broken: lower the urgency
   and go to *Scrape path*. Anything else, carry on.

2. **Are pods running?**

   ```bash
   kubectl get pods -l app=kb
   kubectl describe deploy kb | tail -20
   ```

   | You see | It usually means |
   |---|---|
   | `CrashLoopBackOff` | the process exits at startup (step 3) |
   | `ErrImageNeverPull` / `ImagePullBackOff` | a deploy points at an image that does not exist (step 4) |
   | `Running` but `0/1` ready | readiness is failing; the index did not build (step 3) |
   | no pods at all | the Deployment was scaled to zero or deleted |

3. **Why will it not start?** `kubectl logs -l app=kb --previous --tail=50`.
   Two known causes, both deliberate fail-fast choices:
   - `KB_TOKENS is not set`: the Secret is missing or empty.
     `kubectl get secret kb-tokens`. The server refuses to start rather than
     serve without being able to identify callers.
   - `text(s) are not in vectors.json`: the corpus changed without its vectors.
     The image was built from a commit whose `vectors.json` does not cover
     `docs/`. Roll back (step 4); the fix is a rebuild after embedding.

4. **A bad deploy.** `kubectl rollout undo deploy/kb`, then
   `kubectl rollout status deploy/kb`. Roll back first and diagnose second: the
   old revision is known good, and the diagnosis can wait while users are served.

## Scrape path

`/metrics` is served by the application itself, so "scrape failing, service up"
is a change between Prometheus and the pods: a NetworkPolicy, a changed port, a
relabelled job. Check `up{job="kb"}` per instance before touching the service.

## Cloud Run

`gcloud run services describe kb --region us-central1` and
`gcloud run revisions list --service kb --region us-central1`. A candidate that
failed its smoke test never received traffic, so a Cloud Run outage is not a bad
deploy by construction. Check the Google Cloud status page and the service's logs
before anything else.
