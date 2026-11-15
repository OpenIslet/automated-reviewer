"""A rough size of a text in tokens, for limits that must hold before any provider is asked.

Deliberately high: three characters to a token, where ordinary English runs nearer four. Each real model call also
asks its own provider for an exact count first (docs/SPEC.md, section 6).
"""


def estimate(text: str) -> int:
    return (len(text) + 2) // 3
