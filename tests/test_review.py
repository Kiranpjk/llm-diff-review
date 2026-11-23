import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from llm_diff_review import cli, github, llm
from llm_diff_review.diff import TRUNCATED, chunk_diff, parse_diff
from llm_diff_review.redact import redact


def file_diff(path="src/cart.py", code="return 1"):
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1 +1 @@\n-old\n+{code}\n"
    )


@pytest.mark.parametrize(
    "path",
    [
        "a/package-lock.json",
        "poetry.lock",
        "pnpm-lock.yaml",
        "build/sub/a.py",
        "nested/dist/a.py",
        "vendor/lib.py",
        "a.min.js",
        "img/photo.jpg",
        "icon.svg",
        "go.sum",
        "bun.lockb",
    ],
)
def test_default_ignores(path):
    assert chunk_diff(file_diff(path)) == ([], 0, [(path, "ignored")])


def test_user_globs():
    diff = file_diff("src/generated/a.py") + file_diff("src/cart.py")
    assert chunk_diff(diff, ignore=["src/generated/*"]) == (
        [file_diff("src/cart.py")],
        1,
        [("src/generated/a.py", "ignored")],
    )


@pytest.mark.parametrize(
    "header", ["Binary files a/data and b/data differ", "GIT binary patch"]
)
def test_binary(header):
    assert chunk_diff(f"diff --git a/data b/data\n{header}\n") == (
        [],
        0,
        [("data", "binary")],
    )


def test_plain_diff_and_deletion():
    diff = "--- a/x\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"
    assert list(parse_diff(diff)) == [(["x"], diff)]


def test_spaces_in_paths():
    assert next(parse_diff(file_diff("a file.py")))[0] == ["a file.py"] * 2


def test_chunk_boundary():
    first, second = file_diff("a"), file_diff("b")
    assert chunk_diff(first + second, len(first))[0] == [first, second]
    assert chunk_diff(first + second, len(first + second))[0] == [first + second]


def test_truncation():
    chunks, reviewed, skipped = chunk_diff(file_diff(code="x" * 1000), 120)
    assert len(chunks[0]) == 120
    assert chunks[0].endswith(TRUNCATED)
    assert reviewed == 1
    assert skipped == []


def test_empty_diff():
    assert chunk_diff("") == ([], 0, [])


def test_budget_too_small():
    with pytest.raises(ValueError, match="at least"):
        chunk_diff("", 1)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("AKIA" + "A" * 16, "[REDACTED]"),
        ("ghp_" + "a" * 36, "[REDACTED]"),
        ("gho_" + "b" * 36, "[REDACTED]"),
        ("sk-" + "abc123456789", "[REDACTED]"),
        ("xoxb-" + "12345-abcde", "[REDACTED]"),
        (
            "+-----BEGIN RSA PRIVATE KEY-----\n+example\n+-----END RSA PRIVATE KEY-----",
            "+[REDACTED]\n\n",
        ),
        (
            "-----BEGIN PGP PRIVATE KEY BLOCK-----\nexample\n-----END PGP PRIVATE KEY BLOCK-----",
            "[REDACTED]\n\n",
        ),
        ('password = "hunter2"', "password = [REDACTED]"),
        ("secret='example'", "secret=[REDACTED]"),
        ('"token": "example"', '"token": [REDACTED]'),
        ('api_key = "example"', "api_key = [REDACTED]"),
    ],
)
def test_redaction(source, expected):
    assert redact(source) == (expected, 1)


def test_normal_code():
    source = "def count_tokens(token):\n    return token.count('x')\npassword = get_password()"
    assert redact(source) == (source, 0)


def test_redaction_count_and_idempotence():
    source = 'password = "ghp_abcdefghijklmnop"\nsecret = "example"'
    clean, count = redact(source)
    assert count == 2
    assert redact(clean) == (clean, 0)


@pytest.mark.parametrize("opening", ["", "```\n", "```JSON\n"])
def test_response_parsing(opening):
    text = '{"summary":"No issues","findings":[]}'
    assert llm.parse_response(opening + text + ("\n```" if opening else "")) == {
        "summary": "No issues",
        "findings": [],
    }


