# KbRotationIncomplete: a revoked token is still working somewhere

Processes disagree about which token map they are honouring, and have for ten
minutes. Somebody rotated the Secret, and at least one process never picked it
up.

**Assume the revoked token still reads documents.** That is what this state
means: the pod on the old map does not know the token was revoked, and it will
keep answering it until it reloads or restarts.

## Why ten minutes and not thirty seconds

Two values during a rotation is normal. The kubelet rewrites each mounted Secret
on its own schedule, per pod, independently, so the pods converge over about a
minute; postmortem 004 measured 53 seconds end to end. This alert waits ten
minutes so that the ordinary case never fires it.

## First five minutes

1. **See the disagreement.** The token rotation panel shows one row per process,
   coloured by fingerprint. Rows that have not changed colour are the ones still
   on the old map.

   ```
   count_values("v", kb_tokens_version)
   ```

2. **Read the logs of the stuck process.** There are two very different reasons
   it can be behind, and they say so:

   - `tokens: rotation REJECTED, previous map still in force: ...` means the new
     map is malformed. **The revocation did not happen anywhere**, and the file
     is a typo made in a hurry. Fix the Secret; this is the fastest outcome.
   - `tokens: cannot read ...` means the file is gone or unreadable. Check the
     mount still has `defaultMode: 0444`: mode `0440` makes it unreadable to the
     server, which runs as user 10001 while the mounted files are owned by root.
   - **Nothing at all** means the process is not looking. Check the manifest did
     not acquire a `subPath` on the Secret mount: a Secret mounted with `subPath`
     is copied once at start and never updated, which reproduces exactly the
     failure this metric exists to catch.

3. **Close the window now, diagnose after.** If a leak is what prompted the
   rotation, do not wait for convergence:

   ```
   kubectl rollout restart deploy/kb
   ```

   About fifteen seconds, because graceful shutdown no longer spends thirty of
   them per pod. Every new process reads the Secret at startup, so a restart is
   unconditional where a reload is not.

## Cloud Run

This alert cannot fire there and its absence means nothing. Cloud Run resolves
`--set-secrets` when an instance starts and a Secret change is deployed as a new
revision, which replaces every instance. The failure needs a platform where a
Secret can change underneath a running process.
