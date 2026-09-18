"""The system over HTTP, so something other than a terminal can reach it.

Three layers need this and none of them are a user interface. A load test needs
an endpoint to put pressure on, a chaos experiment needs a process to kill
mid-request, and a trace needs a request boundary to start at. Those are Layers
1, 2 and 5, which is why this exists now rather than when somebody asks for a
web page.

    KB_TOKENS="devstudent:student,devstaff:staff,devadmin:admin" python serve.py
    curl -H "Authorization: Bearer devstudent" "localhost:8080/search?q=how+late+can+I+enroll"

**The role comes from the token, never from the request.** mcp_server.py reads
one role from the environment because an MCP connection is one user; HTTP is
many, so the mapping has to be per-caller. A header the caller sets themselves
would let anyone type `X-Role: admin`, which is the exact failure this whole
project exists to prevent. There is deliberately no development mode that
trusts a header, because a bypass written for convenience is the one that ships.

ponytail: stdlib ThreadingHTTPServer, no web framework. One thread per request
is the wrong shape above a few hundred concurrent callers; the k6 budget in CI
is what will say when that stops being true, and gunicorn is the upgrade.
"""
import json
import os
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import rag
import tools

PORT = int(os.environ.get("PORT", "8080"))
MAX_QUERY = 500  # a question, not a payload

# Recycle every keep-alive connection after this many requests.
#
# Kubernetes balances connections, not requests: a Service picks a pod when a
# connection opens and never again. Chaos experiment 1 showed what that costs.
# When a pod drained, all ten clients reconnected at that moment, when only the
# survivor was ready. The replacement came up a few seconds later and served
# 0 requests for the rest of the run while the survivor served 48,838, pinned at
# its CPU limit, and throughput fell from 1,970 to 802 requests a second. No
# request failed, so neither an error rate nor an availability SLO would have
# noticed.
#
# Asking clients to reconnect every hundred requests lets new pods pick up
# traffic within about a second at that load, for one TCP handshake per hundred
# requests. Counting requests rather than seconds makes it self-scaling: an
# idle connection is never disturbed, and a busy one rebalances quickly, which
# is exactly when rebalancing matters. nginx does the same, with 1000.
MAX_REQUESTS_PER_CONNECTION = 100


def load_tokens():
    """`token:role,token:role` from KB_TOKENS. Absent or malformed is fatal.

    Failing at startup rather than per-request: an unauthenticated server that
    answers is worse than one that never came up, and a crash loop is visible
    in a way that a quietly permissive deployment is not.
    """
    raw = os.environ.get("KB_TOKENS", "").strip()
    if not raw:
        raise SystemExit(
            "KB_TOKENS is not set, so no caller could be identified and every\n"
            "request would have to be refused. Set it to token:role pairs, e.g.\n"
            '  KB_TOKENS="devstudent:student,devstaff:staff,devadmin:admin"\n'
            "Use real secrets outside development.")
    known = set(tools.TOOL_ACCESS["search_docs"])
    out = {}
    for pair in raw.split(","):
        token, _, role = pair.strip().partition(":")
        if not token or role not in known:
            raise SystemExit(f"bad KB_TOKENS entry {pair!r}; expected token:role "
                             f"with role in {sorted(known)}")
        out[token] = role
    return out


TOKENS = load_tokens()


