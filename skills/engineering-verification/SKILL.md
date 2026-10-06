---
name: engineering-verification
description: Disciplined senior-engineer protocol for working in an existing repository. Three modes - investigate an unfamiliar codebase (read, analyze, report), verify that it is a stable baseline (baseline, checks, failure classification, justified fixes only), and add features safely (impact plan, change locality, reuse before creating). Discovers the project's real stack and commands instead of assuming them. Use to understand, assess or stabilize a repository, to check a recent change, or before a non-trivial feature. Not for redesigns or unsolicited refactors.
---

# engineering-verification v1.1.0

A behavioral protocol for any coding agent. It contains no project-specific facts: discover them. What is true
about a repository belongs in that repository's own instructions, never in this skill.

```
UNDERSTAND before changing.   VERIFY before claiming.   REUSE before creating.   LOCALIZE before spreading.
TEST before declaring.        PROTECT user work.        STOP when uncertain.     Do not refactor for its own sake.
```

## Ground rules (all modes)

1. **Evidence over assumption.** Every claim in a report traces to a command you ran, a file you read, or a
   reproduction. Documents describe intent; code and running results describe behavior. **Neither is
   universally "the truth"** — which one controls depends on the kind of claim (a dependency version, a CI
   trigger, a security policy, a historical event). Resolve contradictions with the source-of-truth hierarchy
   in `references/state-and-sources.md`; do not default to "code wins" or "docs win" without checking which
   applies.
2. **Separate FACT, INFERENCE and OPEN QUESTION, and CURRENT, HISTORICAL, PLANNED, DEFERRED and UNKNOWN.**
   Label every claim's evidence type and, when its age matters, its state. Never present an inference as fact,
   and never treat a historical or planned claim as a description of current behavior. State a cause as proven,
   inferred or unknown. Method: `references/state-and-sources.md`.
3. **Never claim a check passed that you did not run.** "Not run" and "could not run" are valid outcomes,
   with reasons. A claim of "passing"/"verified"/"complete" found in a document is not itself a check result —
   re-verify it when it materially affects the task (claim freshness, `references/state-and-sources.md`).
4. **Never hide or reword a failure.** Never call a failure pre-existing without evidence (Failure
   classification).
5. **Do not invent commands.** Take them from repository documentation, CI configuration, manifests or
   scripts. If none exists, write "no documented command".
6. **The existing architecture is the default.** No new layers, patterns, frameworks, dependency upgrades or
   directory moves unless the user asked, or a verified problem cannot be solved otherwise (then stop).
7. **Smallest safe change, and classify before changing anything.** Preserve behavior. Before editing, label
   each proposed change **REQUIRED FOR REQUEST**, **REQUIRED FOR CORRECTNESS**, **REQUIRED FOR SECURITY**,
   **OPTIONAL FOLLOW-UP** or **UNRELATED**. Only the first three may be made without asking; the rest are
   recorded as follow-up, never implemented on your own initiative — "I found something ugly, therefore I
   refactored it" is exactly what this rule forbids.
8. **Protect other people's work.** Uncommitted changes you did not write are not yours to revert, reformat,
   stage or commit. If another agent or person appears to own overlapping files, **preserving their work takes
   priority over finishing your task** — stop and say so rather than merging, overwriting, or racing to land
   your change first.

## Choose a mode

| The user wants to... | Mode |
|---|---|
| understand an unfamiliar or existing repository before changing it | **1. Investigation** |
| know whether the repository is a stable baseline, or check a recent change | **2. Verification** |
| add or change a feature without collateral damage | **3. Feature safety** |
| redesign, migrate a framework, or clean up broadly | None. Say this protocol does not cover it and ask what outcome they want. |

If the request is ambiguous and a wrong guess is expensive, ask one specific question; otherwise pick a
default, state it, and continue. Investigation is the default when the user asks to "look at", "audit" or
"understand" and does not ask for changes.

## Phase 0: Preflight (all modes)

1. **Git state** (or the equivalent if the project does not use git): branch, current commit, recent history,
   status (staged, unstaged, untracked), diff, stashes, operations in progress. Record what was already
   modified before you started and who plausibly owns it. See `references/git-discipline.md`.
