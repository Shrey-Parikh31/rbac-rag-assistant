"""Measure the deployed service from outside it, and ship the result to Grafana.

Every number in reliability/ so far was counted by the server about itself. Two
experiments showed what that cannot see. Postmortem 003 found a listen backlog
of five turning away one connection in five during a burst: refused before the
process existed, so never counted, never timed, never logged, and a perfect
availability figure the whole time. Postmortem 001 found the mirror image, a
pod serving zero requests beside one at its CPU limit, with a 100% success rate.
A server cannot report the requests it never received.

So this runs somewhere else, on a schedule, and asks the way a user does: over
the public internet, through DNS, TLS and whatever sits in front of the
container. What it measures is therefore the whole path, including the part the
SLIs are blind to. That is the point, and it is also why its latency numbers are
much larger than the server's own; see observability/README.md.

    python observability/probe.py --url https://kb-zv5i45k6sq-uc.a.run.app
    python observability/probe.py --url ... --push     # to Grafana Cloud

It costs nothing to run. Every question it asks is already in vectors.json, so
no check reaches the embedding provider and no check spends the API budget. That
is a property worth keeping: change a question here and the probe starts billing
the project once every fifteen minutes, forever, quietly.

ponytail: Influx line protocol over plain HTTP, no client library. Grafana Cloud
accepts it at /api/v1/push/influx/write and turns `measurement,tag=v field=1`
into `measurement_field{tag="v"}`, which is the whole dependency that
prometheus-remote-write's protobuf and snappy would have bought.
"""
import argparse
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

# The demo token, which is in the README and in the deployed environment. The
# probe deliberately holds no privilege: a checker with an admin token can only
# prove that admins can read admin documents, which is not the question anyone
# is worried about.
TOKEN = os.environ.get("KB_PROBE_TOKEN", "demo-student")
TIMEOUT = 20.0   # generous: a Cloud Run cold start is part of what we measure


class Check:
    """One request, and what has to be true about the answer.

    `forbidden` is checked against the entire response body, not against one
    field, because a leak that mattered would not announce which field it came
    out of. The words are the subject of the restricted documents, which is what
    the service promises never to reveal to a student, in postmortem terms the
    difference between refusing and refusing while describing what was refused.
    """

    def __init__(self, name, path, token=TOKEN, status=200,
                 expect=None, forbidden=()):
        self.name, self.path, self.token = name, path, token
        self.status, self.expect, self.forbidden = status, expect, forbidden

    def run(self, base):
        req = urllib.request.Request(base.rstrip("/") + self.path)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                code, body = r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            # A 401 is a pass for the unauthenticated check and a failure for
            # the rest, so the status comparison below decides, not the class
            # of the exception.
            code, body = e.code, e.read().decode("utf-8", "replace")
        except urllib.error.URLError as e:
            # Three different failures, and telling them apart is most of the
            # value. "Cannot connect" is the service being down. A TLS failure
            # is a certificate that expired, or a probe host with no trust
            # store, and reporting it as "down" would send somebody to look at
            # the wrong system at 3am. A timeout is the service being up and
            # unusable, which is the one that also spends the latency budget.
            if isinstance(e.reason, TimeoutError):
                reason = "timeout"
            elif isinstance(e.reason, ssl.SSLError):
                reason = "tls"
            else:
                reason = "connection"
            print(f"{self.name}: {reason}: {e.reason}", file=sys.stderr)
            return 0, time.monotonic() - started, reason
        except TimeoutError:
            return 0, time.monotonic() - started, "timeout"
        took = time.monotonic() - started

        if code != self.status:
            return 0, took, "status"
        low = body.lower()
        if any(word in low for word in self.forbidden):
            return 0, took, "leak"
        if self.expect:
            try:
                payload = json.loads(body)
            except ValueError:
                return 0, took, "content"
            if not self.expect(payload):
                return 0, took, "content"
        return 1, took, "ok"


# Words that appear in the restricted documents and nowhere a student should
# see. If any of these ever comes back on a student's request, the one rule this
# entire project exists to enforce has been broken, and no server-side metric
# would say so: the request returned 200 and was fast.
LEAK_WORDS = ("compensation band", "pay scale", "adjunct",        # salary-bands.md
              "ransomware", "phishing", "escalation",             # incident-response.md
              "salary-bands.md", "incident-response.md")
