"""Anthropic Messages API client."""

import json
import re
from time import sleep
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .prompt import SYSTEM_PROMPT
from .redact import redact

DEFAULT_MODEL = "claude-sonnet-5-5"
SEVERITIES = ("high", "medium", "low")


def parse_response(text):
    cleaned = re.sub(
        r"\A```(?:json)?[ \t]*\n?|\n?```\Z", "", text.strip(), flags=re.IGNORECASE
    )
    try:
        data = json.loads(cleaned)
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        return {
            "summary": cleaned or "The model returned an empty response.",
            "findings": [],
        }
    findings = data.get("findings")
    return {
        "summary": data["summary"],
        "findings": [
            finding
            for finding in findings
            if isinstance(finding, dict)
            and finding.get("severity") in SEVERITIES
            and all(
                isinstance(finding.get(key), str)
                for key in ("file", "title", "explanation")
            )
            and type(finding.get("line")) in (int, str, type(None))
        ]
        if isinstance(findings, list)
        else [],
    }


def review(diff, model, api_key):
    request = Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(
            {
                "model": redact(model)[0],
                "max_tokens": 2000,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": redact(diff)[0]}],
            }
        ).encode(),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    for attempt in range(4):
        try:
            with urlopen(request, timeout=60) as response:
                data = json.load(response)
            return parse_response(
                "\n".join(
                    block["text"]
                    for block in data["content"]
                    if block.get("type") == "text"
                )
            )
        except HTTPError as error:
            error.close()
            if attempt == 3 or not (error.code == 429 or 500 <= error.code <= 599):
                raise ValueError(f"Anthropic API returned HTTP {error.code}") from None
            sleep(2**attempt)
