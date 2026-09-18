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
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import rag
import tools

PORT = int(os.environ.get("PORT", "8080"))
MAX_QUERY = 500  # a question, not a payload


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

    def _send(self, code, body):
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
        import agent  # deferred: the server starts and serves /search without the SDK
        answer = agent.ask(question, role=role)
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


def main():
    started = time.monotonic()
    index()  # fail here, before the port opens, if the corpus is unreadable
    print(f"index built in {time.monotonic() - started:.2f}s, "
          f"{len(_index.chunks)} chunks; listening on :{PORT}", file=sys.stderr)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
