"""Embed every attack's text once, so the corpus runs free from then on.

Same arrangement as the golden set. Each attack's query text is embedded a
single time, the vector is committed to vectors.json, and every run afterwards
is offline: no key, no network, no cost, which is what lets the corpus run on
every push rather than on a good day.

    python security/warm_cache.py --dry-run     what it would cost
    python security/warm_cache.py               do it

One batched request, not one per attack. It prints what is missing and the
estimated cost before spending anything, because this project shares a small
prepaid budget with another one.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import rag          # noqa: E402
import redteam      # noqa: E402

# Published rate for gemini-embedding-001, per million input tokens. Only used
# to print an estimate, so being slightly out is fine; being silent is not.
USD_PER_MTOK = 0.15


def attack_texts():
    """Every string that will be handed to the embedder when the corpus runs."""
    texts = []
    for a in redteam.load():
        if a["surface"] == "query":
            texts.append(a["payload"])
        elif a["surface"] == "tool" and a.get("tool") == "search_docs":
            texts.append(a["args"]["query"])
    # Dict rather than set: order is stable, so two runs print the same list.
    return list(dict.fromkeys(texts))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    texts = attack_texts()
    cache = rag._cache()
    missing = [t for t in texts
               if rag._key(t, "RETRIEVAL_QUERY") not in cache]

    chars = sum(len(t) for t in missing)
    tokens = chars / 4          # the usual rough ratio, good enough for an estimate
    print(f"{len(texts)} attack texts, {len(missing)} not yet cached")
    print(f"about {tokens:.0f} tokens, roughly ${tokens / 1e6 * USD_PER_MTOK:.6f}")

    if not missing:
        print("nothing to do")
        return 0
    if args.dry_run:
        print("\ndry run, nothing sent")
        return 0

    # An empty or whitespace-only attack is rejected by search_docs before it
    # reaches the embedder, so paying to embed it would buy a vector nothing
    # ever looks up.
    billable = [t for t in missing if t.strip()]
    if len(billable) != len(missing):
        print(f"skipping {len(missing) - len(billable)} empty payload(s), which "
              f"are refused before retrieval and need no vector")

    os.environ.pop("KB_READ_ONLY_CACHE", None)   # the write path, deliberately
    rag.embed(billable, "RETRIEVAL_QUERY")

    cache = rag._cache()
    still = [t for t in billable if rag._key(t, "RETRIEVAL_QUERY") not in cache]
    if still:
        print(f"FAILED: {len(still)} text(s) still not cached", file=sys.stderr)
        return 1
    print(f"cached {len(billable)} texts; vectors.json now has {len(cache)} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
