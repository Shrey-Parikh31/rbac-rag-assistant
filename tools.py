"""The tools the assistant may call, and the rules that bound them.

Two decisions here matter more than the tools themselves.

**The caller's role is not a tool parameter.** It is bound out of band before
the loop starts. A model that is argued into believing the user is an
administrator still cannot act on that belief, because there is no argument to
pass. Had `search_docs(query, role)` existed, the model would hold its own
clearance and any prompt injection in a retrieved document would be an
escalation path.

**Arguments are validated before execution, not after.** The model proposes;
this module decides. That boundary is the only reason tool calling is safe
enough to expose to text a stranger wrote.
"""
import os
import json
import contextvars
from datetime import datetime, timezone

from rag import Index, load, MAX_ROLE

# Set by the agent before a turn. The model cannot read or write it.
_role = contextvars.ContextVar("role", default="student")

# Least privilege: each tool names the roles allowed to call it. A read tool is
# open to everyone because the retrieval layer already filters by clearance;
# the write tool is not, because filing a ticket has an effect in the world.
TOOL_ACCESS = {
    "search_docs": {"student", "staff", "admin"},
    "check_academic_standing": {"student", "staff", "admin"},
    "file_ticket": {"staff", "admin"},
}

# Sentinel replies. Named because eval/ classifies responses by matching them,
# and a scorer that string-matches a message someone later reworded produces a
# confident wrong number rather than an error.
NO_MATCH = ("No passages match that query. No document covers it at any "
            "clearance level.")
RESTRICTED_PREFIX = "No passages you are cleared to read match that query, but "

SEVERITIES = {1, 2, 3}
TICKETS = os.environ.get("KB_TICKETS", "tickets.jsonl")

_index = None


def set_role(role):
    """Bind the caller's role for this turn. Called by the agent, never by a tool."""
    _role.set(role)
    return role


def index():
    """The corpus, built once. TF-IDF over a few thousand chunks is milliseconds."""
    global _index
    if _index is None:
        _index = Index(load(os.environ.get("KB_DOCS", "docs")))
    return _index


class Denied(Exception):
    """A tool call the caller's role is not permitted to make."""


def _permit(name):
    role = _role.get()
    if role not in TOOL_ACCESS.get(name, set()):
        raise Denied(f"role {role!r} may not call {name!r}")
    return role


# --- the tools ----------------------------------------------------------------
# Plain functions, so agent.py and mcp_server.py can each wrap them without
# either one owning the implementation. Docstrings are the tool descriptions the
# model reads, so they are written for the model, not for us.

def search_docs(query: str, k: int = 3) -> str:
    """Search internal policy documents and return the passages that match.

    Only documents the current user is cleared to read are searched. If nothing
    matches, say so rather than answering from memory.

    Args:
        query: What to look for, in natural language.
        k: How many passages to return. 1 to 8.
    """
    role = _permit("search_docs")
    if not isinstance(query, str) or not query.strip():
        return "Error: query must be a non-empty string."
    k = max(1, min(int(k), 8))

    hits = index().search(query, role=role, k=k)
    if hits:
        return "\n\n".join(f"[{h['source']} score={h['score']}]\n{h['text']}"
                           for h in hits)

    # Nothing the caller may read. Distinguish "no such policy" from "a policy
    # exists that you are not cleared for" and say which (ADR-7). What crosses
    # this boundary is a classification label, never a passage, a title, or a
    # filename: enough to point someone at the right office, not enough to
    # answer their question.
    restricted = index().search(query, role=MAX_ROLE, k=1)
    if restricted:
        required = restricted[0]["role"]
        return (RESTRICTED_PREFIX +
                f"material classified '{required}' does match. Tell the user "
                f"that guidance on this exists and requires {required} "
                f"clearance, and that they should contact the office that owns "
                f"it. You have not been shown the contents and must not "
                f"speculate about them.")
    return NO_MATCH


def check_academic_standing(gpa: float, consecutive_probation_semesters: int = 0) -> str:
    """Determine a student's academic standing from their cumulative GPA.

    Applies the published rule. Use this rather than reasoning about the
    thresholds yourself, so the answer matches what the registrar would say.

    Args:
        gpa: Cumulative GPA, 0.0 to 4.0.
        consecutive_probation_semesters: Semesters already spent on probation.
    """
    _permit("check_academic_standing")
    try:
        gpa = float(gpa)
    except (TypeError, ValueError):
        return "Error: gpa must be a number."
    if not 0.0 <= gpa <= 4.0:
        return "Error: gpa must be between 0.0 and 4.0."

    prior = max(0, int(consecutive_probation_semesters))
    if gpa >= 2.0:
        return f"GPA {gpa:.2f} is in good standing (the probation threshold is 2.0)."
    if prior >= 1:
        return (f"GPA {gpa:.2f} is below 2.0 after {prior} semester(s) already on "
                f"probation, which is academic suspension.")
    return f"GPA {gpa:.2f} is below 2.0, which places the student on academic probation."


def file_ticket(summary: str, severity: int = 3) -> str:
    """File an IT service desk ticket. Staff and administrators only.

    Severity 1 is reserved for events affecting student data or authentication
    systems; it pages the on-call engineer immediately, at any hour. Do not use
    it for anything else.

    Args:
        summary: One line describing the problem, 10 to 200 characters.
        severity: 1, 2, or 3. See above before choosing 1.
    """
    role = _permit("file_ticket")
    summary = (summary or "").strip()
    if not 10 <= len(summary) <= 200:
        return "Error: summary must be between 10 and 200 characters."
    if severity not in SEVERITIES:
        return f"Error: severity must be one of {sorted(SEVERITIES)}."

    ticket = {"id": f"INC-{_next_id():04d}", "summary": summary, "severity": severity,
              "filed_by": role, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with open(TICKETS, "a", encoding="utf-8") as f:
        f.write(json.dumps(ticket) + "\n")
    return f"Filed {ticket['id']} at severity {severity}."


def _next_id():
    """ponytail: line count as the counter. Single writer, single process.
    Move to a real store if Layer 2's support simulation ever runs concurrently."""
    if not os.path.exists(TICKETS):
        return 1042  # Layer 2's ticket log starts at INC-1042
    with open(TICKETS, encoding="utf-8") as f:
        return 1042 + sum(1 for line in f if line.strip())


TOOLS = [search_docs, check_academic_standing, file_ticket]
