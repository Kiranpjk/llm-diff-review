"""Best-effort removal of likely secrets."""

import re

PRIVATE_KEY_BOUNDARY = re.compile(
    r"-----((?:BEGIN)|(?:END)) [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
    r"|^diff --git ",
    re.MULTILINE,
)

PATTERNS = (
    r"""(?i)(\b(?:password|secret|token|api_key)\b["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')""",
    r"\bAKIA[A-Z0-9]{16}\b",
    r"\bgh[po]_[A-Za-z0-9_]{8,}",
    r"\bsk-[A-Za-z0-9_-]{8,}",
    r"\bxox[a-z]-[A-Za-z0-9-]{8,}",
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
    for index, pattern in enumerate(PATTERNS):
        replacement = r"\1[REDACTED]" if index == 0 else "[REDACTED]"
        text, replaced = re.subn(pattern, replacement, text)
        count += replaced
    return text, count
