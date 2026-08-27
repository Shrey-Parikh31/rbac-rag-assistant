"""python test_rag.py"""
from rag import Index, load, chunk, parse

idx = Index(load())

# chunking packs whole paragraphs up to the size target. Three 290-char
# paragraphs: two fit (290 + 2 + 290 = 582), three do not, so expect [ab][c].
# The separator counts, which is why 300-char paragraphs pack one per chunk.
c = chunk("\n\n".join(["a" * 290, "b" * 290, "c" * 290]), size=600, overlap=0)
assert len(c) == 2, f"expected 2 chunks, got {len(c)}"
assert c[0].count("\n\n") == 1 and c[1] == "c" * 290, c
assert all("\n\n\n" not in x for x in c)

# a paragraph longer than the target is kept whole rather than split mid-idea,
# because half an idea retrieves badly and answers worse
assert len(chunk("z" * 2000, size=600, overlap=0)) == 1

# front matter is parsed, and a file without it defaults to public
meta, body = parse("docs/salary-bands.md")
assert meta["role"] == "confidential", meta
assert not body.startswith("---")

# retrieval finds the right document
hits = idx.search("how late can I enroll", role="student")
assert hits, "no hits for an enrollment question"
assert hits[0]["source"] == "enrollment.md", hits[0]["source"]

# --- the security property, which is the whole point of the role filter ---
# a student must never see confidential content, by any query
for q in ["faculty salary", "compensation bands", "how much do professors earn",
          "adjunct per credit hour", "$1,450"]:
    for h in idx.search(q, role="student"):
        assert h["role"] == "public", f"student saw {h['role']} chunk for {q!r}"

# staff see staff content but still not confidential
staff = idx.search("who do I report a security incident to", role="staff")
assert staff and staff[0]["source"] == "incident-response.md", staff
for h in idx.search("faculty compensation", role="staff"):
    assert h["role"] != "confidential", "staff saw confidential content"

# admin can. This query is also the regression test for the title-indexing bug:
# "faculty compensation bands" appears only in the front matter, not the body.
admin = idx.search("faculty compensation bands", role="admin")
assert any(h["role"] == "confidential" for h in admin), "admin blocked from confidential"

# an unknown role gets the least privilege, not the most
for h in idx.search("faculty compensation", role="intern"):
    assert h["role"] == "public", "unknown role defaulted to more than public"

print("ok")