class Metrics:
    """Counters and a latency histogram, in Prometheus text format, by hand.

    The format is a few lines of plain text, so a client library would be a
    dependency for string formatting. What it would have given for free is
    thread safety, which is the one thing here that is easy to get wrong: a
    ThreadingHTTPServer increments these from many threads at once, and `+= 1`
    on a dict entry is a read and a write with room for another thread between
    them. Hence the lock.

    Every label value is from a fixed set. A route label taken straight from the
    request path would let anyone create a new time series per URL they can type,
    which is how a monitoring system gets taken down by a scanner.
    """
    # Chosen around the SLO threshold (50ms) and the k6 budget (30ms), so both
    # can be read off exact bucket boundaries instead of interpolated.
    BUCKETS = (0.005, 0.01, 0.025, 0.03, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)
    ROUTES = ("/healthz", "/search", "/ask", "/metrics")

    def __init__(self):
        self.lock = threading.Lock()
        self.requests = {}   # (route, code) -> count
        self.latency = {}    # route -> [per-bucket cumulative counts..., +Inf, sum]
        self.outcomes = {}   # search outcome -> count

    def observe(self, path, code, seconds):
        route = path if path in self.ROUTES else "other"
        with self.lock:
            key = (route, str(code))
            self.requests[key] = self.requests.get(key, 0) + 1
            h = self.latency.setdefault(route, [0] * (len(self.BUCKETS) + 2))
            for i, le in enumerate(self.BUCKETS):
                if seconds <= le:
                    h[i] += 1
            h[-2] += 1          # +Inf, which is also the count
            h[-1] += seconds    # sum

    def outcome(self, name):
        with self.lock:
            self.outcomes[name] = self.outcomes.get(name, 0) + 1

    def render(self):
        with self.lock:
            out = ["# HELP kb_requests_total HTTP requests by route and status code.",
                   "# TYPE kb_requests_total counter"]
            for (route, code), n in sorted(self.requests.items()):
                out.append(f'kb_requests_total{{route="{route}",code="{code}"}} {n}')
            out += ["# HELP kb_request_duration_seconds Time to answer, by route.",
                    "# TYPE kb_request_duration_seconds histogram"]
            for route, h in sorted(self.latency.items()):
                for le, n in zip(self.BUCKETS, h):
                    out.append(f'kb_request_duration_seconds_bucket{{route="{route}",le="{le}"}} {n}')
                out.append(f'kb_request_duration_seconds_bucket{{route="{route}",le="+Inf"}} {h[-2]}')
                out.append(f'kb_request_duration_seconds_sum{{route="{route}"}} {h[-1]:.6f}')
                out.append(f'kb_request_duration_seconds_count{{route="{route}"}} {h[-2]}')
            # There is no ground truth online, so this cannot say whether an
            # answer was right. What it can say is the mix, and a mix that
            # suddenly goes all "no match" is an index that has stopped working
            # while every request still returns 200.
            out += ["# HELP kb_search_outcomes_total /search results by kind.",
                    "# TYPE kb_search_outcomes_total counter"]
            for name, n in sorted(self.outcomes.items()):
                out.append(f'kb_search_outcomes_total{{outcome="{name}"}} {n}')
            return "\n".join(out) + "\n"


METRICS = Metrics()


class Breaker:
    """Stop calling a provider that has stopped answering.

    Experiment 3 pointed /ask at a provider that accepts connections and never
    replies. Every user waited out the full timeout to be told it had failed,
    and the next user did the same, and the next: the server learned nothing
    from the previous hundred failures. A timeout bounds how long one user
    waits. This bounds how many users have to find out the hard way.

    closed     calls go through; consecutive slow failures are counted
    open       after THRESHOLD of them, calls fail at once for COOLDOWN seconds
    half-open  after the cooldown, exactly one call goes through to find out;
               success closes the breaker, failure opens it for another cooldown
    """
    def __init__(self, threshold=3, cooldown=30.0, clock=time.monotonic):
        self.threshold, self.cooldown, self.clock = threshold, cooldown, clock
        self.lock = threading.Lock()
        self.failures = 0
        self.opened_at = None
        self.probing = False

    def allow(self):
        with self.lock:
            if self.opened_at is None:
                return True
            if not self.probing and self.clock() - self.opened_at >= self.cooldown:
                self.probing = True
                return True
            return False

    def retry_after(self):
        with self.lock:
            if self.opened_at is None:
                return 0
            return max(1, int(self.cooldown - (self.clock() - self.opened_at)) + 1)

    def record(self, ok):
        with self.lock:
            self.probing = False
            if ok:
                self.failures, self.opened_at = 0, None
                return
            self.failures += 1
            if self.failures >= self.threshold or self.opened_at is not None:
                self.opened_at = self.clock()


BREAKER = Breaker()

# The bulkhead. A breaker counts failures as they *finish*, so a burst that
# arrives all at once gets past it before the first timeout has come back:
# every one of those requests holds a thread and a socket for the full timeout.
# Eight at a time, and the ninth is told immediately rather than queued behind
# a provider that may never answer. /search takes no slot and is never refused
# because /ask is busy.
ASK_CONCURRENCY = 8
ASK_SLOTS = threading.BoundedSemaphore(ASK_CONCURRENCY)


