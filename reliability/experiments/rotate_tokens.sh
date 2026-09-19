#!/usr/bin/env bash
# Experiment 4: revoke a token and time how long it keeps working.
#
# The scenario is a leak. Somebody's token is in a screenshot or a log, and the
# response is to rotate the Secret. The question is the one a security team
# would ask: from the moment the Secret changes, how long can the leaked token
# still read documents?
#
# Probes go through the real Service from inside the cluster, several per tick,
# because the Service picks a pod per connection and pods may disagree about
# which tokens are valid while a change is propagating.
#
#   usage: rotate_tokens.sh [deadline_seconds]
# Exit 1 if the old token is still accepted by any pod at the deadline.
set -uo pipefail

DEADLINE=${1:-150}
OLD=devstudent
NEW=rotated-student
PROBES_PER_TICK=4

status() {
  local token=$1 pod
  pod=$(kubectl get pods -l app=kb --field-selector=status.phase=Running \
          -o jsonpath='{.items[0].metadata.name}')
  kubectl exec "$pod" -- python -c "
import urllib.request, urllib.error
r = urllib.request.Request('http://kb/search?q=How+late+can+I+enroll+in+a+course%3F',
                           headers={'Authorization': 'Bearer $token'})
try:
    print(urllib.request.urlopen(r, timeout=5).status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print('ERR')" 2>/dev/null
}

tick() {  # "<old statuses> | <new statuses>", PROBES_PER_TICK of each
  local o="" n="" i
  for i in $(seq 1 $PROBES_PER_TICK); do o="$o$(status $OLD) "; n="$n$(status $NEW) "; done
  echo "$o| $n"
}

echo "before rotation:  old/new -> $(tick)"

kubectl create secret generic kb-tokens \
  --from-literal=KB_TOKENS="$NEW:student,rotated-staff:staff,rotated-admin:admin" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
t0=$(date +%s)
echo "secret rotated at $(date +%T); $OLD is now revoked"

revoked=""
while :; do
  el=$(( $(date +%s) - t0 ))
  line=$(tick)
  printf "  t+%3ss  old: %s\n" "$el" "$line"
  old_part=${line%%|*}
  if ! grep -q 200 <<<"$old_part"; then revoked=$el; break; fi
  [ "$el" -ge "$DEADLINE" ] && break
  sleep 5
done

if [ -z "$revoked" ]; then
  echo "FAIL: the revoked token still read documents ${DEADLINE}s after rotation"
  exit 1
fi
echo "revoked token refused by every probe ${revoked}s after rotation"
