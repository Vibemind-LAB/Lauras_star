"""A script may only claim what the reviews say is on screen.

Live finding: told to fill a ~415-word budget from 170s of held screens, the author wrote a
grounded first line per chapter and then a SECOND line of invented capability — "health-check
endpoint", "code hashes", "prompt histories", "deterministic task IDs", "signed-off config".
None appear in any scene review. The film's own message is that Captain Cook does not
fabricate and asks instead; the script fabricated nine capabilities the product lacks.

Ungrounded specifics are the checkable part. The check flags multi-word technical terms that
appear nowhere in the reviews or transcript — it cannot judge prose, so it reports rather
than rejects, and the author decides.
"""

from __future__ import annotations

from laura.short_creator.production_tools import ungrounded_terms

REVIEWS = (
    "A screen recording of a webpage titled 'Review MCP server selection' with a list of "
    "key-free servers. The list includes 'SQLite', 'airtable-mcp-server', 'ais-fleet'. "
    "A diagram of an organizational structure with a 'ChiefSalesOfficerAgent' node and an "
    "'Approve Architecture' button. A code editor showing analyze_call_transcript."
)


def test_the_invented_capabilities_that_shipped_are_flagged() -> None:
    """The exact padding from the live run."""
    text = (
        "Audit logs attach versioned agent configs, deterministic task IDs, and links to "
        "input artifacts. Engineers can inspect agent code hashes and prompt histories."
    )
    found = ungrounded_terms(text, REVIEWS)

    lowered = " ".join(found).lower()
    assert "deterministic task ids" in lowered or "task ids" in lowered
    assert "prompt histories" in lowered


def test_a_grounded_qualifier_does_not_launder_an_invented_head() -> None:
    """"code hashes" is invented even though a code editor IS on screen. The head noun carries
    the claim, so a grounded qualifier must not excuse it."""
    assert ungrounded_terms("Engineers inspect agent code hashes.", REVIEWS) != []


def test_prose_that_never_reaches_a_capability_noun_is_ignored() -> None:
    """The precision that makes the list readable: ordinary narration does not end on one."""
    assert ungrounded_terms("Engineers inspect what the picker lists.", REVIEWS) == []


def test_terms_the_reviews_actually_saw_are_not_flagged() -> None:
    """SQLite and airtable-mcp-server were read off the screen — correctly grounded."""
    text = "The picker lists SQLite for state and airtable-mcp-server for persistence."
    assert ungrounded_terms(text, REVIEWS) == []


def test_ordinary_prose_is_not_flagged() -> None:
    """The check must not drown the author in false positives on plain narration."""
    text = "Most demos hide the work behind a chat transcript, so you cannot see what ran."
    assert ungrounded_terms(text, REVIEWS) == []


def test_a_fully_grounded_script_reports_nothing() -> None:
    text = "An Approve Architecture button waits, and the code editor shows a real blocker."
    assert ungrounded_terms(text, REVIEWS) == []
