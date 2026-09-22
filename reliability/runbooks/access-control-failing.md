# KbAccessControlFailing: a student may be able to read restricted material

**This is the one failure this project exists to prevent.** Treat it as a
disclosure until proven otherwise. Everything else in this directory is about a
service being slow or absent; this is about it working perfectly and answering
the wrong person.

**Nothing else can see it.** The request returned 200, quickly, and the server
recorded it as a success. Availability is unaffected, latency is unaffected, the
error budget is untouched, and every other panel is green. The external probe is
the only witness, which is why this alert is a page.

## First five minutes

1. **Which check failed?** The alert carries a `check` label.

   | check | what it was asserting |
   |---|---|
   | `search_refused` | a student asking a confidential question is refused, and the refusal describes nothing |
   | `unauthenticated` | a request with no token gets 401 |
   | `corpus` | the catalogue names no document the caller may not read |

2. **Reproduce it by hand**, with the public demo token, from your own machine:

   ```
   curl -H "Authorization: Bearer demo-student" \
     "$KB_URL/search?q=What+are+the+faculty+compensation+bands%3F"
   ```

   The correct answer is `"outcome": "restricted"` with an empty `sources`, and
   a body containing no word from the document. If the body names the subject,
   the leak is real and current.

3. **If it is real, stop serving before diagnosing.** Roll back to the previous
   revision; do not debug a live disclosure.

   ```
   gcloud run services update-traffic kb --to-revisions PREVIOUS=100
   ```

4. **If it does not reproduce**, the leak was in a revision that is no longer
   taking traffic, or in one instance of several. It still happened. Find the
   revision that was serving at the alert's timestamp before closing this.

## Where this breaks

In rough order of likelihood:

- **A new document with no `role` in its front matter**, or a typo in it. The
  index has one clearance per chunk and it comes from that field.
- **`RESTRICTED_MARGIN` or `MIN_SCORE` changed.** Both are fitted on the tune
  split. Moving them moves what counts as a match, and a confidential document
  that no longer beats the margin is answered around rather than refused.
- **The token map.** A token mapped to the wrong role hands out a clearance
  nobody meant to grant. Check `kb_tokens_version` agrees across instances and
  that the Secret says what you think it says.
- **A second code path.** `/search` calls `tools.search_docs` for exactly this
  reason: one implementation of the rule. A shortcut past it in a new route is
  a hole, and the reason ADR-4 forbids one.

## Afterwards

This alert firing means the golden set has a gap, because the golden set is
supposed to be where a leak is caught before deploying. Add the case that got
through, and check it fails against the revision that leaked.
