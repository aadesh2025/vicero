# Verification methodology (Mode 2)

Goal: establish, with evidence, whether the repository (or a scoped change) is a stable baseline, fix only what
is justified, and report factually. Command discovery, running, and failure-classification detail live in
`testing.md`; this file defines the sequence, the baseline record, the fix loop and the status rules.

## 1. Baseline record

Write this before changing anything. Every later "pre-existing" claim is checked against it.

- **Repository state:** commit and branch; tracked files already modified; untracked files; stashes; concurrent
  activity observed (or "none observed").
- **Environment:** OS and shell; runtime and package-manager versions; services available with host, port and
  owner; local configuration differences that matter (masked, no secrets); how shared resources were isolated.
- **Discovered commands:** for each of install, lint, static analysis, typecheck, unit, integration,
  architecture, end-to-end, build, migration check, dependency audit: the command, its source (doc, CI,
  manifest, convention) and its scope. "no documented command" is a valid entry.
- **Results on the untouched tree:** per check, outcome, counts, duration, and the identifier of every failing
  test with its first error line.
- **Not run:** each check that was not run, with the exact reason.

A document that already claims "all tests pass," "verified," or "production-ready" is historical evidence
about whenever it was written, not current evidence about the tree you are about to baseline — re-run the
checks yourself and report the evidence level you actually reached (`SKILL.md`, "Evidence levels"); do not
carry the document's claim forward as if it were your own result (`state-and-sources.md`, claim freshness).

Keep it in the working notes or the report; do not commit it unless asked.

## 2. Sequence

Run in order; skip a step only if it does not apply, and say so.

1. **Preflight** (`SKILL.md`, Phase 0), then the baseline record above.
2. **Verify the scope.** What is being verified: the whole repository, a commit range, an area, one concern.
3. **Tests.** Focused tests for the scope first, then the broader suites, at CI scope. Where an area has no
   tests, report the gap; do not write broad new suites unprompted.
4. **Security.** Verify the attack surfaces that exist (`security.md`). If the product is multi-tenant, run the
   isolation checks as a member of a different tenant. For a recent security fix, also check sibling paths for
   the same bug class.
5. **Architecture.** Run existing architecture or dependency tests; check dependency direction, cycles and
   boundary rules (`architecture.md`).
6. **Static checks.** Lint, formatting in check mode, typecheck, static analysis, dependency audit if
   documented.
7. **Build.** Production build for each deliverable. Check the worktree afterward for rewritten tracked files.
8. **End-to-end.** Read the setup first (`testing.md` section 6). Prefer an isolated stack. If it cannot run,
   record why; a partial run is not a pass.
9. **Documentation and CI/CD consistency.** Compare README, architecture docs, decision records, security,
   environment, deployment and test docs with the implementation; classify each as current, historical,
   planned, deferred or unknown before touching it (`state-and-sources.md`), and correct factual contradictions
   only in current documentation. Where the task touches process: compare the repository's actual default
   branch, each workflow's triggers, and the documented release process for contradictions (`SKILL.md`,
   "CI/CD consistency").
10. **Git review.** Full diff; classify every modified file (`git-discipline.md` section 8).
11. **Classify failures**, apply the fix rule, **re-verify**, then **report**.

## 3. Fix loop

For each failure worth fixing:

1. Classify it with evidence (`SKILL.md`, Failure classification). Only regressions, and issues the user asked
   to be fixed, or REQUIRED debt, are candidates. Baseline, environment and flaky failures are reported, not
   "fixed" by changing behavior or tests.
2. State the finding and the smallest safe fix before editing. If it changes behavior legitimate users may rely
   on, stop and get a decision.
3. Add or update a focused test that fails without the fix. Make the fix in as few files as possible.
4. Run the focused test, then the neighboring suite, then the broader checks that cover the change.
5. Re-verify the original failing check. Update documentation that the fix made untrue.
6. If a fix starts growing beyond the change budget or into unrelated files, stop (`SKILL.md`, Change locality).

Boundary tests (isolation, destination policy, architecture rules) should be shown to fail when the protected
behavior is broken: mutation check in an isolated worktree.

## 4. Deciding the overall status

- **PASS**: every applicable check ran and passed or is a baseline failure reproduced on the base; no
  regression; no unexplained worktree change; no unresolved required finding.
- **PASS WITH FOLLOW-UP**: as above, but with items that remain and do not invalidate the conclusion, such as
  known baseline or environment failures, optional debt, an operational follow-up, or a documented residual
  risk. List them.
- **BLOCKED**: a required check could not run, a failure is a regression or unclassified, a stop condition
  applies, or a serious finding is open. Name the blocking item.

"Passed earlier" is not "passes now": a check that was not re-run after later changes is not evidence about
the current state.

## 5. Reporting

Use `templates/verification-report.md`. Include: exact commands' outcomes and counts, each failing identifier
with one label and its evidence, what you changed and what you deliberately left alone, remaining risks with
cause labeled proven/inferred/unknown, and optional follow-up (not implemented). Include a Tenant Isolation
section only if the product is multi-tenant. No scores, grades or praise.
