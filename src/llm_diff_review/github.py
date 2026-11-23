"""Fetch pull request diffs and upsert a summary comment."""

import json
import os
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .redact import redact

MARKER = "<!-- llm-diff-review -->"
API = "https://api.github.com"


class GitHub:
    def __init__(self):
        self.token = os.environ.get("GITHUB_TOKEN")
        if not self.token:
            raise ValueError("GITHUB_TOKEN is required")
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            raise ValueError("GITHUB_REPOSITORY must identify the repository")
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        if not event_path:
            raise ValueError("GITHUB_EVENT_PATH is required")
        with open(event_path, encoding="utf-8") as event_file:
            event = json.load(event_file)
        number = event.get("number")
        if "pull_request" not in event or type(number) is not int or number < 1:
            raise ValueError("Expected a pull request event")
        self.repo_url = f"{API}/repos/{repo}"
        self.number = number

    def request(
        self, url, method="GET", accept="application/vnd.github+json", body=None
    ):
        request = Request(
            url,
            data=json.dumps({"body": redact(body)[0]}).encode()
            if body is not None
            else None,
            method=method,
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=60) as response:
                return response.read().decode("utf-8")
        except HTTPError as error:
            error.close()
            raise ValueError(f"GitHub API returned HTTP {error.code}") from None

    def fetch_diff(self):
        return self.request(
            f"{self.repo_url}/pulls/{self.number}", accept="application/vnd.github.diff"
        )

    def upsert_comment(self, body):
        if not body.startswith(MARKER + "\n"):
            body = MARKER + "\n" + body
        url = f"{self.repo_url}/issues/{self.number}/comments"
        page = 1
        while True:
            comments = json.loads(self.request(f"{url}?per_page=100&page={page}"))
            for comment in comments:
                if (comment.get("user") or {}).get("login") != "github-actions[bot]":
                    continue
                if (comment.get("body") or "").startswith(MARKER + "\n"):
                    self.request(
                        f"{self.repo_url}/issues/comments/{int(comment['id'])}",
                        method="PATCH",
                        body=body,
                    )
                    return
            if len(comments) < 100:
                break
            page += 1
        self.request(url, method="POST", body=body)
