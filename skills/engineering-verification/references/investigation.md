# Investigation methodology (Mode 1)

Goal: understand an unfamiliar or existing repository well enough to say what it is, how it is built, where
its boundaries are, and what is risky, without changing it. The output is a report
(`templates/investigation-report.md`).

## 0. How to record findings

Every finding is one of:

- **FACT**: observed directly (a file you read, a command's output, a measurement). Cite the source.
- **INFERENCE**: a conclusion drawn from facts. State what it rests on and how it could be wrong.
- **OPEN QUESTION**: something you could not determine. State what would settle it.

Never present an inference as a fact. Prefer measuring to reading: parse imports, list routes from the
framework's own route table, read the CI file rather than guessing what CI runs.

For any finding whose *timing* matters (a status, a count, a "done" marker, an architecture claim that might
be superseded), also classify its state — **CURRENT, HISTORICAL, PLANNED, DEFERRED or UNKNOWN**
(`state-and-sources.md`) — in addition to its FACT/INFERENCE/OPEN QUESTION label. The two axes are independent:
a historical claim can be an observed FACT (the document genuinely says that) while also being the wrong
evidence for a claim about today.

## 1. Repository

Survey without modifying anything:

- top-level directory structure and what each part appears to be for (application, library, infrastructure,
  docs, tests, generated, deployment);
- important files: entry points, configuration roots, build definitions;
- manifests and lockfiles per package or workspace (and whether it is a monorepo);
- configuration, including environment variable names and their documented defaults (never values that are
  secrets);
- CI and release configuration: what runs, in what order, on which triggers;
- documentation: README, architecture docs, decision records, runbooks, agent instruction files. Note their
  dates and whether they match the code.

## 2. Technology

Identify, from manifests and code: languages and versions; frameworks; package managers and workspace tools;
databases and migration tools; caches, queues and schedulers; external services and provider abstractions;
deployment model (containers, orchestration, serverless, static hosting); observability. Record the source of
each item. Do not assume a stack from a directory name.

## 3. Architecture

Fill `templates/architecture-map.md`. Determine, with evidence:

- application boundaries (what ships as a separate unit), domain boundaries, package boundaries;
- dependency direction between layers and between domains; cycles;
- data access conventions and where tenant or user context originates;
- integration boundaries and the abstractions in front of them;
- frontend/backend boundary and how the client reaches the server;
- infrastructure boundaries and independently shipped surfaces (SDKs, widgets, CLIs).

Method for dependency measurement: `architecture.md`.

## 4. Code organization

Look for and record, as facts with examples:

- ownership: which module owns which concept; where the same concept appears in several places;
- responsibility: files or functions mixing unrelated concerns; entry points containing business logic;
- shared and core modules: what they contain and whether they import feature code;
- duplicate abstractions: two clients, two permission checks, two config loaders for the same thing;
- coupling: most-imported files, feature-to-feature imports that bypass an interface;
- size: large files and long functions, judged for coherence, not by a fixed number;
- cross-domain imports and reuse of another package's private helpers.

## 5. Security boundaries

Identify only the surfaces that exist. For each, record who can reach it, how it is authenticated and
authorized, and what it can touch: authentication and sessions; authorization and roles; tenant or workspace
boundaries and where tenant context is derived; outbound requests built from configurable input; secrets
handling; privileged or administrative operations; file handling; command or process execution; webhooks and
callbacks; OAuth and third-party sign-in; background jobs and schedulers; internal APIs. Do not run checks for
categories the project does not have; say which you skipped and why. Deep method: `security.md`.

## 6. Test and build system

Find the commands and their sources (`testing.md` section 1). Record: test layers present and where their
tests live; fixtures and what they touch (datastores, services, network); how CI runs them; build outputs per
deliverable; whether builds or installs rewrite tracked files; what could not be run in this environment and
why. If you ran anything, record exact results and mark them as observations, not baselines, unless you ran
the full baseline procedure.

## 7. Technical debt

Classify each item:

- **REQUIRED**: the repository is incorrect, unsafe or unreliable without it (a verified bug, a security
  boundary hole, a documented rule that is violated).
- **OPTIONAL**: an improvement that is not needed for correctness (tidiness, consolidation, size).
- **BLOCKING**: prevents a reliable conclusion or further safe work until resolved (checks that cannot run,
  unknown architecture, missing access).

Give each a location, evidence and a rough effort. Do not fix anything in this mode.

## 8. Risks, open questions, next steps

Risks are stated with likelihood and impact reasoning, not adjectives. List open questions with what would
answer them. Recommend next steps in order, saying which need a user decision.

## 9. Rules for this mode

- Read, analyze, report. Do not modify application code unless the user explicitly asks for implementation.
- Read-only commands only; do not run scripts that write or delete without understanding them.
- Do not run long or shared-resource-heavy checks merely to have results; run them only when the report needs
  them, isolated, and record them as observations.
- Do not treat documentation as truth. Do not treat the absence of a finding as evidence of absence; say what
  you did not look at.
