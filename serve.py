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
        try:
            handler()
        except Exception:
            import traceback
            traceback.print_exc(file=sys.stderr)
            try:
                self._send(500, {"error": "internal error; see server logs"})
            except Exception:
                pass  # response already begun; nothing useful left to say

    def _get(self):
        url = urlparse(self.path)

        if url.path == "/healthz":
            # Unauthenticated on purpose: a load balancer has no token, and this
            # says only that the process is up and the index is built.
            return self._send(200, {"ok": True, "chunks": len(index().chunks),
                                    "dim": rag.EMBED_DIM})

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
