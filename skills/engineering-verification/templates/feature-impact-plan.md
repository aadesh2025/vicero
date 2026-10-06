# Feature impact plan and report

Two parts. Write the plan before editing; write the report at the end and compare the two.

---

# FEATURE IMPACT PLAN

Feature:
Acceptance criteria:
Domain owner:
Existing abstraction:
Existing implementation to reuse:
Search performed (terms, places searched, what was found):
Why a new abstraction is needed (only if one is proposed):

Expected files (what should change):
Protected files (what should NOT change):

API impact:
Database impact (tables, migrations, indexes, ownership, deletion, compatibility):
Security impact:
Authorization:
Tenant impact (or "not multi-tenant"):
Integration impact:
Background jobs:
Frontend:
Configuration / environment impact:

Tests:
Documentation:
Potential ripple effects:

Change budget (files / domains) and the point at which I stop and re-plan:

---

# FEATURE IMPACT REPORT

## Feature

## Domain Owner

## Existing Abstractions

Reused: `<what>`. Created: `<what, and why reuse was not possible>`.

## Expected Files

| File | In the plan? | Why it changed |
|---|---|---|
| | yes / no | |

Any file not in the plan needs an explanation here.

## Protected Files

`<files/areas the plan said would not change>`; confirmed unchanged: `<yes / exceptions and why>`.

## API Impact

## Database Impact

## Security Impact

## Authorization

## Tenant Impact

## Integration Impact

## Background Jobs

## Frontend

## Tests

| Test | Covers | Result |
|---|---|---|
| | | |

Checks run: `<commands and outcomes>`. Not run: `<... and why>`.

## Documentation

## Risks

`<each risk; cause proven / inferred / unknown>`

## Final Scope

What changed, what deliberately did not, and what was noticed but left for follow-up — each classified
REQUIRED FOR REQUEST / REQUIRED FOR CORRECTNESS / REQUIRED FOR SECURITY (changed) or OPTIONAL FOLLOW-UP /
UNRELATED (not changed, listed instead) per `SKILL.md`, "Change-scope guard". Confirm the diff was reviewed in
full against the plan, and that negative verification (`references/git-discipline.md` §8) was done.
