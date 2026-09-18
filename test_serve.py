"""python test_serve.py [base_url]

End to end over HTTP, against a running server. With no argument it starts one
itself; in CI it is pointed at the container, which is the only way to test the
image that actually ships rather than the source it was built from.

The concurrency check is the one that matters. tools.py binds the caller's role
to a contextvar, and a threaded server runs every request in a different thread.
If that binding leaked between threads, a staff request arriving while a student
request was in flight could answer the student from staff material, and no
single-threaded test would ever see it. The rest of the suite tests the rule;
this tests that the rule survives being used by more than one person at a time.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# The same `token:role` map the server reads, so a test run and the server it
# talks to cannot disagree about who is who. Taken from the environment when
# present: the deployed service does not use these tokens, because this
# repository is public and a token anybody can read is not a clearance.
KB_TOKENS = os.environ.get("KB_TOKENS") or \
    "devstudent:student,devstaff:staff,devadmin:admin"
TOKENS = {role: token for token, _, role in
          (pair.strip().partition(":") for pair in KB_TOKENS.split(","))}
assert set(TOKENS) >= {"student", "staff"}, \
    f"KB_TOKENS must cover at least student and staff, got {sorted(TOKENS)}"
# Only in the staff document, and in no tool description. eval/retrieval.py
# explains at length why that distinction matters.
STAFF_ONLY = "five business days"


def call(base, path, role=None, body=None, timeout=30):
    req = urllib.request.Request(base + path,
                                 data=json.dumps(body).encode() if body else None,
                                 method="POST" if body else "GET")
    if role:
        req.add_header("Authorization", f"Bearer {TOKENS[role]}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def check_load_test_questions(base):
    """Every question the load test asks must already have a vector.

    The container ships with no model credentials, so an uncached question is a
    500 rather than a slow answer -- and a load test whose p95 is the latency of
    an error message stays comfortably inside its budget while measuring
    nothing. Two of the first six questions were uncached when they were typed
    straight into the k6 script, which is why the list now lives in a file that
    both sides read.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "perf", "questions.json"), encoding="utf-8") as f:
        questions = json.load(f)
    assert questions, "perf/questions.json is empty"
    for q in questions:
        status, body = call(base, "/search?q=" + urllib.parse.quote(q), role="student")
        assert status == 200, f"load test question {q!r} returns {status}: {body}"
        assert len(body["result"]) > 20, f"load test question {q!r} returns nothing"


def check_metrics(base):
    """The counters moved, and moved in the right places.

    Exact counts are only checked against a local server. Behind a load
    balancer with several instances, /metrics answers from whichever instance
    received it, which may not be the one that served the requests above.
    """
    with urllib.request.urlopen(base + "/metrics", timeout=10) as r:
        text = r.read().decode()
    for name in ("kb_requests_total", "kb_request_duration_seconds", "kb_search_outcomes_total"):
        assert f"# TYPE {name}" in text, f"{name} missing from /metrics"

    # The request to /nope earlier must be counted as route="other". A label
    # copied from the path would let anyone mint a time series per URL.
    assert 'route="/nope"' not in text, "an arbitrary path became a metric label"

    if not base.startswith(("http://127.0.0.1", "http://localhost")):
        return
    series = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.rpartition(" ")
            series[key] = float(value)
    ok = series.get('kb_requests_total{route="/search",code="200"}', 0)
    assert ok >= 40, f"expected at least 40 successful searches counted, got {ok}"
    assert series.get('kb_requests_total{route="/search",code="401"}', 0) >= 1, \
        "the unauthenticated search was not counted"
    assert series.get('kb_requests_total{route="other",code="404"}', 0) >= 1, \
        "the unknown path was not counted under route=other"
    # A histogram's +Inf bucket is its count, by definition. If they disagree the
    # exposition is malformed and every percentile computed from it is fiction.
    assert series['kb_request_duration_seconds_bucket{route="/search",le="+Inf"}'] == \
        series['kb_request_duration_seconds_count{route="/search"}']
    answered = series.get('kb_search_outcomes_total{outcome="answer"}', 0)
    assert answered > 0, "no search was classified as answered"


