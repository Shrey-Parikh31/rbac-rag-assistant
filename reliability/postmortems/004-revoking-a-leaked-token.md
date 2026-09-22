# Postmortem 004: revoking a leaked token

**Type:** deliberate experiment, `reliability/experiments/rotate_tokens.sh`, run in
the CI cluster on every push. **Status:** resolved, with a residual window stated
below.

## Summary

A staff member's token leaks. The response any team would reach for is to
rotate the Secret. This experiment asked the question a security team would
ask: from the moment the Secret changes, how long can the leaked token still read
documents?

**Before: indefinitely, and the rotation also locked out the legitimate user.**
**After: 53 seconds, with no restart.**

## Results

Four probes per tick through the real Service, from inside the cluster. Each
column is one probe; the Service picks a pod per connection, so a mix means the
pods disagree.

**Before** (token map from an environment variable):

```
before rotation:  old: 200 200 200 200 | new: 401 401 401 401
secret rotated;   the old token is now revoked
  t+  0s          old: 200 200 200 200 | new: 401 401 401 401
  t+ 10s          old: 200 200 200 200 | new: 401 401 401 401
  ...
FAIL: the revoked token still read documents 150s after rotation
```

**After** (token map from a mounted file, re-read within a second):

```
  t+  0s  old: 200 200 200 200 | new: 200 401 401 401
  t+ 11s  old: 401 401 401 200 | new: 401 401 401 200
  t+ 21s  old: 200 200 200 401 | new: 401 401 401 401
  t+ 32s  old: 401 401 200 200 | new: 401 200 401 200
  t+ 42s  old: 200 200 200 401 | new: 401 401 401 200
  t+ 53s  old: 401 401 401 401 | new: 200 200 200 200
revoked token refused by every probe 53s after rotation
```

## What was wrong

The pods took their token map from the Secret through `envFrom`. **A Pod's
environment is fixed when the Pod starts.** Changing the Secret afterwards changes
nothing any running process can see. So rotating the Secret:

- **revoked nothing.** The leaked token kept working on every pod, with no end
  date, until somebody thought to restart every pod by hand.
- **locked out the rightful owner.** The replacement token was refused on every
  pod for the same reason.

During an incident, that is the worst available combination. The person
responding believes the leak is closed, and the only visible symptom is that the
legitimate user starts reporting 401s, which looks like the rotation working.

The deployment guide already said "rotation is a restart". That was accurate. It
was also a sentence someone would need to remember while dealing with a leak,
and nothing enforced it.

## The fix

The Secret is mounted as a file, and `serve.py` re-reads `KB_TOKENS_FILE` at most
once a second, swapping the map when the content changes.

Two details that would have quietly undone it:

- **No `subPath`.** A Secret mounted with `subPath` is copied once at start and
  never updated, reproducing exactly the original failure.
- **Mode `0444`, not `0440`.** The mounted files are owned by root and the server
  runs as user 10001, so `0440` would have made the server's own token map
  unreadable to it. That was caught by reading the manifest before pushing, not
  by the pipeline.

**A malformed rotation is ignored, and the previous map stays in force.** This
was a deliberate choice between two bad outcomes. Rejecting the old map would
lock every user out on a typo made in a hurry. Keeping it means a malformed
revocation does not revoke. It matches startup behaviour, where a bad map stops
the new pod and the old pods keep serving. The server logs
`rotation REJECTED, previous map still in force`, so the failure is not silent.

## The residual window

The kubelet rewrites a mounted Secret on its own sync schedule, roughly a minute,
**per pod, independently**. In the run above one pod had the new map almost
immediately and the other took about 50 seconds. For that window:

- the leaked token still works on some requests
- the new token fails on some requests, so a legitimate user sees intermittent 401s

53 seconds is bounded and automatic, which "whenever someone restarts
everything" was not. It is still not instant. If the leak is severe enough that
a minute matters, the response is to rotate **and** restart:
`kubectl rollout restart deploy/kb` finishes in about 15 seconds, because
graceful shutdown (postmortem 001) no longer waits 30 seconds per pod.

## Cloud Run

Cloud Run resolves `--set-secrets` when an instance starts, so it has the
original behaviour. That is acceptable there for a different reason: changing a
Secret on Cloud Run is done by deploying a new revision, which replaces every
instance. The failure mode here needs a platform where the Secret can change
underneath running processes, which is what Kubernetes allows.

## Lessons

**"Rotate the secret" is two steps, and most systems only do the first.** Storing
the new value is easy. Getting every running process to stop honouring the old
one is the part that decides whether a leak is closed.

**An incident response can look successful by failing.** The only symptom of the
broken rotation was 401s for the legitimate user, which is also what a working
rotation produces for whoever still has the old token. Only probing *both*
tokens, from inside the cluster, told the two apart.

## Action items

| | Status |
|---|---|
| Mount the token Secret as a file; reload within a second | done |
| Keep the previous map on a malformed rotation, and log it | done |
| Unit test: rotation takes effect, malformed rotation is ignored | done |
| Experiment runs in CI with a 150s deadline | done |
| Expose the token-map version as a metric, so "have all pods rotated?" is a query rather than a probe loop | **open**: Layer 5 |
