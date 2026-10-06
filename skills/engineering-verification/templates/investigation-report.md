# ENGINEERING INVESTIGATION REPORT

Read-only analysis. Label every statement **FACT** (observed; cite the source), **INFERENCE** (concluded; say
what it rests on) or **OPEN QUESTION** (undetermined; say what would settle it). Never present an inference as
fact. No scores, grades or praise.

## Repository

- Location, branch, commit, size and shape (single project / monorepo):
- State at start (modified, untracked, concurrent activity observed):
- Instruction and documentation files read:

| Statement | Type (FACT / INFERENCE / OPEN QUESTION) | Source or basis |
|---|---|---|
| | | |

## Technology Stack

| Area | Finding | Source | Type |
|---|---|---|---|
| Languages and versions | | | |
| Frameworks | | | |
| Package managers / workspaces | | | |
| Datastores and migration tools | | | |
| Queues, caches, schedulers | | | |
| Deployment and infrastructure | | | |

## Architecture

Summary of the architecture as it is, and where documents disagree with the code. Attach or link the filled
`architecture-map.md`.

## Domain Map

| Domain | Entry points | Owns (data / logic) | Depends on | Tests | Docs |
|---|---|---|---|---|---|
| | | | | | |

## Dependency Boundaries

- Method used (tool or script):
- Layering and direction:
- Cycles (file level, package level), with the shared primitive behind each:
- Shared or core code importing feature code:
- Cross-boundary use of private helpers; feature-to-feature imports bypassing an interface:
- Duplicate abstractions:

## Security Boundaries

Only surfaces that exist. For each: who can reach it, authentication, authorization, what it can touch.

| Surface | Reach | Authn / authz | Notes | Type |
|---|---|---|---|---|
| | | | | |

Surfaces considered and skipped (with reason):

## Data Boundaries

Where data lives, who owns it, how tenant or user scoping is applied (convention, query layer, database
policy), what crosses boundaries, retention and deletion.

## External Integrations

| System | Direction (in / out) | Abstraction | Configured by (operator / tenant / user) | Guarded? |
|---|---|---|---|---|
| | | | | |

## Testing Infrastructure

| Layer | Command (source) | Location | Touches (datastores, services, network) | Run here? |
|---|---|---|---|---|
| | | | | |

## Build Infrastructure

| Deliverable | Command (source) | Output | Rewrites tracked files? | Run here? |
|---|---|---|---|---|
| | | | | |

## Documentation

Documents present, their apparent freshness, and factual contradictions with the code.

| Document | Claim | State (current / historical / planned / deferred / unknown) | Contradiction? |
|---|---|---|---|
| | | | |

## CI/CD Consistency

Default/primary branch, each workflow's triggers, deployment conditions (automatic / manual dispatch / tag-
gated), and the documented release process — only if CI/release configuration is in scope.

| Item | Repository policy | Workflow/config says | Contradiction? |
|---|---|---|---|
| | | | |

## Technical Debt

### Required

| Item | Location | Evidence | Effort |
|---|---|---|---|
| | | | |

### Optional

| Item | Location | Evidence | Effort |
|---|---|---|---|
| | | | |

Blocking items (prevent a reliable conclusion or further safe work):

## Risks

Each risk with the reasoning for likelihood and impact.

## Open Questions

| Question | What would settle it | Needs a user decision? |
|---|---|---|
| | | |

## Not Run / Not Verified

Checks or claims deliberately not run or not independently verified, and why (includes any document claim of
"complete"/"verified"/"passing" taken as historical evidence only — `state-and-sources.md`).

| Item | Why not run/verified | Evidence level reached (if any) |
|---|---|---|
| | | |

## Recommended Next Steps

Ordered. Mark which need a user decision and which would change code (this report changed none).
