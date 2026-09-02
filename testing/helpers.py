"""Shared helpers for the static source checks."""
from __future__ import annotations

import io
import tokenize
from pathlib import Path


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def code_only(path: Path) -> str:
    """Source with comments and string literals removed.

    The static rules here are about what the code *does*. This project's
    docstrings deliberately quote the anti-patterns and endpoints they describe,
    so scanning raw text would flag documentation as a violation.
    """
    out: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(read(path)).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    except tokenize.TokenError:
        return read(path)
    return " ".join(out)