2. **Concurrent agents.** If another agent or person appears to be using the same worktree or shared
   services, **stop** (see Stop conditions).
3. **Read the project's own instructions** in this order: agent instruction files, README and contributing
   docs, architecture docs and decision records, environment/setup docs, CI configuration. Obey explicit rules
   ("read X before touching Y", forbidden areas, required checks).
4. **Detect the stack** from manifests, lockfiles, build and test configuration: languages, package managers,
   frameworks, datastores, queues, external services, deployment model, monorepo or not.
5. **Discover commands** for install, lint, static analysis, typecheck, tests (unit, integration, end-to-end),
   build, migrations. Source priority: documented command > CI job > manifest script > convention. Record the
   source. Prefer the CI form.
6. **Find shared resources** the checks touch (databases, caches, queues, ports, files, external and paid
   services) and decide how to isolate your runs. Never run something that deletes or mutates data you did not
   create without reading it and doing a dry run first.
7. **Note what you must not do:** production access, credentials you were not given, destructive scripts.

## Mode 1: Investigation (read, analyze, report)

Default behavior is READ → ANALYZE → REPORT. **Do not modify application code** unless the user explicitly asks
for implementation.

`DISCOVER → READ INSTRUCTIONS → MAP REPOSITORY → IDENTIFY ARCHITECTURE → DOMAINS → DEPENDENCIES → SECURITY
BOUNDARIES → TEST/BUILD SYSTEM → TECHNICAL DEBT → REPORT`

Measure rather than guess (import graphs, route tables, config, CI) and record the architecture in
`templates/architecture-map.md`. Classify technical debt as **REQUIRED**, **OPTIONAL** or **BLOCKING**.
For any claim about *when* something is true (a session handoff, a phase summary, a "done"/"complete" marker),
classify it CURRENT / HISTORICAL / PLANNED / DEFERRED / UNKNOWN before relying on it —
`references/state-and-sources.md`. Method: `references/investigation.md`. Output:
`templates/investigation-report.md`, with FACTS, INFERENCES and OPEN QUESTIONS kept apart, and nothing left
unlabeled under "Not Run / Not Verified".

## Mode 2: Verification

`DISCOVER → BASELINE → VERIFY → TEST → SECURITY → ARCHITECTURE → BUILD → E2E → DOCUMENTATION → GIT REVIEW →
CLASSIFY FAILURES → FIX ONLY JUSTIFIED ISSUES → REVERIFY → REPORT`

Record the baseline on the untouched tree before any change. Run the checks that exist. Classify every failure.
Fix only what the fix rule allows, then re-verify. If the user scoped the pass (a commit range, an area, one
concern), go deep on the scope, still record the baseline and run the checks that cover it, and list what was
out of scope. Method: `references/verification.md`; commands, runs and classification detail:
`references/testing.md`. Output: `templates/verification-report.md`.

### Documentation reconciliation (a scoped Mode 2 pass)

When the task is specifically "make the docs match reality" (not a general verification pass), run this
narrower sequence instead of the full one above, still inside Mode 2's evidence and fix-rule discipline:

1. Identify current implementation facts (read the code/config, not the docs, for each claim in scope).
2. Identify current documentation claims on the same points.
3. Detect contradictions between the two.
4. Classify each document section CURRENT, HISTORICAL, PLANNED, DEFERRED or UNKNOWN
   (`references/state-and-sources.md`) before touching it.
5. Fix only contradictions in CURRENT documentation. Never rewrite a HISTORICAL record to agree with today —
   correct the current artifact, not the past one.
6. Verify the resulting documentation (links, cross-references, anything generated from it).
7. Report what was corrected and what was deliberately preserved as history, with the reason for each.