def run(base):
    status, body = call(base, "/healthz")
    assert status == 200 and body["ok"], body
    assert body["chunks"] > 0, "server is up with an empty index"

    # --- no token, no answer ---------------------------------------------------
    assert call(base, "/search?q=anything")[0] == 401
    assert call(base, "/ask", body={"question": "hi"})[0] == 401

    # A caller cannot promote themselves. This is the whole point of reading the
    # role from the token: the request may say whatever it likes.
    req = urllib.request.Request(base + "/search?q=what+do+adjuncts+earn")
    req.add_header("Authorization", f"Bearer {TOKENS['student']}")
    req.add_header("X-Role", "admin")
    with urllib.request.urlopen(req, timeout=30) as r:
        assert json.loads(r.read())["role"] == "student", "a header changed the role"

    # --- the access rule, over HTTP -------------------------------------------
    q = "how long do I have to remediate an incident"
    status, staff = call(base, f"/search?q={q.replace(' ', '+')}", role="staff")
    assert status == 200 and STAFF_ONLY in staff["result"].lower(), staff
    _, student = call(base, f"/search?q={q.replace(' ', '+')}", role="student")
    assert STAFF_ONLY not in student["result"].lower(), \
        f"a student was served staff material: {student['result'][:200]}"

    # --- bad input is refused, not crashed on ---------------------------------
    assert call(base, "/search", role="student")[0] == 400
    assert call(base, "/search?q=" + "x" * 600, role="student")[0] == 413
    assert call(base, "/nope", role="student")[0] == 404

    # --- roles do not bleed across threads ------------------------------------
    # 40 interleaved requests, alternating clearance. Every student reply must
    # still be free of staff material while staff replies are being produced in
    # neighbouring threads.
    def one(i):
        role = "staff" if i % 2 else "student"
        _, b = call(base, f"/search?q={q.replace(' ', '+')}", role=role)
        return role, b["role"], b["result"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        for asked, answered, text in pool.map(one, range(40)):
            assert asked == answered, f"asked as {asked}, answered as {answered}"
            if asked == "student":
                assert STAFF_ONLY not in text.lower(), "staff material leaked under load"

    check_load_test_questions(base)
    check_metrics(base)
    check_connection_recycling(base)
    print("serve: ok")


def check_connection_recycling(base):
    """A keep-alive connection is asked to reconnect after 100 requests.

    Without this a replacement pod receives nothing until clients happen to
    reconnect: measured as 0 requests to the new pod against 48,838 to the
    survivor in chaos experiment 1.
    """
    import http.client
    url = urllib.parse.urlparse(base)
    if url.hostname not in ("127.0.0.1", "localhost"):
        # Behind a proxy (Cloud Run) the proxy owns the client's connection and
        # our Connection: close is spent on the proxy, so the client cannot see
        # it. Nothing to check from out here.
        return
    conn = http.client.HTTPConnection(url.netloc, timeout=10)
    headers = {"Authorization": f"Bearer {TOKENS['student']}"}
    closed_at = None
    for i in range(1, 121):
        conn.request("GET", "/search?q=how+late+can+I+enroll", headers=headers)
        r = conn.getresponse()
        r.read()
        if r.getheader("Connection", "").lower() == "close":
            closed_at = i
            break
    conn.close()
    assert closed_at == 100, f"recycled after {closed_at} requests, expected 100"


def _free_port():
    """A port nothing is already listening on.

    A fixed port looks harmless and is not. A server left running from an
    earlier session answers /healthz instantly, the spawn loop sees a healthy
    service and proceeds, and the whole suite then tests the stale process --
    with whatever code and whatever credentials it happened to start with. That
    is not hypothetical: it is how a question with no cached vector got embedded
    during a run that was supposed to prove no API key was needed.

    Asking the operating system for a free port removes the collision instead of
    detecting it. There is a gap between closing this socket and the server
    binding it, which nothing else on a developer machine or a CI runner is
    racing for.
    """
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _spawn():
    port = _free_port()
    env = dict(os.environ, KB_TOKENS=KB_TOKENS, PORT=str(port), KB_DRAIN_SECONDS="2")
    p = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "serve.py")],
                         env=env, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        if p.poll() is not None:
            raise SystemExit(f"server exited immediately with code {p.returncode}")
        try:
            if call(base, "/healthz", timeout=2)[0] == 200:
                return p, base
        except Exception:
            time.sleep(0.5)
    p.kill()
    raise SystemExit("server did not become healthy in 30s")


def check_breaker():
    """The circuit breaker's states, against a clock the test controls."""
    os.environ.setdefault("KB_TOKENS", KB_TOKENS)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from serve import Breaker
    now = [0.0]
    b = Breaker(threshold=3, cooldown=30, clock=lambda: now[0])

    for _ in range(2):
        assert b.allow()
        b.record(False)
    assert b.allow(), "opened before reaching the threshold"
    b.record(True)
    assert b.failures == 0, "a success must reset the count; failures are consecutive"

    for _ in range(3):
        b.allow()
        b.record(False)
    assert not b.allow(), "three consecutive failures did not open the breaker"
    assert b.retry_after() == 31

    now[0] = 29.9
    assert not b.allow(), "let a call through before the cooldown ended"
    now[0] = 30.0
    assert b.allow(), "no probe after the cooldown"
    assert not b.allow(), "more than one probe while half-open"
    b.record(False)
    assert not b.allow(), "a failed probe must reopen the breaker"

    now[0] = 60.0
    assert b.allow()
    b.record(True)
    assert b.allow() and b.allow(), "a successful probe must close the breaker"


def check_drain(proc, base):
    """SIGTERM starts a drain, not an exit.

    POSIX only: on Windows os.kill(SIGTERM) is TerminateProcess, which no
    handler can intercept, so there is nothing to test. The chaos experiment in
    CI tests the same thing under load, in a real cluster; this is the
    one-second version that fails at the line that broke.
    """
    import signal
    os.kill(proc.pid, signal.SIGTERM)
    time.sleep(0.5)
    req = urllib.request.Request(base + "/healthz")
    try:
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("still reporting healthy after SIGTERM")
    except urllib.error.HTTPError as e:
        assert e.code == 503, f"expected 503 while draining, got {e.code}"
        assert e.headers.get("Connection", "").lower() == "close", \
            "draining responses must tell the client to reconnect elsewhere"
    # Still serving real requests during the drain -- that is the point of it.
    status, _ = call(base, "/search?q=how+late+can+I+enroll", role="student")
    assert status == 200, f"stopped serving during the drain: {status}"
    try:
        proc.wait(timeout=float(os.environ.get("KB_DRAIN_SECONDS", "5")) + 5)
    except subprocess.TimeoutExpired:
        raise AssertionError("did not exit after the drain; SIGTERM ignored?")
    assert proc.returncode == 0, f"exited with {proc.returncode}, not cleanly"


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run(sys.argv[1].rstrip("/"))
    else:
        check_breaker()
        print("breaker: ok")
        proc, base = _spawn()
        try:
            run(base)
            if os.name == "posix":
                check_drain(proc, base)
                print("drain: ok")
        finally:
            proc.kill()
