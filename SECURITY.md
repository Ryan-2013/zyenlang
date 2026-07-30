# Security Policy

## Supported versions

Security fixes are provided for the latest `0.2.x` release. Older preview
releases are not maintained and should be upgraded before reporting a bug.

## Reporting a vulnerability

Please use GitHub's **Report a vulnerability** form in the repository Security
tab. Do not open a public issue for a suspected vulnerability.

Include the affected version, operating system, minimal reproduction steps,
impact, and any suggested mitigation. Avoid including secrets or personal data.
You should receive an acknowledgement within seven days. A fix and disclosure
timeline will be coordinated after the report is reproduced.

## Trust boundaries

- ZyenLang programs and native modules execute with the permissions of the
  current user. Only compile and run source code and C modules that you trust.
- `c_module` compatibility sources are native code; the compiler does not
  sandbox them.
- The VS Code extension does not run the compiler in an untrusted workspace.
- Release archives include SHA-256 checksums and GitHub build provenance.
- The Windows MSI is currently not Authenticode-signed. Verify its checksum and
  provenance from the matching GitHub release before installation.