This is a documentation fix, not a documentation rewrite: do not restyle, reorganize or "improve" prose that
was not contradicted. A full CI/CD consistency check (branch triggers, release gates — Principles, "CI/CD
consistency") is in scope whenever workflow or process files are part of the reconciliation.

### Failure classification

Every failing check gets exactly one label and the evidence for it:

| Label | Required evidence |
|---|---|
| **Baseline failure** | Fails identically on the untouched base (same identifiers, same error), reproduced by you in a temporary worktree or checkout, a merge base, or a recorded CI result. If you cannot reproduce the base, do not use this label; use Unknown and say why. |
| **Regression** | Passes on the base, fails with the changes, reproducible. |
| **Environment failure** | Cause is outside the code (network, credentials, service, disk, port, clock). Show the error that proves it. |
| **Flaky failure** | Passes and fails on the same code. Show repeated runs with counts. A stall or dropped connection that vanishes on rerun is flaky or environmental until shown otherwise, with the cause stated as unproven. |
| **Unknown** | Everything else. State what you tried. Never round Unknown up to a friendlier label. |

"Red test = my change broke it" is an assumption. When the same failures appear in two runs, compare the
identifier lists, not the counts. If comparison with a baseline is impossible, say so.

### Fix rule

Fix only when the problem is verified, you can explain the cause, the fix is small and behavior-preserving,
and it needs no architectural change. Then: state the finding and the smallest fix; add a focused test that
fails without it; change as few files as possible; run the focused test, the neighboring suite, then the
broader checks; re-verify the original failure. Never modify a test only to make a suite green.

If a fix changes behavior that legitimate users may rely on, **stop and get a decision** first, with the
trust-boundary analysis and options. When you find the same bug class on a sibling path, report it; fix it
only if it is the same small, behavior-preserving change and inside the user's request.

## Mode 3: Feature safety

`UNDERSTAND REQUEST → INVESTIGATE EXISTING SYSTEM → IDENTIFY DOMAIN OWNER → FIND EXISTING ABSTRACTIONS →
FEATURE IMPACT PLAN → IMPLEMENT → TEST → VERIFY → REVIEW DIFF → REPORT`

Applies to any non-trivial change: one that adds an entry point, stored data, an outbound call, a permission
or a dependency, or touches more than one domain. A change whose location and effect are obvious and local may
skip the written plan, not the search for existing code. Before editing, write the plan
(`templates/feature-impact-plan.md`): what should change **and what should not**. Method:
`references/feature-safety.md`. Investigate the affected part of the system first (Mode 1 depth, scoped to
the feature).

### Change locality

`FEATURE → DOMAIN OWNER → EXISTING INTERFACE → MINIMAL DEPENDENCIES.` Do not edit unrelated modules because
they are nearby or convenient.

Set a change budget from the plan. One to three files is normal for a localized change. If a supposedly small
change grows past the plan, touches a file or domain the plan protected, or pulls in shared or global code
(a working guide: more than about ten files, or more than one domain you did not expect), **stop and explain
the dependency chain**, then decide with the user: reuse an existing abstraction; a boundary is misplaced; the
feature genuinely crosses domains; or the edit is accidental. Large changes can be legitimate for security
boundaries, migrations, API migrations, generated code and cross-cutting infrastructure; explain why. Do not
combine a feature with a refactor, a dependency upgrade or a framework migration.

### Duplicate-abstraction rule

Before creating a service, manager, helper, utility, client, provider, adapter, repository, middleware,
validator, hook, context or configuration mechanism: search by name, by synonym and by behavior (who already
makes this call, parses this format, checks this permission, reads this setting), in the neighboring modules,
the shared area and the tests. If an equivalent exists, reuse or extend it. If not, write one sentence saying
why a new one is justified. "I could not find it" is not a reason; widen the search. Prefer a narrow named
module to a generic bucket.

## Principles

### Architecture

Understand the existing architecture before changing it. Prefer an existing abstraction to a new one, and a
local change to a repository-wide one. Do not introduce microservices, repository-per-module, interface-per-
class, factory-per-provider, CQRS, event buses, dependency-injection frameworks, or clean/hexagonal/DDD
rewrites unless the project already uses them, the user asked, or a demonstrated concrete problem requires
them. Do not split coherent files or functions to meet a size number. Method: `references/architecture.md`.

### Security

Risk-based: identify the attack surfaces this project actually has and verify those; do not run a generic
checklist against every project. Trace untrusted input to the sink. For any server-side request built from
configurable input, trace input → validation → storage → URL construction → DNS → connection → redirect →
final destination, and do not assume one HTTP client is the only outbound path. Distinguish untrusted tenant
or user configuration from explicitly trusted operator configuration, and do not block or allow private
destinations without understanding the trust model. In a multi-tenant product, verify isolation as a member of
a different tenant against real resources. Method: `references/security.md`.

### Testing

Discover the test system → establish a baseline → run focused tests → run broader tests → classify failures →
re-verify. Run checks at CI scope. Use isolated instances and separate ports for your runs; do not point tests
at someone else's services. Prove a boundary test can fail (mutation check in an isolated worktree, never in a
shared tree). Method: `references/testing.md`.

