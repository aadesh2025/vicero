# Architecture map

Describes the repository as it is, measured from the code. Where a document disagrees, note the drift.

## Layout

| Path | Kind (app / library / infra / docs / tests / generated / deploy) | Purpose | Ships independently? |
|---|---|---|---|
| | | | |

## Domains

| Domain | Entry points | Logic | Data access | Jobs | Tests | Docs |
|---|---|---|---|---|---|---|
| | | | | | | |

## Layers and conventions in use

- Layers present (transport / application / persistence / adapters): `<...>`
- Data-access convention (and whether a document claims a different one): `<...>`
- Error model, configuration mechanism, logging: `<...>`
- Where tenant/user context comes from (if applicable): `<...>`

## Integration boundaries

| External system | Abstraction | Implementations | Callers use the abstraction? |
|---|---|---|---|
| | | | |

## Dependency measurements

- Method used (tool or script): `<...>`
- File-level cycles: `<none | list>`
- Package-level cycles: `<none | list, with the shared primitive that causes each>`
- Shared/core code importing feature code: `<none | list>`
- Cross-package imports of private names: `<count, examples>`
- Feature-to-feature imports that bypass an interface: `<list>`
- Most-imported files: `<list>`

## Boundary rules

| Rule (stated by the project, or inferred and marked so) | Enforced by | Holds? |
|---|---|---|
| | test / lint / convention only | |

## Size and responsibility hot spots

| File / function | Size | Coherent? | Action (none / record / propose) |
|---|---|---|---|
| | | | |

## Documentation drift

| Document | Claim | State (current / historical / planned / deferred / unknown) | Reality | Fix |
|---|---|---|---|---|
| | | | | |

Only correct a row classified **current**. A **historical** row describing an older architecture is not drift
to fix — see `references/state-and-sources.md`.
