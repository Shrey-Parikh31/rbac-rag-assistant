"""Role-aware retrieval over a document corpus.

Retrieval is by meaning, not by spelling. Word matching was the baseline the
evaluation layer existed to beat, and it lost on every measure at once: finding,
refusing, paraphrase, and the plural bug. The table that decided it is in ADR-3.

Vectors are cached to disk and the cache is committed. That is what keeps the
tests and the retrieval scorer offline, free and instant, which is the property
that lets them gate every commit. A query not in the cache costs one API call
and is then cached too.
"""
import os
import re
import glob
import json
import time
import hashlib
import textwrap

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
VECTORS = os.environ.get("KB_VECTORS", os.path.join(HERE, "vectors.json"))
EMBED_MODEL = os.environ.get("KB_EMBED_MODEL", "gemini-embedding-001")

# 768 rather than the full 3072. These embeddings are trained so a truncated
# prefix is still a usable vector once re-normalised, and 768 scored 44/48
# against 45/48 at full size: one question out of 48, inside noise, for a
# quarter of the storage and the arithmetic. ADR-3.
EMBED_DIM = 768

# Minimum cosine similarity for a chunk to count as a match. Unrelated text from
# this model still scores well above zero, so a floor is not optional here.
#
# The only number in the system fitted to data, so it is fitted on the `tune`
# split alone and never on `test`. Earlier values (0.65, 0.635) were swept over
# every question and then reported on every question, which measures the tuning
# as much as the retriever.
#
# Selection rule, fixed before reading test: among floors scoring within one of
# the best on tune, take the midpoint. The exact peak overfits to a single case;
# the midpoint of the near-best band survives one noisy question. Here the band
# is 0.59 to 0.63, so 0.61.
#
# tune 94.5%, held-out test 90.0%. The 4.5 point gap is the honest cost of
# having written both the documents and the tune questions.
MIN_SCORE = 0.61

# Who may see what. A request carries a role; a chunk carries a role. The
# request's clearance must cover the chunk's.
CLEARANCE = {"student": {"public"},
             "staff": {"public", "staff"},
             "admin": {"public", "staff", "confidential"}}

# The role that can see everything. Derived rather than written down, so adding
# a clearance level cannot leave this pointing at the second-highest one.
MAX_ROLE = max(CLEARANCE, key=lambda r: len(CLEARANCE[r]))

_vectors = None
_client = None

# Seconds spent waiting on the embedding API, accumulated. agent.ask() reads it
# to separate "retrieval was slow" from "the model was slow", which is not
# guessable from a single total and was guessed wrong once already.
EMBED_SECONDS = 0.0


def parse(path):
    """Split a markdown file into its front matter and body."""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    meta = {"role": "public", "title": os.path.basename(path), "keywords": ""}
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    body = raw
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = m.group(2)
    return meta, body.strip()


