"""Compare how the index tokenises text. Word-level TF-IDF treats "professors"
and "professor" as unrelated words, so "What do full professors earn?" scores
zero against a document that says "Full Professor". Character n-grams overlap on
the shared stem instead, which costs nothing extra to install.

    python eval/analyzer.py

Same purpose as sweep.py: settle an argument with a number, then delete.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sklearn.feature_extraction.text import TfidfVectorizer

import rag
import tools
from retrieval import load_golden, score  # noqa: E402

CONFIGS = {
    "word 1-2 (current)": dict(stop_words="english", ngram_range=(1, 2)),
    "word 1-2 sublinear": dict(stop_words="english", ngram_range=(1, 2), sublinear_tf=True),
    "char_wb 3-5":        dict(analyzer="char_wb", ngram_range=(3, 5)),
    "char_wb 4-6":        dict(analyzer="char_wb", ngram_range=(4, 6)),
}

_original = rag.Index.__init__


def patched(kwargs):
    def __init__(self, chunks):
        self.chunks = chunks
        self.vec = TfidfVectorizer(**kwargs)
        self.matrix = self.vec.fit_transform(
            [f"{c['title']}\n{c['text']}" for c in chunks])
    return __init__


def run(name, kwargs, cases, floor):
    rag.Index.__init__ = patched(kwargs)
    rag.MIN_SCORE = floor
    tools._index = None
    results = score(cases)
    by = {}
    for r in results:
        d = by.setdefault(r["kind"], [0, 0])
        d[0] += r["ok"]
        d[1] += 1
    para = [r for r in results if r.get("paraphrase")]
    morph = [r for r in results if r.get("morphology")]
    return {"name": name, "total": sum(r["ok"] for r in results), "n": len(results),
            "by": by, "para": sum(r["ok"] for r in para), "n_para": len(para),
            "morph": sum(r["ok"] for r in morph), "n_morph": len(morph),
            "leaks": sum(bool(r["leaks"]) for r in results)}


if __name__ == "__main__":
    cases = load_golden()
    try:
        print(f"\n{'analyzer':<22}{'floor':>6}{'total':>10}{'answer':>9}"
              f"{'restr':>8}{'absent':>8}{'paraphrase':>12}{'plural':>8}{'leaks':>7}")
        for name, kwargs in CONFIGS.items():
            # Character n-grams produce higher baseline similarity across the
            # board, so each analyzer gets its floor swept rather than inheriting
            # a number tuned for a different representation.
            best = None
            for floor in (0.0, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30):
                r = run(name, kwargs, cases, floor)
                if best is None or r["total"] > best["total"]:
                    best, best["floor"] = r, floor
            b = best["by"]
            f = lambda k: f"{b[k][0]}/{b[k][1]}" if k in b else "-"
            print(f"{name:<22}{best['floor']:>6.2f}{best['total']:>6}/{best['n']:<3}"
                  f"{f('answer'):>9}{f('restricted'):>8}{f('absent'):>8}"
                  f"{best['para']:>7}/{best['n_para']:<4}"
                  f"{best['morph']:>4}/{best['n_morph']:<3}{best['leaks']:>7}")
        print("\nEach row shows that analyzer at its own best floor.")
        print("'plural' is q039, which fails purely on professors vs professor.\n")
    finally:
        rag.Index.__init__ = _original