class Handler(BaseHTTPRequestHandler):
    server_version = "rbac-rag"
    sys_version = ""  # the Python version is a free gift to an attacker
    # Keep-alive. Under HTTP/1.0 every request pays a fresh TCP handshake, and a
    # load test then measures the handshake as if it were the application.
    protocol_version = "HTTP/1.1"

    # TCP_NODELAY, and the load test is what found it. The first k6 run reported
    # p95 40.94ms, p90 40.93ms, median 40.91ms, min 1.75ms: a distribution with
    # no spread at all is not a workload, it is a constant, and ~40ms is the
    # Linux delayed-ACK timer. http.server flushes the headers in one write and
    # the body in another, so Nagle's algorithm holds the second segment waiting
    # for an acknowledgement the client will not send for 40ms because it is
    # waiting for more data. Every request paid it, and retrieval itself takes
    # under 2ms.
    disable_nagle_algorithm = True

    def _send(self, code, body, headers=None):
        # Anything the caller sent and we did not read has to be drained, or the
        # connection is closed with bytes still in flight and the client sees a
        # dropped connection rather than the refusal it was actually given.
        remaining = int(self.headers.get("Content-Length") or 0) - self._read
        if remaining > 0:
            self.rfile.read(remaining)
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        # One handler object per connection, so this counts requests on it.
        self._served = getattr(self, "_served", 0) + 1
        if DRAINING.is_set() or self._served >= MAX_REQUESTS_PER_CONNECTION:
            # Go and reconnect; the new connection will be routed elsewhere.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(payload)

    def _role(self):
        """The caller's role, or None. Never read from a header the caller picks."""
        auth = self.headers.get("Authorization", "")
        scheme, _, token = auth.partition(" ")
        return TOKENS.get(token) if scheme.lower() == "bearer" else None

    _read = 0  # bytes of the request body consumed so far

    def log_message(self, fmt, *args):
        # The path only, never the query string. The query carries the user's
        # question, and the question is the thing this system exists to keep
        # private; logging it hands it to anyone who can read the logs.
        del fmt, args
        sys.stderr.write(f'{self.command} {urlparse(self.path).path} '
                         f'{getattr(self, "_status", "-")}\n')

    def send_response(self, code, message=None):
        self._status = code
        super().send_response(code, message)

    def do_GET(self):
        self._dispatch(self._get)

    def do_POST(self):
        self._dispatch(self._post)

    def _dispatch(self, handler):
        """An unhandled exception is a 500, not a dropped connection.

        The default is to let the exception escape, which closes the socket
        mid-response; the caller sees "remote end closed connection" and learns
        nothing about what happened. Layer 2 is going to break this service on
        purpose, and an experiment can only be read if the failure has a shape.

        The caller gets a generic message. The detail goes to stderr, because an
        exception string can carry a path, a query or a fragment of a document,
        and this is a system whose entire subject is material that must not
        reach the wrong reader.
        """
        started = time.monotonic()
        # Per-request state reset here, because under keep-alive one handler
        # object serves every request on a connection. Left alone, a request
        # that crashes before responding is recorded with the previous request's
        # status code, and a POST that follows another POST computes how much
        # body to drain from the last one's length.
        self._status = None
        self._read = 0
        try:
            handler()
        except Exception:
            import traceback
            traceback.print_exc(file=sys.stderr)
            try:
                self._send(500, {"error": "internal error; see server logs"})
            except Exception:
                pass  # response already begun; nothing useful left to say
        finally:
            # Recorded in `finally`, so a request that crashed is counted as the
            # 500 it was. Metrics that only see successes report a perfect
            # availability right up until the moment somebody checks by hand.
            METRICS.observe(urlparse(self.path).path, self._status or 500,
                            time.monotonic() - started)

    def _get(self):
        url = urlparse(self.path)

        if url.path == "/healthz":
            # Unauthenticated on purpose: a load balancer has no token, and this
            # says only that the process is up and the index is built.
            if DRAINING.is_set():
                return self._send(503, {"ok": False, "draining": True})
            return self._send(200, {"ok": True, "chunks": len(index().chunks),
                                    "dim": rag.EMBED_DIM})

        if url.path == "/metrics":
            # Unauthenticated, like /healthz: counts and timings only, never a
            # question, a token or a document. The outcome mix does reveal how
            # often callers reach for restricted material, which is mildly
            # sensitive, and is the reason to keep this port off the public
            # internet in a real deployment rather than behind a password.
            payload = METRICS.render().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        role = self._role()
        if role is None:
            return self._send(401, {"error": "bearer token required"})

        if url.path == "/search":
            q = (parse_qs(url.query).get("q") or [""])[0].strip()
            if not q:
                return self._send(400, {"error": "q is required"})
            if len(q) > MAX_QUERY:
                return self._send(413, {"error": f"q is longer than {MAX_QUERY} characters"})
            started = time.monotonic()
            # tools.search_docs rather than the index directly, so the HTTP path
            # makes access decisions with the same code the agent does. Two
            # implementations of one security rule is one implementation and one
            # hole waiting to be found.
            tools.set_role(role)
            result = tools.search_docs(q)
            METRICS.outcome("no_match" if result == tools.NO_MATCH else
                            "restricted" if result.startswith(tools.RESTRICTED_PREFIX)
                            else "answer")
            return self._send(200, {"role": role, "result": result,
                                    "took_s": round(time.monotonic() - started, 3)})

        return self._send(404, {"error": "not found"})

    def _post(self):
        role = self._role()
        if role is None:
            return self._send(401, {"error": "bearer token required"})
        if urlparse(self.path).path != "/ask":
            return self._send(404, {"error": "not found"})

        length = int(self.headers.get("Content-Length") or 0)
        if length > 4096:
            return self._send(413, {"error": "body too large"})
        raw = self.rfile.read(length)
        self._read = len(raw)
        try:
            question = (json.loads(raw or b"{}").get("question") or "").strip()
        except json.JSONDecodeError:
            return self._send(400, {"error": "body must be JSON"})
        if not question:
            return self._send(400, {"error": "question is required"})

        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            # Not an error in the deployment: /search is the whole retrieval
            # path and needs no key. Say which half is unavailable.
            return self._send(503, {"error": "no model credentials configured; "
                                             "/search works without them"})
        if not BREAKER.allow():
            wait = BREAKER.retry_after()
            return self._send(503, {"error": "the model provider is not responding, "
                                             "so this was not sent to it; /search "
                                             "still works", "retry_after_s": wait},
                              {"Retry-After": str(wait)})
        if not ASK_SLOTS.acquire(blocking=False):
            return self._send(503, {"error": f"{ASK_CONCURRENCY} questions are already "
                                             "waiting on the model provider; try again "
                                             "shortly. /search still works"},
                              {"Retry-After": "5"})
        try:
            import agent  # deferred: the server starts and serves /search without the SDK
            answer = agent.ask(question, role=role)
        finally:
            ASK_SLOTS.release()
        # Only failures that cost the caller time open the breaker. A rate
        # limit is refused in milliseconds, so there is nothing to protect
        # anyone from by failing it faster.
        BREAKER.record(answer["status"] not in ("transport_error", "unavailable"))
        return self._send(200 if answer["status"] == "ok" else 502, answer)


