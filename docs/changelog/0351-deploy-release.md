# 0351 — PRD-005 slice 9: deploy-smoke tenancy flow + secrets-gated release pipeline

- **Commit:** pending
- **Date:** 2026-08-24
- **Author:** Claude (Sonnet subagent) / Nikita Fedorov

## Summary
FR7/FR14/FR15: the deployed-instance smoke now proves the tenancy story
(AC2/AC5 deployed halves + a real token-authenticated clone/push);
`release.yml` builds macOS/Windows desktop artifacts with signing and
notarization that run iff secrets are provisioned; deployment.md gains
the full PRD-005 operator story; the PRD's FR13/FR15+AC8 lines carry
their recorded amendments.

## Changes
- deploy-smoke.yml (+195): second user, org/workspace bootstrap (compose
  exec python one-liner — no CLI exists yet, stated honestly), 403→grant
  →200 role flip, scoped token edits A / 403 on B / 401 after revoke,
  real `git clone`+push against `/git/acme/main/widget.git`
  (extraHeader flagged as a CI-only shortcut), audit query asserting
  distinct principals with the honest caveat that the general
  `tap_registry` is not yet wired into serve (slice 10).
- release.yml (new): PyInstaller onedir per OS; the bench.yml
  guard-job secrets pattern (booleans into job env — never `secrets.*`
  in `if:`); macOS loop-signs dylibs bottom-up then the entry with
  hardened runtime + entitlements (`packaging/entitlements.plist`,
  plutil-linted), `notarytool --wait`, best-effort staple (Apple only
  guarantees stapling for app/pkg/dmg); Windows signtool path;
  unsigned-with-notice when secrets absent. The Windows job reproduces
  `build_binary.sh`'s three commands with `Scripts/` paths (the script
  hardcodes POSIX `bin/` — out of slice scope, noted for follow-up).
- deployment.md (+393/−13): SSO/passkeys (`[cloud]` extra, oidc.json
  shape), orgs/roles (ladder, name-free 404, "RBAC is not a filesystem
  boundary"), scoped tokens, audit (query/retention/VACUUM INTO/WAL cp
  warning), sync (credential helper, push cap, exact hook refusals),
  desktop builds (secrets table), "Still deferred" rewritten to what
  actually remains (SAML/SCIM, billing, multi-process bus).
- PRD: FR13 → trailer+UI wording; FR15/AC8 → "(pipeline ships
  secrets-gated; positive signing evidence requires provisioned
  certificates)".

## Files
- `.github/workflows/deploy-smoke.yml`, `docs/deployment.md`,
  `docs/prd/in-progress/PRD-005-multi-tenant-cloud.md` — extended
- `.github/workflows/release.yml`, `packaging/entitlements.plist` — new

## Notes
Workflows desk-checked (`bash -n`, YAML parse, plutil); the keychain/
signtool paths are genuinely unverifiable without provisioned certs and
a live runner — stated in the workflow comments.
`make test` — 7002 passed, 51 skipped (12:41); non-passing were the documented families only: the pre-existing prd028 AC6 local solver timeout (skips on CI) and supervisor/share_isolation load flakes — 30/30 green in isolation.
