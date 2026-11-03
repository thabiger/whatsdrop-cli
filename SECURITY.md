# Security policy

## Supported versions

Security fixes are provided for the latest `0.2.x` release. Pre-release or
unreleased development snapshots are not supported.

## Reporting a vulnerability

Please do **not** open a public issue for a security vulnerability.

Use GitHub's private vulnerability reporting:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability**.
3. Include the affected version, platform, a description, and reproduction
   steps.

If private reporting is unavailable, contact the maintainer at
`tomasz.habiger@gmail.com` with the subject line `whatsdrop-cli security`.

Please do not include real chat content, archive keys, Dropbox tokens, or other
personal data in a report. Use synthetic examples where possible.

## Scope

Security-relevant areas include:

- Dropbox OAuth and refresh-token handling.
- Credential storage permissions and revocation.
- Archive key environment-file permissions.
- Archive format detection, decryption and media path validation.
- Output and media export permissions.
- Terminal control-character handling and CSV formula injection.
- Dependency and release-pipeline integrity.

## Disclosure

I will acknowledge reports as soon as practical, investigate, and coordinate a
fix and disclosure timeline with you. Please allow reasonable time for a fix
before public disclosure.
