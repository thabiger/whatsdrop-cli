# Releasing

## Prerequisites

- PyPI and TestPyPI accounts with 2FA enabled.
- A GitHub repository `thabiger/whatsdrop-cli`.
- Trusted publishing configured on PyPI:
  - Project: `whatsdrop-cli`
  - Owner: `thabiger`
  - Repository: `whatsdrop-cli`
  - Workflow: `release.yml`
  - Environment: `release`
- Trusted publishing configured on TestPyPI:
  - Project: `whatsdrop-cli`
  - Owner: `thabiger`
  - Repository: `whatsdrop-cli`
  - Workflow: `testpypi.yml`
  - Environment: `testpypi`

Add a **pending publisher** before the first release:

- PyPI: <https://pypi.org/manage/account/publishing/>
- TestPyPI: <https://test.pypi.org/manage/account/publishing/>

The GitHub environments already exist at
<https://github.com/thabiger/whatsdrop-cli/settings/environments>.

No long-lived PyPI API token is required. The workflows use OIDC trusted
publishing and produce PyPI attestations.

## Version bump

1. Update `version` in `pyproject.toml`.
2. Update `__version__` in `src/whatsdrop_cli/__init__.py`.
3. Move the `CHANGELOG.md` entries into a dated release section.
4. Regenerate the lockfile:

   ```sh
   uv lock
   ```

5. Run the full checks:

   ```sh
   uv run pytest -q
   uv run ruff check .
   uv run ruff format --check .
   uv build
   uvx twine check dist/*
   ```

6. Inspect the artifacts:

   ```sh
   tar -tzf dist/whatsdrop_cli-*.tar.gz
   python3 -m zipfile -l dist/whatsdrop_cli-*.whl
   ```

   Confirm `LICENSE`, `NOTICE`, `README.md` and `pyproject.toml` are present,
   and that `media/`, credentials, caches and `__pycache__` are absent.

## TestPyPI dry run

1. Push the release commit.
2. Run the `TestPyPI` workflow manually from GitHub Actions.
3. Install from TestPyPI in a clean environment and verify:

   ```sh
   python -m venv /tmp/whatsdrop-test
   /tmp/whatsdrop-test/bin/pip install --index-url https://test.pypi.org/simple/ \
     --extra-index-url https://pypi.org/simple/ whatsdrop-cli
   /tmp/whatsdrop-test/bin/whatsdrop --version
   /tmp/whatsdrop-test/bin/whatsdrop --help
   /tmp/whatsdrop-test/bin/whatsdrop auth --status   # expected exit 2
   ```

## PyPI release

1. Create and push the tag:

   ```sh
   git tag -a v0.1.0 -m "whatsdrop-cli 0.1.0"
   git push origin v0.1.0
   ```

2. The `Release` workflow builds the sdist/wheel, publishes to PyPI using
   trusted publishing, and creates a GitHub release with the artifacts.
3. Verify the PyPI page, README rendering, metadata, file list, and attestation
   links.
4. Install from PyPI in a clean environment:

   ```sh
   uv tool install whatsdrop-cli
   whatsdrop --version
   ```

## Post-release

- Confirm GitHub secret scanning and Dependabot have no open alerts.
- Confirm the companion extension build uses the same Dropbox app key as the
  CLI (`ydtsl7okrf4xuee`).
- If the app key changed, document how users can access archives created with an
  older key via `--dropbox-app-key`.
