# KbIndexLooksBroken — everything succeeds, nothing is found

**Severity:** ticket. **Means:** more than half of searches in 15 minutes found no
passage above the similarity floor, with at least 50 searches in that time.
Around a fifth is normal: the golden set has that many questions that correctly
have no answer.

The availability SLO cannot see this. Every request returned 200.

## Likely causes, most likely first

1. **Query and corpus vectors from different models.** If the corpus vectors
   were regenerated with one embedding model and queries are embedded with
   another, similarity scores collapse below the 0.61 floor and everything
   misses, successfully. Compare the deployed `KB_EMBED_MODEL` with the model
   `vectors.json` was built with.

2. **The floor moved.** `MIN_SCORE` changed in a commit. The retrieval gate in CI
   should have stopped this, because every golden case that was answered would
   now fail. If it got through, find out how the gate was bypassed.

3. **The corpus loaded short.** `curl <service>/health` reports `chunks`. Fewer
   than the number of documents means `docs/` did not make it into the image;
   the `.dockerignore` allowlist names it explicitly.

4. **The traffic changed, not the system.** A new population asking about things
   the corpus has never covered produces exactly this signal with nothing broken.
   A step change at a deploy is the system; a gradual drift is the questions.

## Confirming it offline

```bash
python eval/retrieval.py --gate
```

Against the image's code and vectors, with no network. If the gate passes, the
index is fine and the questions have changed. If it fails, it names the cases.
