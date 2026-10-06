# Architecture and dependency-boundary verification

Goal: learn the architecture the repository actually has, check that its boundaries hold, and avoid
imposing a different one. Understand the existing architecture before changing it. Prefer an existing
abstraction to a new one, and a local change to a repository-wide one.

## 1. Discover, do not assume

Read the architecture documents and decision records, then check them against the code. Where the two
disagree, prefer the code **for what it actually does** — but check `references/state-and-sources.md` before
deciding what to do about the disagreement: a document with no historical framing that misdescribes current
structure is drift to correct; a dated or explicitly historical document describing a past architecture is not
drift, it is a record, and must not be rewritten to match today; and a documented, explicit architectural
decision (an ADR that says "we chose X and know Y looks like an alternative") controls over a naive reading of
the code, because the code alone cannot tell you whether a shape is an oversight or a decision.

Record (use `templates/architecture-map.md`):

- top-level layout and what each part is for (application, library, infrastructure, docs, tests, generated);
- domains or feature areas, and where each one's entry points, logic, data access, jobs and tests live;
- layers in use, if any (transport, application/service, persistence, provider adapters);
- shared or "core" areas, and whether they contain business logic they should not;
- integration boundaries: databases, queues, third-party APIs, provider abstractions, plugin mechanisms;
- the frontend/backend boundary and how the client reaches the server (generated client, one API module,
  scattered calls);
- independently shipped surfaces (embeddable widgets, SDKs, CLIs) that must not depend on the rest;
- generated code and where its source of truth is.

## 2. Measure dependencies

Prefer measurement to reading. In the project's own language, statically parse imports (a short throwaway
script or existing tooling) and compute:

- edges between packages/modules, and between layers;
- cycles at file level and at package level;
- shared areas that import feature code (upward dependencies);
- imports of another package's private or underscore-prefixed names;
- feature-to-feature imports that bypass a public interface;
- files that everything imports (candidates for a mislocated shared concept).

Separate top-level imports from function-level (lazy) imports and from type-only imports; a lazy import is
often how a cycle is being hidden. Report counts and examples. Do not treat lazy imports as violations by
themselves.

A package-level cycle with no file-level cycle usually means one shared primitive lives in the wrong package.
Name the primitive.

## 3. Judge, do not rewrite

For each finding decide which of these it is, and say so:

- a real violation of a rule the project states;
- an accepted, documented exception;
- debt worth recording;
- not a problem.

Fix a violation only when it is small, behavior-preserving and covered by tests. Otherwise list it under
Optional follow-up. Renaming, moving or splitting many files to make the structure look tidier is out of
scope.

## 4. Size and responsibility

Use file and function size as a prompt to look, not a rule. A large file that is one coherent thing (schema,
table of constants, state machine) is fine. A moderate file that mixes transport, persistence and provider
calls is not. Never split code only to meet a number, and never create wrappers that add names but no
meaning.

## 5. Routers, handlers and services

Check that entry points parse input, authenticate and authorize, call the application layer, and return.
Flag substantial business logic, query construction or provider orchestration living in handlers. Flag
services that coordinate unrelated domains. Repositories, where they exist, should own persistence and not
contain transport or provider concerns. If the project has no repository layer, do not add one; check that
its actual data-access convention is applied consistently, and that any document describing a layer that does
not exist is corrected.

## 6. Provider and adapter abstractions

Where the project isolates external systems behind an interface, verify callers use the interface, and that
provider-specific types do not leak into domain code. Do not add a second abstraction for the same concept.
Adding an implementation should be a local change; if it is not, note why.

## 7. Lightweight enforcement

If boundary rules exist in documentation but nothing enforces them, propose a small automated check and add
it only if the task includes it. Good properties:

- static, fast, no new heavy dependency;
- each rule states something currently true, so it can pass on day one;
- known exceptions are listed with a reason; an exception list can only shrink (a *ratchet*: new violations
  fail; entries that are no longer violations must be removed);
- proven to fail: introduce a temporary violation in an isolated worktree and confirm the rule catches it.

Typical rules: no file-level cycles; shared code does not import feature code; low-level layers do not import
high-level ones; no cross-package imports of private names; entry points do not build queries; independent
surfaces import nothing from the app; client code does not import server-only or route files.

Do not build an architecture-testing framework.

## 8. Frontend

Check that route or page composition, feature components, shared UI, API access and state each have an
obvious home; that API calls go through the established client; that there is no duplicated data fetching for
the same resource; and that state is global only when it is genuinely global. Do not rewrite working UI to
change its structure.

## 9. Things not to introduce

Repository-per-module, interface-per-class or -function, service-per-class, factory-per-provider,
microservices, event buses, dependency-injection containers or frameworks, CQRS, or clean/hexagonal/DDD
restructurings. Introduce one only if (1) the project already uses it, (2) the user explicitly asked, or (3) it
is required to solve a demonstrated concrete problem, which you can state and evidence. An abstraction needs a
present reason: multiple implementations, an external system to isolate, a needed test substitute, or a
boundary being violated now.