@pytest.mark.parametrize("value", ["plain text", "[1, 2]", '{"summary":false}'])
def test_invalid_response(value):
    assert llm.parse_response(value) == {"summary": value, "findings": []}


def test_invalid_findings_are_dropped():
    assert llm.parse_response(
        '{"summary":"Found something","findings":[null, {}]}'
    ) == {"summary": "Found something", "findings": []}


def response(value):
    return BytesIO(json.dumps(value).encode())


def http_error(code):
    return HTTPError("https://example.invalid", code, "error", {}, None)


def test_anthropic_request_and_retry():
    payload = {"content": [{"type": "text", "text": '{"summary":"No issues"}'}]}
    with (
        patch.object(
            llm,
            "urlopen",
            side_effect=[http_error(429), http_error(503), response(payload)],
        ) as send,
        patch.object(llm, "sleep") as pause,
    ):
        assert (
            llm.review('secret = "example"', "test-model", "test-key")["summary"]
            == "No issues"
        )
    request = send.call_args.args[0]
    assert request.full_url == "https://api.anthropic.com/v1/messages"
    assert request.get_method() == "POST"
    assert request.get_header("Anthropic-version") == "2023-06-01"
    assert request.get_header("X-api-key") == "test-key"
    assert send.call_args.kwargs == {"timeout": 60}
    body = json.loads(request.data)
    assert body["max_tokens"] == 4096
    assert body["model"] == "test-model"
    assert body["messages"][0]["content"] == 'secret = "example"'
    assert [call.args for call in pause.call_args_list] == [(1,), (2,)]


@pytest.mark.parametrize(("code", "calls"), [(500, 4), (401, 1)])
def test_retry_limits(code, calls):
    with (
        patch.object(llm, "urlopen", side_effect=http_error(code)) as send,
        patch.object(llm, "sleep") as pause,
        pytest.raises(ValueError, match=f"HTTP {code}"),
    ):
        llm.review("diff", "model", "test-key")
    assert send.call_count == calls
    assert pause.call_count == calls - 1


@pytest.fixture
def pr(monkeypatch, tmp_path):
    event = tmp_path / "event.json"
    event.write_text('{"number": 42, "pull_request": {}}')
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    return github.GitHub()


def test_fetch_diff(pr):
    with patch.object(github, "urlopen", return_value=BytesIO(b"diff")) as send:
        assert pr.fetch_diff() == "diff"
    request = send.call_args.args[0]
    assert request.full_url.endswith("/repos/owner/repo/pulls/42")
    assert request.get_header("Accept") == "application/vnd.github.diff"
    assert request.get_header("Authorization") == "Bearer test-token"


@pytest.mark.parametrize(
    ("author", "method", "suffix"),
    [
        ("someone", "POST", "/issues/42/comments"),
        ("github-actions[bot]", "PATCH", "/issues/comments/7"),
    ],
)
def test_comment_upsert(pr, author, method, suffix):
    comments = [{"id": 7, "user": {"login": author}, "body": github.MARKER + "\nold"}]
    with patch.object(
        github, "urlopen", side_effect=[response(comments), response({})]
    ) as send:
        pr.upsert_comment('token = "example"')
    assert send.call_count == 2
    request = send.call_args.args[0]
    assert request.full_url.endswith(suffix)
    assert request.get_method() == method
    assert json.loads(request.data)["body"] == github.MARKER + "\ntoken = [REDACTED]"


def test_comment_pagination(pr):
    existing = {
        "id": 9,
        "user": {"login": "github-actions[bot]"},
        "body": github.MARKER + "\nold",
    }
    with patch.object(
        github,
        "urlopen",
        side_effect=[response([{}] * 100), response([existing]), response({})],
    ) as send:
        pr.upsert_comment("new")
    assert "page=2" in send.call_args_list[1].args[0].full_url
    assert send.call_args.args[0].get_method() == "PATCH"


