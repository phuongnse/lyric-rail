# Security policy

LyricRail is developing an authenticated encrypted package and native Player.
The current beta must not be described as commercial DRM or as resistant to an
administrator controlling a playback device. The normative threat model, format,
key lifecycle, and release gates are documented in:

- `docs/THREAT_MODEL.md`
- `docs/LRAIL_FORMAT_V1.md`
- `docs/KEY_MANAGEMENT.md`
- `docs/SECURITY_ACCEPTANCE.md`

## Supported version

Security fixes are applied to the latest revision on the default branch while LyricRail is in beta.

## Reporting a vulnerability

Do not open a public issue for a vulnerability or exposed credential. Use GitHub's private security-advisory reporting for the repository and include:

- the affected command or component;
- reproduction steps and platform details;
- the expected impact;
- any suggested mitigation.

If a credential was committed, revoke it at the provider immediately. Removing it from the latest Git revision is not sufficient because Git history may still contain the value.
