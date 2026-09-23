#!/usr/bin/env bash
# Ask the Service, from inside the cluster, whether it is serving.
#
# Exists because `kubectl port-forward svc/kb` does not go through the Service.
# It selects one pod and tunnels to it, so it reports that pod's health while
# looking like it reports the Service's. During a failed rollout that is exactly
# the wrong answer: the Service is fine, the tunnel's pod may not be, and the
# pipeline concluded the service had gone down when it had not.
#
# From inside, `http://kb` is the real Service DNS name, resolved by the real
# cluster DNS and load balanced across the real endpoints.
#
# ponytail: exec into a pod we already run rather than starting a curl image.
# The container has python and no curl, so urllib it is -- no pull, no wait.
set -euo pipefail

# Pick a pod that is Ready and is not on its way out.
#
# `kubectl exec deploy/kb` takes the first pod matching the selector, which
# during a failed rollout can be the broken one that will never start, turning
# "is the Service up" into "did the deliberately broken pod come up", whose
# answer is always no.
#
# `--field-selector=status.phase=Running` was the first fix and it was not
# enough. **A terminating pod still reports phase Running** for its whole grace
# period. The chaos step deletes a pod with --wait=false and probes immediately,
# so this selected the pod that was in the middle of dying, exec'd into it, and
# was killed along with it: exit 137, reported as the Service being down, at the
# exact moment the experiment was checking that the Service stays up. The one
# failure this script exists to prevent, reintroduced through a different field.
#
# The tell is `metadata.deletionTimestamp`, set the moment a delete is accepted
# and long before the pod leaves the list.
#
# ponytail: python3 rather than jq. Both are on the runner, and this one can be
# tested on a laptop against a saved `kubectl get pods -o json` without a
# cluster, which is how the selector below was checked before it shipped.
select_pod() {
  kubectl get pods -l app=kb -o json | python3 -c '
import json, sys
for pod in json.load(sys.stdin)["items"]:
    if pod["metadata"].get("deletionTimestamp"):
        continue                      # accepted for deletion, still phase Running
    conditions = pod.get("status", {}).get("conditions") or []
    if any(c["type"] == "Ready" and c["status"] == "True" for c in conditions):
        print(pod["metadata"]["name"])
        break
'
}

probe() {
  kubectl exec "$1" -- python -c "
import json, urllib.request
body = json.load(urllib.request.urlopen('http://kb/health', timeout=5))
assert body.get('ok') and body.get('chunks', 0) > 0, body
print('  service healthy from inside the cluster:', body)
"
}

# One retry, because a pod that was healthy when it was chosen can begin
# terminating a moment later and nothing can hold it still. Retrying the probe
# is right; retrying forever would hide a Service that is genuinely down, so
# this gets exactly one more chance from a freshly chosen pod and then fails.
for attempt in 1 2; do
  pod=$(select_pod)
  test -n "$pod" || { echo "no Ready pod to probe from"; exit 1; }
  if probe "$pod"; then
    exit 0
  fi
  echo "  probe via $pod did not complete, choosing another pod" >&2
  sleep 3
done
exit 1