_index = None


def index():
    """Built once at startup, not per request.

    Reading four documents and a vector file on every call would put file IO
    inside the latency budget the pipeline is about to enforce, and the budget
    would then be measuring the disk.
    """
    global _index
    if _index is None:
        _index = tools.index()
    return _index


# Graceful shutdown, and chaos experiment 1 is why it exists.
#
# Killing a pod under load took 31 seconds and dropped 2 of 147,821 requests.
# The process runs as PID 1 in its container, and the kernel does not deliver
# SIGTERM to PID 1 unless a handler is installed, so the request to stop was
# ignored outright. Kubernetes waited out its 30-second grace period and then
# SIGKILLed a process that was still serving, cutting the requests in flight on
# connections that had never been told to go elsewhere.
#
# Installing the handler is the fix to the first half. The second half is what
# to do once the signal arrives. Exiting at once reopens a race the
# orchestrator cannot close for us: the pod is removed from the Service the
# moment it starts terminating, but that removal takes a moment to reach every
# node's routing, and a connection routed in that moment finds nobody. So:
#
#   1. /healthz reports 503, so anything that routes on readiness stops sending
#   2. every response carries `Connection: close`, so clients holding a
#      keep-alive connection reconnect -- through routing that now excludes us
#   3. keep serving for DRAIN_SECONDS while that happens
#   4. stop
#
# ponytail: requests still in flight at the end of the drain are cut, since
# handler threads are daemons. By then every client has been told to leave and
# routing has moved on; waiting on stragglers means joining threads parked on
# idle keep-alive sockets, which is a timeout per socket to bound it properly.
class Server(ThreadingHTTPServer):
    # The listen backlog: connections the kernel has accepted and this process
    # has not yet picked up. socketserver's default is 5.
    #
    # Found by accident in experiment 3, as one ConnectionResetError among thirty
    # simultaneous questions, and then confirmed on purpose: 200 connections
    # arriving at once, 40 refused. One in five callers turned away before the
    # server had seen them -- and since it never saw them, no metric here counted
    # them, and no alert could have fired. The only record is on the client.
    #
    # SOMAXCONN is the largest the kernel allows. The accept loop is fast, so the
    # queue only needs to absorb a burst, not hold a backlog.
    request_queue_size = socket.SOMAXCONN


