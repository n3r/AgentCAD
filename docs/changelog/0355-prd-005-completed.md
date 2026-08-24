# 0355 — PRD-005 completed: move PRD to completed/, mark roadmap DONE

- **Commit:** pending
- **Date:** 2026-08-24
- **Author:** Claude (orchestrator) / Nikita Fedorov

## Summary
PRD-005 (multi-tenant cloud) merged into main via PR #35: orgs → workspaces
→ per-project roles, OIDC + WebAuthn passkeys, scoped agent tokens, git
push/pull/clone sync, per-tenant fair scheduling, an append-only audit log,
identity UI, and a secrets-gated signed-build pipeline. Local mode
byte-for-byte unchanged. This commit is the docs-only close-out.

## Changes
- `docs/prd/in-progress/PRD-005-multi-tenant-cloud.md` → `completed/`,
  header status → completed.
- `docs/roadmap.md`: PRD-005 row → DONE (PR #35), link to `completed/`; the
  v4 section note that "cloud with local-first sync" is now shipped.

## Files
- `docs/prd/completed/PRD-005-multi-tenant-cloud.md` — moved, status line
- `docs/roadmap.md` — status + link

## Notes
Security-critical feature: a four-way review (2 Opus lenses, adversarial
verifier, Codex xhigh) plus two adversarial re-check rounds found and closed
a proven git-sync RCE (`.history/**` push → unconfined hook execution) and a
chat-outside-tenancy isolation hole before merge — the review record and the
0353 review-fix changelog carry the detail. Deferred, recorded: SAML/SCIM,
billing, a multi-process event bus, per-account budgets, and AC8's positive
signing evidence (needs provisioned certificates). Evidence: PR #35 CI green.