def chunk(text, size=600, overlap=100):
    """Split on paragraphs, then pack them up to `size` characters.

    Splitting mid-idea is the most common cause of a RAG system retrieving the
    right document and still answering wrongly, so paragraphs are the unit and
    a chunk only exceeds `size` when a single paragraph already does.
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) + 2 > size:
            out.append(cur)
            cur = (cur[-overlap:] + "\n\n" + p) if overlap else p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        out.append(cur)
    return out


def load(docs_dir=None):
    """Every chunk in the corpus, each tagged with its source and role."""
    docs_dir = docs_dir or os.environ.get("KB_DOCS", os.path.join(HERE, "docs"))
    chunks = []
    for path in sorted(glob.glob(os.path.join(docs_dir, "*.md"))):
        meta, body = parse(path)
        for i, c in enumerate(chunk(body)):
            chunks.append({"text": c, "source": os.path.basename(path),
                           "title": meta["title"], "keywords": meta["keywords"],
                           "role": meta["role"], "i": i})
    return chunks


# --- embeddings ---------------------------------------------------------------

def _key(text, task):
    return hashlib.sha1(
        f"{EMBED_MODEL}|{EMBED_DIM}|{task}|{text}".encode("utf-8")).hexdigest()


def _cache():
    global _vectors
    if _vectors is None:
        if os.path.exists(VECTORS):
            with open(VECTORS, encoding="utf-8") as f:
                _vectors = json.load(f)
        else:
            _vectors = {}
    return _vectors


def embed(texts, task):
    """Vectors for `texts`, calling the API only for whatever is not cached.

    `task` is RETRIEVAL_DOCUMENT for corpus text and RETRIEVAL_QUERY for
    questions. The distinction is not cosmetic: the model deliberately encodes
    a question and the passage that answers it differently, and using one
    setting for both measurably degrades retrieval.
    """
    global _client
    cache = _cache()
    missing = [t for t in texts if _key(t, task) not in cache]
    if missing:
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            # Fail loudly rather than fall back to something weaker. A silent
            # downgrade would change what the tests measure without saying so,
            # which is the exact failure mode this project keeps tripping over.
            raise RuntimeError(
                f"{len(missing)} text(s) are not in {os.path.basename(VECTORS)} "
                f"and no GEMINI_API_KEY is set, so they cannot be embedded. The "
                f"cache covers the corpus and every question in the golden set; "
                f"a new query needs the key once. "
                f"First missing: {missing[0][:60]!r}")
        from google import genai
        from google.genai import types
        if _client is None:
            _client = genai.Client()
        started = time.monotonic()
        for i in range(0, len(missing), 100):        # the API caps a batch
            batch = missing[i:i + 100]
            r = _client.models.embed_content(
                model=EMBED_MODEL, contents=batch,
                config=types.EmbedContentConfig(
                    task_type=task, output_dimensionality=EMBED_DIM))
            for text, e in zip(batch, r.embeddings):
                cache[_key(text, task)] = [round(float(v), 6) for v in e.values]
        global EMBED_SECONDS
        EMBED_SECONDS += time.monotonic() - started
        _write_json(VECTORS, cache)
    return _unit(np.array([cache[_key(t, task)] for t in texts], dtype=np.float32))


def _write_json(path, obj):
    """Write atomically: new file beside it, then rename over the top.

    open(path, "w") truncates before writing, so a process killed in that
    window destroys the old contents without having written the new ones. That
    is not theoretical: it cost 29 cached answers here. os.replace is atomic on
    both POSIX and Windows, so a reader sees either the old file or the new one
    and never a half-written one.
    """
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _unit(m):
    """Re-normalise. Required after truncating to EMBED_DIM, not optional."""
    return m / (np.linalg.norm(m, axis=-1, keepdims=True) + 1e-9)


class Index:
    def __init__(self, chunks):
        self.chunks = chunks
        # Title and keywords are embedded with the chunk but never stored into
        # it. People search using a document's title, and more often using
        # everyday words the policy itself never uses: "hacked account" against
        # a document that says "suspected compromise". The keywords are the
        # author's own list of what people will call this, the same job a
        # librarian does with subject headings. Both stay out of `text`, so
        # neither repeats in a prompt nor reaches an answer.
        self.matrix = embed(
            [f"{c['title']}\n{c['keywords']}\n{c['text']}" for c in chunks],
            "RETRIEVAL_DOCUMENT")

    def search(self, query, role="student", k=3):
        """Top k chunks this role is cleared to see.

        The clearance filter is applied to the candidate set, not to the
        output. A chunk the caller may not see never enters the ranking, so it
        cannot be inferred from what is missing or from a score.
        """
        allowed = CLEARANCE.get(role, {"public"})
        idx = [i for i, c in enumerate(self.chunks) if c["role"] in allowed]
        if not idx:
            return []
        q = embed([query], "RETRIEVAL_QUERY")[0]
        sims = self.matrix[idx] @ q
        ranked = sorted(zip(idx, sims), key=lambda t: -t[1])[:k]
        return [dict(self.chunks[i], score=round(float(s), 4))
                for i, s in ranked if s > MIN_SCORE]


if __name__ == "__main__":
    # Retrieval on its own, with no model in the way. When an answer is wrong,
    # the first question is always whether the right passage was even found,
    # and this answers it without the model involved.
    import sys
    idx = Index(load())
    role = sys.argv[1] if len(sys.argv) > 1 else "student"
    q = " ".join(sys.argv[2:]) or "How late can I enroll in a course?"
    hits = idx.search(q, role)
    print(f"role={role}  q={q}\n")
    if not hits:
        print("  no accessible passages match")
    for h in hits:
        print(f"  {h['score']:.4f}  {h['source']}  ({h['role']})")
        print(textwrap.indent(textwrap.fill(h["text"][:300], 72), "      "), "\n")
