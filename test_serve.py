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

TOKENS = {"student": "devstudent", "staff": "devstaff", "admin": "devadmin"}
KB_TOKENS = ",".join(f"{t}:{r}" for r, t in TOKENS.items())
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
    req.add_header("Authorization", "Bearer devstudent")
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
    print("serve: ok")


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
    env = dict(os.environ, KB_TOKENS=KB_TOKENS, PORT=str(port))
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


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run(sys.argv[1].rstrip("/"))
    else:
        proc, base = _spawn()
        try:
            run(base)
        finally:
            proc.kill()