### Technical debt and cleanup

Classify findings **REQUIRED** (must be fixed for the task to be correct), **OPTIONAL** (improvement) or
**BLOCKING** (prevents a reliable conclusion). Only REQUIRED items are normally fixed during verification.
OPTIONAL items go under Optional follow-up. Do not turn an investigation into a cleanup project.

### Change-scope guard

A different axis from debt severity above: not "how bad is this finding" but "may I change it now." Before
editing, label every candidate change **REQUIRED FOR REQUEST** (the user asked for this), **REQUIRED FOR
CORRECTNESS** (the task is wrong without it — a proven regression, a verified bug in scope), **REQUIRED FOR
SECURITY** (a verified, in-scope vulnerability), **OPTIONAL FOLLOW-UP** or **UNRELATED**. Only the first three
may be changed without asking. This applies even to things found by accident: a stale abstraction, an unused
file, an old dependency, a naming inconsistency or an architecture smell noticed while doing something else is
OPTIONAL FOLLOW-UP or UNRELATED, not a reason to touch it. See Ground rule 7.

### Documentation consistency

Verify that code equals documentation for the README, architecture docs, decision records, security,
environment, deployment and test documentation. Correct factual contradictions only in documentation that
describes **current** behavior; do not rewrite for style, and never edit a document (or a section of one)
that is explicitly historical merely to make it agree with today — see `references/state-and-sources.md` and
Mode 2's "Documentation reconciliation" for the full procedure.

### CI/CD consistency

Treat CI/release configuration as a claim about process, and check it against the repository's actual policy
the same way code is checked against documentation. Provider-neutral; apply whatever of this exists. Compare:
the repository's actual default/primary branch, each workflow's push/PR/tag triggers, deployment conditions
(automatic vs. manual dispatch vs. tag-gated), any visible required checks or branch protection, and the
documented release process. Flag contradictions such as a workflow that only triggers on a branch the
repository no longer uses, or documentation that claims an automatic deploy that the workflow actually gates
behind manual dispatch. Fix only the configuration or documentation that is factually wrong for the
repository's current policy; do not redesign the pipeline or infer a policy the project never stated.

## Git safety

Inspect before and after (status, branch, commit, history, diff, untracked; then status, diff statistics and
the full diff). Every changed file needs a reason. Protect user work, uncommitted work, generated files,
lockfiles, migrations and environment files. Never run blanket destructive commands such as hard resets or
forced cleans of untracked files, and never overwrite another agent's changes. Classify any lockfile or
generated-file change before deciding what to do with it. Commit only when the task calls for it or the user
asked; stage explicit paths only. Method: `references/git-discipline.md`.

### Negative verification

Before reporting, confirm explicitly — not just by omission — what did **not** change, scoped to what the task
should have touched: no unrelated files modified, no dependency or lockfile changes, no migration changes, no
public API changes, no security-policy changes, no architecture/boundary changes, no generated file changed
beyond what a run you triggered explains. Use `git diff --stat` and a full read of the diff to state this as a
fact, not an assumption — "nothing else changed" is itself a claim that needs evidence.

## Environment and production safety

- **Secrets:** never print, log, commit or paste passwords, keys, tokens, private keys or credentialed URLs.
  Report variable names and non-secret values only. Mask connection strings.
- **Production:** read-only inspection by default. Do not modify production data unless the user explicitly
  requests it and it is clearly authorized. Describe any needed production action separately as
  **OPERATIONAL FOLLOW-UP** and do not perform it silently. If you cannot inspect production, say so; never
  imply you did.
- **Processes and ports:** stop only processes you started. Remove temporary worktrees, files and services you
  created. Bound long runs with timeouts and enable stack dumps on stall where the tool offers it.
