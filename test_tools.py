"""python test_tools.py

No API key needed. The agent loop is the SDK's; what is worth testing is the
boundary around it: who may call what, and what happens to bad arguments.
"""
import os
import tempfile

os.environ["KB_TICKETS"] = os.path.join(tempfile.mkdtemp(), "tickets.jsonl")

import tools
from tools import (Denied, search_docs, check_academic_standing, file_ticket,
                   set_role, TICKETS)

# --- least privilege ----------------------------------------------------------
# The write tool is the one with an effect in the world, so it is the one gated.
set_role("student")
try:
    file_ticket("the printer on floor two is offline", 3)
    raise AssertionError("a student filed a ticket")
except Denied:
    pass

set_role("staff")
assert "INC-1042" in file_ticket("the printer on floor two is offline", 3)

# ids increment, and the log is append-only
assert "INC-1043" in file_ticket("wifi is down in the east wing", 2)
assert sum(1 for _ in open(TICKETS, encoding="utf-8")) == 2

# --- arguments are validated before execution ---------------------------------
assert "Error" in file_ticket("too short", 3), "accepted a 9-character summary"
assert "Error" in file_ticket("a perfectly reasonable summary", 0), "accepted severity 0"
assert "Error" in file_ticket("a perfectly reasonable summary", 99)
# a rejected call must not reach the log
assert sum(1 for _ in open(TICKETS, encoding="utf-8")) == 2

# --- retrieval is still role-filtered when reached through a tool -------------
# rag.py proves the index filters. This proves the tool does not route around it.
set_role("student")
assert "1,450" not in search_docs("adjunct pay per credit hour")
assert "78,000" not in search_docs("assistant professor salary")

set_role("admin")
assert "1,450" in search_docs("adjunct pay per credit hour"), "admin blocked"

# k is clamped rather than trusted
set_role("student")
assert search_docs("enrollment deadline", k=999)
assert "Error" in search_docs("", k=3)

# --- ADR-7: existence is disclosed, content is not ----------------------------
# A student asking about staff-only material is told it exists and that they
# cannot read it, rather than being told nothing exists.
set_role("student")
blocked = search_docs("who do I report a security incident to")
assert "staff" in blocked and "clearance" in blocked, blocked
# ...but nothing from inside the document crosses the line: no text, no filename
assert "service desk" not in blocked.lower(), "leaked content of a staff document"
assert "one hour" not in blocked.lower(), "leaked content of a staff document"
assert "incident-response.md" not in blocked, "leaked a filename"

# same for confidential material, and the label must name the right level
blocked = search_docs("faculty compensation bands")
assert "confidential" in blocked, blocked
assert "78,000" not in blocked and "1,450" not in blocked, "leaked salary figures"
assert "salary-bands.md" not in blocked, "leaked a filename"

# a question no document covers is answered differently, which is the whole point
absent = search_docs("policy on wearing hats during examinations")
assert "no document covers it" in absent.lower(), absent
assert "does match" not in absent, "claimed restricted material exists when none does"

# Known limitation, recorded rather than asserted away: lexical matching also
# fires on innocent questions that happen to share vocabulary with a restricted
# document, e.g. "who is a full professor here" scores 0.28 against the salary
# bands. No threshold separates that from a legitimate match at 0.19, so this is
# a retrieval-quality problem for Layer 3 to measure, not a policy one.
# What must hold regardless is the property below: however wrong the match is,
# no restricted content ever crosses the boundary.
for probe in ["who is a full professor here", "what happens in the first hour of class",
              "how do I make an appointment with my advisor"]:
    out = search_docs(probe)
    for secret in ["78,000", "96,000", "1,450", "187,000", "service desk",
                   "postmortem", "on-call"]:
        assert secret not in out, f"{probe!r} leaked {secret!r}"

# staff get the same treatment one level up: told confidential material exists
set_role("staff")
blocked = search_docs("faculty compensation bands")
assert "confidential" in blocked and "78,000" not in blocked, blocked

# an administrator sees content, not a notice
set_role("admin")
assert "1,450" in search_docs("adjunct pay per credit hour")

# --- the deterministic rule ---------------------------------------------------
set_role("student")
assert "good standing" in check_academic_standing(3.6)
assert "good standing" in check_academic_standing(2.0), "2.0 is the threshold, not below it"
assert "probation" in check_academic_standing(1.9)
assert "suspension" in check_academic_standing(1.9, 1)
assert "suspension" in check_academic_standing(1.9, 2)
assert "Error" in check_academic_standing(4.5)
assert "Error" in check_academic_standing("nonsense")

# --- an unknown role gets nothing, not everything -----------------------------
set_role("provost")
for name in tools.TOOL_ACCESS:
    try:
        getattr(tools, name)("a query long enough to pass validation")
        raise AssertionError(f"unknown role called {name}")
    except Denied:
        pass

print("ok")
