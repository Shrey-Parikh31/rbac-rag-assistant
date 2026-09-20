"""Experiment 2: break the vector store four ways and see who notices.

    python reliability/experiments/broken_index.py

The "vector database" here is vectors.json: one embedding per chunk and per
known question. It can fail loudly or quietly, and the quiet ways are the ones
worth an experiment:

  missing    the file is gone
  truncated  the file is cut off mid-write
  random     document vectors from the wrong model: right shape, wrong meaning
  shuffled   every document holds another document's vector

For each, a server is started against the broken file and asked real golden-set
questions. The table says whether it started, whether it reported itself
healthy, and what it actually answered.

Exit code is 1 if any broken index is served as healthy, so this runs in CI as a
regression test for the fix it motivated.
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import rag  # noqa: E402
from test_serve import _free_port  # noqa: E402

TOKEN = "exp:student"
# Public questions the golden set says are answered, with the document that
# answers them, restricted to cases the recorded baseline says currently pass.
# The first version took the first eight answerable questions and included a
# known retrieval miss, so the healthy index scored 7/8 and the experiment
# could not tell its control from a failure.
_PASSING = set(json.load(open(os.path.join(ROOT, "eval", "baseline.json")))["passing"])
PROBES = [(c["q"], c["source"]) for c in
          (json.loads(l) for l in open(os.path.join(ROOT, "eval", "golden.jsonl"), encoding="utf-8"))
          if c["kind"] == "answer" and c["role"] == "student" and c["id"] in _PASSING][:8]


def variants(workdir):
    """Write each broken vector file and return {name: path}."""
    src = os.path.join(ROOT, "vectors.json")
    cache = json.load(open(src, encoding="utf-8"))
    doc_keys = [rag._key(f"{c['title']}\n{c['keywords']}\n{c['text']}", "RETRIEVAL_DOCUMENT")
                for c in rag.load()]
    assert all(k in cache for k in doc_keys), "corpus vectors missing from the cache"
    rng = random.Random(7)
    out = {}

    out["healthy"] = src

    out["missing"] = os.path.join(workdir, "missing.json")  # never written

    p = os.path.join(workdir, "truncated.json")
    raw = open(src, encoding="utf-8").read()
    open(p, "w", encoding="utf-8").write(raw[: len(raw) // 2])
    out["truncated"] = p

    # Wrong model: the query vectors still come from the real one, so the
    # corpus and the questions no longer live in the same space.
    rnd = dict(cache)
    for k in doc_keys:
        rnd[k] = [rng.gauss(0, 1) for _ in range(rag.EMBED_DIM)]
    p = os.path.join(workdir, "random.json")
    json.dump(rnd, open(p, "w", encoding="utf-8"))
    out["random"] = p

    # Every document gets its neighbour's vector. Nothing is missing, the
    # shapes and magnitudes are all real, and it is wrong everywhere.
    shf = dict(cache)
    for a, b in zip(doc_keys, doc_keys[1:] + doc_keys[:1]):
        shf[a] = cache[b]
    p = os.path.join(workdir, "shuffled.json")
    json.dump(shf, open(p, "w", encoding="utf-8"))
    out["shuffled"] = p
    return out


def probe(vectors_path):
    port = _free_port()
    # NO_DOTENV: otherwise serve.py re-reads .env and the removed key comes back.
    env = dict(os.environ, KB_TOKENS=TOKEN, PORT=str(port), KB_VECTORS=vectors_path, NO_DOTENV="1")
    env.pop("GEMINI_API_KEY", None)   # a missing vector must not be quietly re-embedded
    env.pop("GOOGLE_API_KEY", None)
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "serve.py")], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(40):
            if proc.poll() is not None:
                last = [l for l in proc.stderr.read().strip().splitlines() if l.strip()]
                return {"started": False, "why": last[-1][:90] if last else "?"}
            try:
                urllib.request.urlopen(base + "/health", timeout=1)
                healthy = True
                break
            except urllib.error.HTTPError as e:
                healthy = e.code == 200
                break
            except OSError:
                time.sleep(0.25)
        else:
            return {"started": False, "why": "never answered"}

        right = wrong = none = 0
        for q, source in PROBES:
            req = urllib.request.Request(base + "/search?q=" + urllib.parse.quote(q))
            req.add_header("Authorization", "Bearer exp")
            text = json.loads(urllib.request.urlopen(req, timeout=10).read())["result"]
            if text.startswith("No passages"):
                none += 1
            elif f"[{source} " in text.split("\n", 1)[0]:
                right += 1
            else:
                wrong += 1
        return {"started": True, "healthy": healthy, "right": right, "wrong": wrong, "none": none}
    finally:
        proc.kill()


def main():
    import urllib.parse  # noqa: F401  (used inside probe)
    work = tempfile.mkdtemp()
    try:
        rows = {name: probe(path) for name, path in variants(work).items()}
    finally:
        shutil.rmtree(work, ignore_errors=True)

    n = len(PROBES)
    print(f"\n{'index':<11}{'started':<9}{'healthy':<9}{'right':>6}{'wrong':>7}{'none':>6}")
    served_broken = []
    for name, r in rows.items():
        if not r["started"]:
            print(f"{name:<11}{'no':<9}{'-':<9}{'':>19}   {r['why']}")
            continue
        print(f"{name:<11}{'yes':<9}{'yes' if r['healthy'] else 'NO':<9}"
              f"{r['right']:>4}/{n}{r['wrong']:>5}/{n}{r['none']:>4}/{n}")
        if name != "healthy" and r["healthy"]:
            served_broken.append(name)

    assert rows["healthy"].get("healthy") and rows["healthy"]["right"] == n, \
        f"the healthy index is not healthy: {rows['healthy']}"
    if served_broken:
        print(f"\nFAIL: a broken index reported healthy and served traffic: {served_broken}")
        return 1
    print("\nok: every broken index was refused before it could serve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