- **Verification side effects:** builds and installs can rewrite tracked files; check the worktree after them.

## Stop conditions

Stop, report, and wait. Do not guess past any of these.

- **Another agent or person appears to be modifying the same worktree, or owns overlapping files** (moving
  `HEAD`, files changing that you did not touch, foreign processes on shared resources). Do not run destructive
  cleanup, reset files, run broad formatting, commit over it, assume the changes are yours, or attempt to
  "merge" their in-progress work automatically. Tell the user that concurrent modification makes verification
  unreliable; their uncommitted work outranks finishing your task.
- Unexplained user changes, or unrelated files beginning to change.
- Production data would be modified, or secrets are required and unavailable.
- A production or integration claim cannot be verified from where you are (no access, no environment) — say
  so; do not report it as checked at a level you did not reach.
- A security boundary or the architecture cannot be determined confidently, or two sources of evidence about
  the architecture conflict and neither source-of-truth rule resolves it (`references/state-and-sources.md`).
- A fix needs a broad redesign, an unexpected migration, or an unexpected dependency upgrade.
- A migration, public API, or security-policy change is discovered outside the task's stated scope.
- A generated file or lockfile changes unexpectedly.
- A failure cannot be classified with evidence.
- Required commands cannot be discovered from documentation, CI, manifests or scripts, and no convention
  applies confidently.
- Continuing would delete, overwrite or publish something hard to reverse.
- You found a serious security issue: report it clearly at once; do not fold it into a bigger change.

Report the blocker and what would resolve it. Do not invent a workaround to avoid stopping.

## Reporting

Use the templates: `investigation-report.md` (Mode 1), `verification-report.md` (Mode 2),
`feature-impact-plan.md` (Mode 3; plan before, report after). Reports are factual: exact counts, exact
outcomes, explicit "not run", causes labeled proven/inferred/unknown. No numerical scores, grades or praise.
Verification status is **PASS**, **PASS WITH FOLLOW-UP** or **BLOCKED**. Every report separates what was
observed (FACT: ran, read, measured) from what was inferred (INFERENCE: concluded from observations) from what
is unknown (OPEN QUESTION), lists required follow-up, and states what was deliberately not run — the same
FACT/INFERENCE/OPEN QUESTION labels from Ground rule 2, not a second vocabulary.

### Evidence levels

Label how a claim was checked, not how good the repository is — these are not a quality score:

| Level | Meaning |
|---|---|
| **E0** | Not checked. |
| **E1** | Static inspection (read the file/config; no execution). |
| **E2** | Targeted check (one command, one probe, one focused test). |
| **E3** | The relevant test suite ran at its normal scope. |
| **E4** | The full verification suite ran (tests, lint, typecheck, build, as applicable). |
| **E5** | Verified against a running instance or production/integration environment. |

Use these next to a claim ("backend tests: E3", "production deployment: E0") so a reader can see exactly how
much weight the claim carries. A document's own "verified"/"complete"/"production-ready" wording is not
evidence by itself — state the level you actually achieved when checking it, which may be lower than the
document claims.

## Files

- `references/investigation.md` — how to investigate a repository and classify what you find.
- `references/verification.md` — baseline record, stage-by-stage checks, fix loop, status rules.
- `references/security.md` — attack-surface identification, per-class methods, SSRF trace, fix design.
- `references/architecture.md` — discovering boundaries, measuring dependencies, lightweight enforcement.
- `references/testing.md` — command discovery, running checks, classification procedure, flakes, E2E.
- `references/git-discipline.md` — preflight, concurrency, worktrees, staging, lockfiles, safe commands.
- `references/feature-safety.md` — impact plan, locality, duplicate search, feature report.
- `references/state-and-sources.md` — current/historical/planned/deferred/unknown classification, the
  source-of-truth hierarchy, and claim-freshness checking.
- `references/self-check.md` — for maintainers of this skill package itself, not for verifying a target
  repository: how to validate the skill's own structure, links, version consistency and project-neutrality.
- `templates/` — `investigation-report.md`, `verification-report.md`, `architecture-map.md`,
  `feature-impact-plan.md`.
