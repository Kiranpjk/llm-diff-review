"""Filter unified diffs and pack whole file sections into requests."""

import re
from fnmatch import fnmatchcase

DEFAULT_IGNORE = (
    "*.lock",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "bun.lockb",
    "go.sum",
    "*.min.js",
    "dist/",
    "build/",
    "vendor/",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.ico",
    "*.bmp",
    "*.tiff",
    "*.tif",
    "*.avif",
    "*.svg",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
)
TRUNCATED = "\n[File diff truncated]\n"


def parse_diff(diff):
    sections = re.split(r"(?m)(?=^diff --git )", diff)
    if len(sections) == 1:
        sections = re.split(r"(?m)(?=^--- [^\n]*\n\+\+\+ )", diff)
    for section in sections:
        if not section.strip():
            continue
        headers = section.split("\n@@", 1)[0]
        paths = re.findall(r"(?m)^(?:---|\+\+\+) (.+)$", headers)
        if not paths:
            first = section.splitlines()[0]
            boundary = re.search(r' (?="?b/)', first)
            paths = [first[boundary.end() :]] if boundary else []
        paths = [
            re.sub(r"^[ab]/", "", path.split("\t", 1)[0].strip('"'))
            for path in paths
            if path != "/dev/null"
        ]
        if paths:
            yield paths, section


def matches(path, pattern):
    if pattern.endswith("/"):
        parts = path.split("/")
        return any(
            fnmatchcase("/".join(parts[start:end]), pattern.rstrip("/"))
            for end in range(1, len(parts))
            for start in range(end)
        )
    return fnmatchcase(path, pattern) or fnmatchcase(path.rsplit("/", 1)[-1], pattern)


def chunk_diff(diff, max_chars=60000, ignore=()):
    if max_chars < len(TRUNCATED):
        raise ValueError(f"max-chars must be at least {len(TRUNCATED)}")
    chunks, skipped = [], []
    current = ""
    reviewed = 0
    for paths, section in parse_diff(diff):
        header = section.split("\n@@", 1)[0]
        if re.search(r"(?m)^(?:Binary files .* differ|GIT binary patch)$", header):
            skipped.append((paths[-1], "binary"))
            continue
        if any(
            matches(path, pattern)
            for path in paths
            for pattern in (*DEFAULT_IGNORE, *ignore)
        ):
            skipped.append((paths[-1], "ignored"))
            continue
        if len(section) > max_chars:
            section = section[: max_chars - len(TRUNCATED)] + TRUNCATED
        if len(current) + len(section) > max_chars:
            chunks.append(current)
            current = ""
        current += section
        reviewed += 1
    if current:
        chunks.append(current)
    return chunks, reviewed, skipped
