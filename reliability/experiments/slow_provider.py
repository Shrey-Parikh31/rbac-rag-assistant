"""Experiment 3: the model provider stops answering.

    python reliability/experiments/slow_provider.py

The provider is replaced with a tarpit: a socket that accepts every connection
and never sends a byte. That is the worst kind of outage for a caller, because
a refused connection fails in a millisecond while silence has to be waited out.

Three questions:
  1. How long does a user of /ask wait, and what do they get at the end?
  2. Does a burst of hung /ask requests slow /search, which never touches the
     provider?
  3. How much does the server hold open while it waits?

Nothing here needs a real key or the network. The tarpit is local.
"""
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from test_serve import _free_port  # noqa: E402

BURST = int(os.environ.get("BURST", "30"))
TOKEN = "exp"


def tarpit():
    """Accept everything, answer nothing, hold every socket open."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(512)
    held = []

    def loop():
        while True:
            conn, _ = s.accept()
            held.append(conn)       # keep a reference so it is never closed
    threading.Thread(target=loop, daemon=True).start()
    return s.getsockname()[1], held


def get(base, path, timeout=10):
    req = urllib.request.Request(base + path)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    return code, time.perf_counter() - t


def ask(base, timeout=600):
    req = urllib.request.Request(base + "/ask", method="POST",
                                 data=json.dumps({"question": "How late can I enroll?"}).encode())
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body, code = json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        body, code = json.loads(e.read() or b"{}"), e.code
    except Exception as e:  # the client's own timeout
        body, code = {"status": f"client gave up: {type(e).__name__}"}, None
    return code, body.get("status") or body.get("error", "")[:60], time.perf_counter() - t


def search_latency(base, seconds=5, workers=5):
    """p50/p95 of /search over a few seconds of steady load, in ms."""
    stop = time.monotonic() + seconds
    lat, errors = [], 0

    def worker():
        nonlocal errors
        while time.monotonic() < stop:
            code, s = get(base, "/search?q=How+late+can+I+enroll+in+a+course%3F")
            if code == 200:
                lat.append(s * 1000)
            else:
                errors += 1
    with ThreadPoolExecutor(workers) as pool:
        for _ in range(workers):
            pool.submit(worker)
    lat.sort()
    return {"n": len(lat), "p50": statistics.median(lat), "p95": lat[int(0.95 * len(lat))],
            "errors": errors}


def thread_count(pid):
    """Live threads in the server process. Linux only; None elsewhere."""
    try:
        return int(next(l.split()[1] for l in open(f"/proc/{pid}/status") if l.startswith("Threads:")))
    except OSError:
        return None


def main():
    pit_port, held = tarpit()
    port = _free_port()
    env = dict(os.environ, KB_TOKENS=f"{TOKEN}:student", PORT=str(port),
               GEMINI_API_KEY="not-a-real-key",
               GOOGLE_GEMINI_BASE_URL=f"http://127.0.0.1:{pit_port}")
    env.pop("GOOGLE_API_KEY", None)
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "serve.py")], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(base + "/healthz", timeout=1)
                break
            except OSError:
                time.sleep(0.25)

        before = search_latency(base)
        print(f"/search, provider irrelevant:   p50 {before['p50']:.1f}ms  "
              f"p95 {before['p95']:.1f}ms  ({before['n']} requests, {before['errors']} errors)")
        print(f"server threads at rest:         {thread_count(proc.pid)}")

        print(f"\nsending {BURST} /ask requests at a provider that never answers...")
        pool = ThreadPoolExecutor(BURST)
        started = time.monotonic()
        asks = [pool.submit(ask, base) for _ in range(BURST)]
        time.sleep(3)

        during = search_latency(base)
        print(f"/search, {BURST} asks hung:         p50 {during['p50']:.1f}ms  "
              f"p95 {during['p95']:.1f}ms  ({during['n']} requests, {during['errors']} errors)")
        print(f"server threads while hung:      {thread_count(proc.pid)}")
        print(f"connections held at provider:   {len(held)}")

        results = [f.result() for f in asks]
        pool.shutdown()
        times = sorted(r[2] for r in results)
        kinds = {}
        for code, status, _ in results:
            kinds[(code, status)] = kinds.get((code, status), 0) + 1
        print(f"\n/ask outcome after {time.monotonic() - started:.0f}s:")
        print(f"  waited  min {times[0]:.1f}s  median {statistics.median(times):.1f}s  "
              f"max {times[-1]:.1f}s")
        for (code, status), n in sorted(kinds.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>3} x  HTTP {code}  {status}")
        print(f"  provider saw {len(held)} connection attempts for {BURST} questions")

        # The question a timeout cannot answer: does the next user also have to
        # wait it out, now that the server has watched the provider fail?
        attempts_before = len(held)
        with ThreadPoolExecutor(10) as p2:
            second = [f.result() for f in [p2.submit(ask, base) for _ in range(10)]]
        t2 = sorted(r[2] for r in second)
        k2 = {}
        for code, status, _ in second:
            k2[(code, status)] = k2.get((code, status), 0) + 1
        print(f"\nsecond wave, 10 more questions after the first failed:")
        print(f"  waited  min {t2[0]:.2f}s  median {statistics.median(t2):.2f}s  max {t2[-1]:.2f}s")
        for (code, status), n in sorted(k2.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>3} x  HTTP {code}  {status}")
        print(f"  provider saw {len(held) - attempts_before} new connection attempts")
        return {"before": before, "during": during, "ask_times": times, "kinds": kinds,
                "attempts": attempts_before, "second_times": t2, "second_kinds": k2}
    finally:
        proc.kill()


def verdict(r):
    """What must stay true. Measured before any fix: every one of 10 users
    waited 63.4s for an error, and the provider saw every question."""
    problems = []
    if r["attempts"] > 8:
        problems.append(f"{r['attempts']} requests reached a dead provider; the bulkhead allows 8")
    if r["ask_times"][-1] > 20:
        problems.append(f"a user waited {r['ask_times'][-1]:.0f}s for an error; the timeout is 15s")
    if r["second_times"][-1] > 1:
        problems.append(f"after the provider failed, the next user still waited "
                        f"{r['second_times'][-1]:.1f}s; the breaker should answer at once")
    if any(code != 503 for code, _ in r["second_kinds"]):
        problems.append(f"second wave was not refused fast: {r['second_kinds']}")
    lost = sum(n for (code, _), n in r["kinds"].items() if code is None)
    if lost:
        # A caller whose connection was reset got no answer at all -- not even
        # the fast 503. The first CI run of this experiment lost one of thirty
        # that way, which is how the listen backlog of 5 was found.
        problems.append(f"{lost} caller(s) never got an HTTP response at all")
    if r["during"]["errors"]:
        problems.append(f"/search failed {r['during']['errors']} times while /ask was hung")
    for p in problems:
        print("FAIL:", p)
    if not problems:
        print("\nok: bounded wait, bounded concurrency, and the breaker spares everyone after")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(verdict(main()))
