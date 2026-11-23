"""Best-effort removal of likely secrets."""

import re

PRIVATE_KEY_BOUNDARY = re.compile(
    r"-----((?:BEGIN)|(?:END)) [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
    r"|^diff --git ",
    re.MULTILINE,
)

PATTERNS = (
    (
        (
            r"""(?i)([\w.-]*(?:password|passwd|secret|token|api[_-]?key)[\w.-]*["']?\s*[:=]\s*)"""
            r"""(?!\[REDACTED\]|[\w.]*\()"""
            r"""(?:"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|[^\s"',;()]+)"""
        ),
        r"\1[REDACTED]",
    ),
    (r"(://[^\s:/@]+:)[^\s@/]+(@)", r"\1[REDACTED]\2"),
    (r"\bAKIA[A-Z0-9]{16}\b", "[REDACTED]"),
    (r"\bgh[po]_[A-Za-z0-9_]{8,}", "[REDACTED]"),
    (r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]"),
    (r"\bxox[a-z]-[A-Za-z0-9-]{8,}", "[REDACTED]"),
)


def redact_private_keys(text):
    pieces = []
    start = cursor = depth = count = 0
    for match in PRIVATE_KEY_BOUNDARY.finditer(text):
        boundary = match[1]
        if boundary == "BEGIN":
            if depth == 0:
                start = match.start()
            depth += 1
            count += 1
            continue
        if not depth:
            continue
        depth = depth - 1 if boundary else 0
        if depth == 0:
            end = match.end() if boundary else match.start()
            pieces.extend((text[cursor:start], "[REDACTED]"))
            pieces.append("\n" * text.count("\n", start, end))
            cursor = end
    if depth:
        pieces.extend((text[cursor:start], "[REDACTED]"))
        cursor = len(text)
    pieces.append(text[cursor:])
    return "".join(pieces), count


def redact(text):
    text, count = redact_private_keys(text)
    for pattern, replacement in PATTERNS:
        text, replaced = re.subn(pattern, replacement, text)
        count += replaced
    return text, count
