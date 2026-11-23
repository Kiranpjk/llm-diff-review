"""Command-line entry point."""

import argparse
import os
import re

from .diff import chunk_diff
from .github import MARKER, GitHub
from .llm import DEFAULT_MODEL, SEVERITIES, parse_response, review
from .redact import redact


def read_file(path):
    with open(path, encoding="utf-8") as source:
        return source.read()


def markdown(text):
    text = redact(str(text))[0]
    text = " ".join(text.split())
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("@", "&#64;")
    return re.sub(r"([\\|`*_\[\]~#!])", r"\\\1", text)


def render(reviews, model, reviewed, skipped, redactions):
    findings = [finding for result in reviews for finding in result["findings"]]
    findings.sort(key=lambda finding: SEVERITIES.index(finding["severity"]))
    lines = [MARKER, "## AI diff review", "", "### Summary", ""]
    lines.extend(f"- {markdown(result['summary'])}" for result in reviews)
    if not reviews:
        lines.append("No reviewable file diffs.")
    lines += [
        "",
        "### Findings",
        "",
        "| Severity | File | Line | Title | Explanation |",
        "| --- | --- | --- | --- | --- |",
    ]
    for finding in findings:
        cells = [
            markdown(finding[key])
            for key in ("severity", "file", "line", "title", "explanation")
        ]
        lines.append("| " + " | ".join(cells) + " |")
    if not findings:
        lines.append("| — | — | — | No findings reported | — |")
    if skipped:
        lines += ["", "### Skipped files", ""]
        lines.extend(f"- {markdown(path)} ({reason})" for path, reason in skipped)
    footer = (
        f"\n\nModel: {markdown(model)[:500]} · Files reviewed: {reviewed} · "
        f"Files skipped: {len(skipped)} · Secrets redacted: {redactions}\n\n"
        "AI review can be wrong; verify before acting\n"
    )
    body = []
    length = len(footer)
    for line in lines:
        length += len(line.encode("utf-8")) + 1
        if length > 64000:
            body.append("\n[Comment truncated]")
            break
        body.append(line)
    return "\n".join(body) + footer, findings


def main(argv=None):
    parser = argparse.ArgumentParser(description="Review a pull request diff")
    parser.add_argument("--diff-file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mock-response-file")
    parser.add_argument(
        "--model", default=os.getenv("LLM_DIFF_REVIEW_MODEL") or DEFAULT_MODEL
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=os.getenv("LLM_DIFF_REVIEW_MAX_CHARS") or "60000",
    )
    parser.add_argument("--ignore", action="append", default=[])
    parser.add_argument(
        "--fail-on",
        choices=["high"],
        default=os.getenv("LLM_DIFF_REVIEW_FAIL_ON") or None,
    )
    args = parser.parse_args(argv)
    if args.fail_on not in (None, "high"):
        parser.error("--fail-on must be high")
    if args.dry_run and not args.diff_file:
        parser.error("--dry-run requires --diff-file")
    if args.mock_response_file and not args.dry_run:
        parser.error("--mock-response-file requires --dry-run")
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key and not args.mock_response_file:
        parser.error("ANTHROPIC_API_KEY is required")
    try:
        github = None if args.dry_run else GitHub()
        diff = read_file(args.diff_file) if args.diff_file else github.fetch_diff()
        diff, redactions = redact(diff)
        ignore = [
            pattern.strip()
            for value in [os.getenv("LLM_DIFF_REVIEW_IGNORE", ""), *args.ignore]
            for pattern in re.split(r"[,\n]", value)
            if pattern.strip()
        ]
        chunks, reviewed, skipped = chunk_diff(diff, args.max_chars, ignore)
        if args.mock_response_file:
            results = (
                [parse_response(read_file(args.mock_response_file))] if chunks else []
            )
        else:
            results = [review(chunk, args.model, key) for chunk in chunks]
        body, findings = render(results, args.model, reviewed, skipped, redactions)
        if args.dry_run:
            print(body, end="")
        else:
            github.upsert_comment(body)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"llm-diff-review: {redact(str(error))[0]}\n")
    return int(
        args.fail_on == "high" and any(item["severity"] == "high" for item in findings)
    )
