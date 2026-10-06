# Feature safety methodology (Mode 3)

Use before implementing any non-trivial feature or behavior change: one that adds an entry point, stored data, an
outbound call, a permission or a dependency, or touches more than one domain. Skip the written plan for a
change whose location and effect are obvious and local; do not skip the search for existing code.

## 1. Order of work

1. Restate the request and the acceptance criteria.
2. Investigate the affected part of the system (`investigation.md`, scoped to the feature): its boundaries,
   data, callers, tests and documentation.
3. Find the owning domain (where this concern already lives).
4. Search for existing abstractions and implementations to reuse.
5. Write the impact plan below.
6. Analyze security, authorization and tenant impact.
7. Implement in small steps, running the narrowest relevant check after each.
8. Test the behavior, the failure cases and the security boundary.
9. Run the broader checks (`verification.md` section 2, scoped to the change); review the full diff against the
   plan.
10. Write the feature report.

Do not create files first. Search first.

## 2. Find the owning domain

Follow the request to where the same kind of thing is already done: the closest existing feature, the module
whose data or vocabulary it uses, the tests of similar behavior, the documentation that describes it. Use the
repository's existing names; do not introduce synonyms for existing concepts. If two places could own it,
choose the one that already owns the data, and say why.

## 3. Search for existing abstractions

Before writing a service, manager, helper, utility, provider, client, repository, adapter, hook, context,
middleware, validator or configuration mechanism, search for:

- the same concept under other names (grep for verbs and nouns the feature involves, not only your planned
  name);
- the same behavior (who already calls this API, parses this format, checks this permission, reads this
  setting, retries this call);
- the interface a new implementation should satisfy (existing provider or adapter contracts);
- sibling implementations, to copy their structure and tests;
- the shared or core area, and the documentation of it.

Record what you searched. If you reuse something, name it. If you cannot, give one sentence why. An
adapter for an external system belongs in that system's existing adapter layer; a new implementation of an
existing contract should not require changes outside the contract's own area.

## 4. Impact plan

Fill `templates/feature-impact-plan.md` before editing. It must state **what should change and what should
not**. It names:

- the domain owner, the existing abstraction and the existing implementation to reuse;
- expected files to change, and protected files that must **not** change (be specific: the neighboring domains a
  careless implementation would be tempted to touch);
- API, database (tables, migrations, indexes, ownership, deletion), security, authorization, tenant,
  integration, background-job and frontend impact;
- tests required (unit, integration, security boundary, regression) and documentation to update;
- ripple effects: who else calls what you change; shared types; configuration and environment variables;
  deployment.

Migrations: never edit historical migrations; add new ones; check for compatibility with existing data.
New configuration: document it where the project documents configuration and give it a safe default.

## 5. Change locality

`feature -> owning domain -> existing interface -> minimal dependencies.`

Examples of good locality: a new channel or provider touches that channel's or provider's area and its tests
and docs, and perhaps one configuration entry; a new dashboard view touches its feature area and reuses the
shared client and components; a new field touches the model, its migration, the one validation point and the
tests.

Signals to stop: the plan said 4 files and the diff has 15; an unrelated domain is being edited to make a
type fit; a shared module is being modified by a feature that only one domain uses; you are working around a
boundary rather than through it.

When you stop, write the dependency chain that pulled each extra file in, then decide with the user among:
reuse an existing abstraction; a boundary is misplaced (propose a separate change to fix it); the feature
genuinely crosses domains (widen the plan explicitly); the edit is accidental (revert it).

## 6. Security, access and tenancy for the new surface

For every new entry point, input, outbound request, file, job or stored value, decide: who may call it and how
that is enforced; what identifies the owner and how ownership is checked; what an unauthorized caller sees;
what is validated; what could be abused for server-side requests, injection or resource exhaustion; what is
logged and whether it may contain secrets. If the product is multi-tenant, derive tenant context from the
authenticated session or a trusted relation, never from a client-supplied value, and add a cross-tenant test.
Method: `security.md`.

## 7. Implementation discipline

- Follow the style of the surrounding code: naming, comments, structure.
- Add the minimum abstraction the feature needs.
- Keep the feature separate from refactors, upgrades and migrations of frameworks.
- Update documentation only where its truth changed. Record a decision when a boundary or abstraction changes.
- Do not fix unrelated problems you notice; classify them (`SKILL.md`, "Change-scope guard") and list anything
  short of REQUIRED FOR REQUEST/CORRECTNESS/SECURITY under follow-up instead of implementing it.

## 8. Feature report

Use the report section of `templates/feature-impact-plan.md`. Compare the final change set with the plan: list any
file that changed and was not in the plan and explain it. State remaining risks, including anything not
verified.