@pytest.mark.parametrize(
    ("severity", "flag", "expected"),
    [
        ("high", False, 0),
        ("high", True, 1),
        ("medium", True, 0),
        ("low", True, 0),
    ],
)
def test_offline_and_fail_on(severity, flag, expected, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_DIFF_REVIEW_FAIL_ON", raising=False)
    local_diff = tmp_path / "diff"
    local_diff.write_text(file_diff(code='secret = "example"'))
    mock = tmp_path / "mock.json"
    mock.write_text(
        json.dumps(
            {
                "summary": "Test summary",
                "findings": [
                    {
                        "file": "src/cart.py",
                        "line": 1,
                        "severity": severity,
                        "title": "Finding",
                        "explanation": "Example",
                    }
                ],
            }
        )
    )
    args = [
        "--dry-run",
        "--diff-file",
        str(local_diff),
        "--mock-response-file",
        str(mock),
    ]
    with patch.object(cli, "GitHub") as gh, patch.object(cli, "review") as review:
        assert cli.main(args + (["--fail-on", "high"] if flag else [])) == expected
    gh.assert_not_called()
    review.assert_not_called()
    out = capsys.readouterr().out
    assert "Test summary" in out
    assert f"| {severity} |" in out
    assert "Secrets redacted: 1" in out


def test_missing_key(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit) as error:
        cli.main(["--dry-run", "--diff-file", "unused"])
    assert error.value.code == 2
    assert "ANTHROPIC_API_KEY is required" in capsys.readouterr().err


def test_dry_run_requires_local_diff(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with patch.object(cli, "GitHub") as gh, pytest.raises(SystemExit) as error:
        cli.main(["--dry-run"])
    assert error.value.code == 2
    gh.assert_not_called()


def test_comment_finding_order_and_escaping():
    base = {
        "file": "file|name",
        "line": 1,
        "title": "<tag>",
        "explanation": "line\nbreak",
    }
    findings = [dict(base, severity=severity) for severity in ("low", "high", "medium")]
    body, _ = cli.render(
        [{"summary": "Summary", "findings": findings}], "model", 1, [], 0
    )
    assert body.index("| high |") < body.index("| medium |") < body.index("| low |")
    assert r"file\|name" in body
    assert "&lt;tag&gt;" in body
    assert "line break" in body


def test_interleaved_private_keys_in_diff():
    source = (
        "-\x2d----BEGIN PRIVATE KEY-----\n"
        "+-----BEGIN PRIVATE KEY-----\n"
        "-old key data\n"
        "-\x2d----END PRIVATE KEY-----\n"
        "+new key data\n"
        "+-----END PRIVATE KEY-----\n"
    )
    clean, count = redact(source + file_diff())
    assert "key data" not in clean
    assert count == 2
    assert clean.endswith(file_diff())


def test_redacts_prefixed_unquoted_and_url_secrets():
    from llm_diff_review.redact import redact

    for line in (
        'DB_PASSWORD="hunter2"',
        "password: hunter2",
        "API_KEY=abc123def456",
        "postgres://admin:s3cret@db/x",
    ):
        text, count = redact(line)
        assert (
            count == 1
            and "hunter2" not in text
            and "abc123" not in text
            and "s3cret" not in text
        )


def test_env_files_are_never_sent():
    from llm_diff_review.diff import chunk_diff

    diff = "diff --git a/.env b/.env\n--- /dev/null\n+++ b/.env\n@@ -0,0 +1 @@\n+X=1\n"
    chunks, _, skipped = chunk_diff(diff)
    assert chunks == [] and skipped == [(".env", "ignored")]


def test_cut_off_model_reply_is_an_error():
    import io
    import json
    from unittest import mock

    import pytest

    from llm_diff_review.llm import review

    body = json.dumps(
        {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "{"}]}
    )
    with (
        mock.patch(
            "llm_diff_review.llm.urlopen", return_value=io.BytesIO(body.encode())
        ),
        pytest.raises(ValueError, match="cut off"),
    ):
        review("diff", "m", "k")
