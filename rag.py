"""Role-aware retrieval over a document corpus.

ponytail: TF-IDF, not embeddings. This is the baseline the evaluation layer
exists to beat. Swap in a semantic retriever once the golden set shows TF-IDF
losing, and keep the number that justified the swap.
"""
import os
import re
import glob
import textwrap

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Who may see what. A request carries a role; a chunk carries a role. The
# request's clearance must cover the chunk's.
CLEARANCE = {"student": {"public"},
             "staff": {"public", "staff"},
             "admin": {"public", "staff", "confidential"}}

# The role that can see everything. Derived rather than written down, so adding
# a clearance level cannot leave this pointing at the second-highest one.
MAX_ROLE = max(CLEARANCE, key=lambda r: len(CLEARANCE[r]))


def parse(path):
    """Split a markdown file into its front matter and body."""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    meta = {"role": "public", "title": os.path.basename(path)}
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


def load(docs_dir="docs"):
    """Every chunk in the corpus, each tagged with its source and role."""
    chunks = []
    for path in sorted(glob.glob(os.path.join(docs_dir, "*.md"))):
        meta, body = parse(path)
        for i, c in enumerate(chunk(body)):
            chunks.append({"text": c, "source": os.path.basename(path),
                           "title": meta["title"], "role": meta["role"], "i": i})
    return chunks


class Index:
    def __init__(self, chunks):
        self.chunks = chunks
        self.vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
        # The title is indexed with the chunk but not stored into it. People
        # search using a document's title ("faculty compensation bands") far
        # more often than its wording, and parse() lifts the title out of the
        # body into metadata, so without this the exact title matches nothing.
        # Kept out of `text` so it does not repeat in every prompt.
        self.matrix = self.vec.fit_transform(
            [f"{c['title']}\n{c['text']}" for c in chunks])

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
        sims = cosine_similarity(self.vec.transform([query]), self.matrix[idx])[0]
        ranked = sorted(zip(idx, sims), key=lambda t: -t[1])[:k]
        return [dict(self.chunks[i], score=round(float(s), 4))
                for i, s in ranked if s > 0]


if __name__ == "__main__":
    # Retrieval on its own, with no model and no API key. Worth keeping as a
    # separate entry point: when an answer is wrong, the first question is
    # always whether the right passage was even found, and this answers it
    # without the model in the way.
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