DRAIN_SECONDS = float(os.environ.get("KB_DRAIN_SECONDS", "5"))
DRAINING = threading.Event()


def _drain_then_stop(server):
    time.sleep(DRAIN_SECONDS)
    server.shutdown()


# One known question per document, asked at startup, as the role cleared to
# read it. Chosen from the golden set as the passing case with the widest
# margin over the similarity floor, so a canary failing means the index is
# wrong rather than that a borderline question drifted.
#
# Experiment 2 is why. A missing or truncated vectors.json already stopped the
# server from starting. But document vectors from the wrong model started fine,
# reported healthy, and found nothing for anyone; and shuffled vectors -- every
# document holding a neighbour's -- started fine, reported healthy, and handed
# out the wrong document for half the probe questions. Nothing failed, so no
# error metric saw it, and a confidently wrong answer is not even a "no match"
# for KbIndexLooksBroken to count.
CANARIES = [
    ("How late can I enroll in a course?", "student", "enrollment.md"),
    ("Can a grade appeal go above the dean?", "student", "grading.md"),
    ("Remind me how fast a suspected compromise must be reported.", "staff", "incident-response.md"),
    ("What are the faculty compensation bands?", "admin", "salary-bands.md"),
]


def check_canaries(idx):
    """Refuse to serve an index that cannot find its own documents.

    Exiting rather than starting unready, for the same reason a missing
    KB_TOKENS exits: a crash loop is loud, the rollout that shipped it is
    refused, and the pods from the previous revision keep serving.
    """
    failed = []
    for q, role, want in CANARIES:
        hits = idx.search(q, role=role, k=1)
        got = hits[0]["source"] if hits else None
        if got != want:
            failed.append(f"{q!r} as {role}: wanted {want}, got {got or 'nothing'}")
    # ponytail: one canary per document covers every chunk today because each
    # document is one chunk. A document long enough to split would need a canary
    # per chunk, or a corrupted second half would pass unnoticed.
    if failed:
        raise SystemExit("the index failed its canary questions, so it is not "
                         "serving anything:\n  " + "\n  ".join(failed))


def main():
    import signal
    started = time.monotonic()
    index()  # fail here, before the port opens, if the corpus is unreadable
    check_canaries(_index)
    server = Server(("", PORT), Handler)

    def on_sigterm(signum, frame):
        if not DRAINING.is_set():
            DRAINING.set()
            print(f"SIGTERM: draining for {DRAIN_SECONDS:g}s", file=sys.stderr)
            threading.Thread(target=_drain_then_stop, args=(server,), daemon=True).start()

    signal.signal(signal.SIGTERM, on_sigterm)
    print(f"index built in {time.monotonic() - started:.2f}s, "
          f"{len(_index.chunks)} chunks; listening on :{PORT}", file=sys.stderr)
    server.serve_forever()
    server.server_close()
    print("drained; exiting", file=sys.stderr)


if __name__ == "__main__":
    main()
