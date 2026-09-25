# Security Policy

## Report Privately

Report suspected vulnerabilities through the repository's
[private vulnerability report form](https://github.com/research-engineering/ci-coordinator/security/advisories/new).
Do not publish secrets, sensitive deployment data, or exploit details in public
issues, discussions, or pull requests. Redact credentials and personal data
from evidence, including private reports.

Include:

- the exact affected version, source commit, and container digest where
  applicable; identify any unknown coordinates explicitly;
- a minimal reproduction and its required configuration or preconditions;
- the observed behavior, expected behavior, and security impact;
- relevant redacted logs or other evidence.

See GitHub's guidance on
[privately reporting a security vulnerability](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately).

## Authorized Testing

Test only systems you own or have explicit permission to test. Access to this
repository does not authorize testing third-party or corporate deployments.
Use a controlled reproduction without exposing other users' data or credentials.
