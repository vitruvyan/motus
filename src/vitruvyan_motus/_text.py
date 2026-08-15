"""What a Motus string is, in one place both sides of the package import.

A Motus string denotes a sequence of Unicode scalar values (rule `J1`,
ADR-026). This module exists because that check belongs at every position a
string enters a trace -- `Fact.key`, `Decision.reason`, an adapter's
`receipt_id` -- and those live in modules that import each other. It has no
imports of its own and never will: `trace` imports `effects`, so anything both
need has to sit below both.

The contract validator carries its own copy of the same predicate, because the
kernel is stdlib-only and `contract/validate.py` imports `jsonschema`.
`tests/test_surrogate_boundary.py` runs both over one table of strings and
asserts they agree, which is the strongest thing available under that
constraint.
"""

from __future__ import annotations

__all__ = ["encodable", "surrogate_at"]


def encodable(text: str) -> str:
    """Refuse a str that has no UTF-8 encoding (J1).

    A lone surrogate is a Python `str` and passes every other J1 check, so it
    reached the seal and raised UnicodeEncodeError out of `Runtime.run()` —
    not NodeFailed, so the caller got no state and no trace, and the sink kept
    a run with no terminal record. It is not an exotic input: `json.loads` of
    an escaped `\\ud800` produces one silently, which is any node parsing an
    external payload with a broken surrogate pair in it.

    `isascii()` is a C-level flag check and answers for the overwhelming
    majority of strings without encoding anything, so the cost of this on the
    hot path is a branch.
    """
    index = surrogate_at(text)
    if index is not None:
        raise ValueError(
            f"U+{ord(text[index]):04X} at index {index} is an unpaired "
            "surrogate: it denotes no character, has no UTF-8 encoding, and is "
            "not an RFC 8259 JSON string (rule J1, ADR-026)")
    return text


def surrogate_at(text: str) -> int | None:
    """The index of the first surrogate code point in ``text``, or None.

    The producing half of the pair `contract/validate.py::_surrogate_at` forms.
    **The two are deliberately the same test and deliberately not the same
    code**: this package is stdlib-only and the contract validator imports
    `jsonschema`, so the kernel cannot import it. A duplicated predicate is how
    two sides drift, which is the exact defect ADR-026 closes -- so
    `tests/test_surrogate_boundary.py` runs both over one table of strings and
    asserts they return the same verdict for every one.
    """
    if text.isascii():
        return None
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        return exc.start
    return None
