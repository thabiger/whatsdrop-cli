# Contributing

## Development setup

```sh
git clone https://github.com/thabiger/whatsdrop-cli.git
cd whatsdrop-cli
uv sync --extra mcp
uv run pytest -q
```

The test suite uses a local fake Dropbox server and encrypted fixtures. It does
not need a Dropbox account, network access, or real chat data.

## Before opening a pull request

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv build
uvx twine check dist/*
```

Keep changes focused, add tests for behavior changes, and update the README or
CHANGELOG when user-facing behavior changes.

## Test data

Never commit real chat content, contact names, phone numbers, archive keys,
Dropbox tokens, or exported media. Use synthetic fixtures and reserved example
values such as `+1 555 0100`.

## Security

Do not report security vulnerabilities in a public issue. See
[SECURITY.md](SECURITY.md).

## Commit style

Use short imperative commit subjects, for example:

```text
Add CSV formula-injection protection
Fix credential permissions
```

## License

By contributing, you agree that your contribution is licensed under the
Apache-2.0 license. See [LICENSE](LICENSE).
