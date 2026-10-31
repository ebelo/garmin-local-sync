# Security and private data

Security fixes target the latest code on `main` during this alpha phase. Include
the version or commit when reporting a problem.

## Report a vulnerability privately

Use the repository's **Security → Report a vulnerability** option when private
vulnerability reporting is enabled. If that option is unavailable, open an issue
asking the maintainer for a private reporting channel, without disclosing the
vulnerability itself. Do not publish an exploit or sensitive details in an issue.

Describe the affected version, impact and a minimal reproduction with synthetic
data. Never include a real API key, bearer token, account identifier, health record,
home-location track, original activity file or personal GeoPackage. If you discover
an exposed credential, revoke it rather than posting its value as evidence.

## Protect your export

The GeoPackage, raw responses and original downloads are private personal data even
when no credentials are present. Keep them out of Git, issues and public artifacts.
Back them up privately. Store the API key and bearer cache with owner-only
permissions; the CLI refuses permissive key files. See the
[credential setup instructions](docs/usage.md#set-up-your-personal-api-key).

Use normal issues for non-security bugs, with redacted logs and synthetic inputs.