# Each word was checked to appear in exactly one restricted document and in no
# readable one, so a hit is a leak and not a coincidence. A word added here
# without that check turns the probe into an alarm nobody trusts.

CHECKS = [
    # No token at all. The service is public, so this is the check that says
    # whether it is still refusing strangers, which is a thing a bad deploy can
    # silently stop doing.
    Check("unauthenticated", "/search?q=anything", token=None, status=401),

    # Cheapest possible request. Its duration is the closest thing this project
    # has to a cold-start measurement: the server's own clock starts after the
    # container is already running.
    Check("health", "/health"),

    # A question whose answer is known, so "returns 200" is not mistaken for
    # "works". This is postmortem 002's failure mode: an index that has loaded
    # the wrong vectors answers everything, confidently, from the wrong file.
    Check("search_hit", "/search?q=How+late+can+I+enroll+in+a+course%3F",
          expect=lambda p: p.get("outcome") == "answer"
          and any(s["source"] == "enrollment.md" for s in p.get("sources", []))),

    # The same shape of request, for a document this caller may not read. The
    # correct answer is a refusal that describes nothing.
    Check("search_refused", "/search?q=What+are+the+faculty+compensation+bands%3F",
          expect=lambda p: p.get("outcome") == "restricted" and not p.get("sources"),
          forbidden=LEAK_WORDS),

    # The catalogue. A student is told how many documents exist above their
    # clearance, never what they are called.
    Check("corpus", "/corpus", forbidden=LEAK_WORDS,
          expect=lambda p: bool(p.get("readable"))),
]


def measure(base, origin):
    """Run every check and return Influx line protocol.

    One timestamp for the whole run, so the points line up on a dashboard
    instead of smearing across the seconds the checks took.
    """
    stamp = time.time_ns()
    lines, failed = [], []
    for check in CHECKS:
        up, took, reason = check.run(base)
        if not up:
            failed.append(f"{check.name} ({reason})")
        lines.append(
            f"kb_probe,check={check.name},origin={origin},reason={reason} "
            f"up={up}i,duration_seconds={took:.4f} {stamp}")
    return lines, failed


def push(lines, url, user, token):
    body = "\n".join(lines).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "text/plain")
    auth = base64.b64encode(f"{user}:{token}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=os.environ.get(
        "KB_URL", "https://kb-zv5i45k6sq-uc.a.run.app"))
    ap.add_argument("--origin", default=os.environ.get("KB_PROBE_ORIGIN", "local"),
                    help="a label for where the probe ran; keep the set small")
    ap.add_argument("--push", action="store_true",
                    help="send to Grafana Cloud; needs GRAFANA_PUSH_URL, "
                         "GRAFANA_USER and GRAFANA_TOKEN")
    ap.add_argument("--fail-on-error", action="store_true",
                    help="exit non-zero if any check failed, for use as a "
                         "post-deploy smoke test rather than as a monitor")
    args = ap.parse_args()

    lines, failed = measure(args.url, args.origin)
    print("\n".join(lines))
    if failed:
        print(f"FAILED: {', '.join(failed)}", file=sys.stderr)

    if args.push:
        url = os.environ.get("GRAFANA_PUSH_URL")
        user, token = os.environ.get("GRAFANA_USER"), os.environ.get("GRAFANA_TOKEN")
        if not (url and user and token):
            # Not an error. The probe is useful before anyone has a Grafana
            # account, and a scheduled job that fails for a missing credential
            # teaches whoever is on call to ignore it.
            print("no Grafana credentials set; printed only", file=sys.stderr)
        else:
            try:
                print(f"pushed {len(lines)} points, HTTP {push(lines, url, user, token)}",
                      file=sys.stderr)
            except urllib.error.HTTPError as e:
                # A monitor that goes down must not look like the thing it
                # monitors going down, so this is loud and separate.
                print(f"push FAILED: HTTP {e.code} {e.read()[:200]!r}", file=sys.stderr)
                return 2

    return 1 if (failed and args.fail_on_error) else 0


if __name__ == "__main__":
    sys.exit(main())
