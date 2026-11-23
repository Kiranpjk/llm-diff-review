# llm-diff-review

A Python 3.10+ GitHub Action that sends a pull request diff to Anthropic for review and creates or updates one PR summary comment. The comment reports potential bugs, security issues, and missing tests. The package has zero third-party runtime dependencies.

## Quick start

Add an `ANTHROPIC_API_KEY` repository Actions secret, then save this as `.github/workflows/review.yml` in the repo you want reviewed. The workflow uses `pull_request_target` so the secret is available on PRs, and it never checks out or runs the PR's code; the action only reads the diff through the GitHub API.

```yaml
name: Review pull request
on:
  pull_request_target:
    types: [opened, synchronize, reopened]
permissions:
  contents: read
  pull-requests: write
concurrency:
  group: llm-diff-review-${{ github.event.pull_request.number }}
  cancel-in-progress: false
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: Kiranpjk/llm-diff-review@main
        with:
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

**Status:** the test suite mocks the Anthropic and GitHub HTTP calls. The request and response format follow the public Messages API docs, but I have not yet run it against the live API.

### Action inputs

| Input | Default | Description |
| --- | --- | --- |
| `anthropic-api-key` | Required | Anthropic API key from a GitHub secret. |
| `github-token` | `github.token` | GitHub Actions token with `pull-requests: write`. |
| `model` | `claude-sonnet-5-5` | Anthropic model ID. Set a model enabled for your API account. |
| `max-chars` | `60000` | Maximum diff characters per request. |
| `ignore` | Empty | Extra ignore globs, separated by commas or newlines. |
| `fail-on` | Empty | Use `high` to exit 1 after reporting a high-severity finding. |

### Offline example

```sh
python -m pip install .
python -m llm_diff_review --diff-file examples/sample.diff --mock-response-file examples/mock-response.json --dry-run
```

Dry runs require a local diff and make no GitHub calls. Supply a mock response for offline use. Without a mock, the CLI uses `ANTHROPIC_API_KEY`. The CLI also accepts `--model`, `--max-chars`, `--ignore`, and `--fail-on`. Corresponding environment variables are `LLM_DIFF_REVIEW_MODEL`, `LLM_DIFF_REVIEW_MAX_CHARS`, `LLM_DIFF_REVIEW_IGNORE`, and `LLM_DIFF_REVIEW_FAIL_ON`.

### Privacy and limits

The filtered diff is sent to Anthropic. Regex-based secret redaction is best effort. Only run this action on diffs you are allowed to send to Anthropic.

The action uses one summary comment by the GitHub Actions bot, with no inline line comments. It skips binary files, common lockfiles, minified JavaScript, images, and files in `dist/`, `build/`, and `vendor/`. Whole file sections are grouped within the character budget. A file exceeding the budget is truncated with a marker. Each request reviews its own chunk. AI review can be wrong; verify before acting.

### Tests

```sh
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest -q
```

## License

[MIT](LICENSE)
