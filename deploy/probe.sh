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

# A Running pod, explicitly. `kubectl exec deploy/kb` takes the first pod
# matching the selector, which during a failed rollout can be the broken one
# that will never start, turning "is the Service up" into "did the deliberately
# broken pod come up", whose answer is always no.
pod=$(kubectl get pods -l app=kb --field-selector=status.phase=Running \
        -o jsonpath='{.items[0].metadata.name}')
test -n "$pod" || { echo "no Running pod to probe from"; exit 1; }

kubectl exec "$pod" -- python -c "
import json, urllib.request
body = json.load(urllib.request.urlopen('http://kb/health', timeout=5))
assert body.get('ok') and body.get('chunks', 0) > 0, body
print('  service healthy from inside the cluster:', body)
"
