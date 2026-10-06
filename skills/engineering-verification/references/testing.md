# Testing, baseline and failure classification

## 1. Discover the commands

Find each check's command in this order and record the source:

1. what the project's own documentation and agent instructions say to run;
2. the continuous-integration configuration (this is the closest definition of "passing");
3. scripts declared in the package or build manifests, and task runners (Makefile and similar);
4. convention for the detected stack, only as a last resort, and marked as such.

Checks to look for: install/setup, lint, formatting (check mode), static analysis, typecheck, unit tests,
integration tests, security tests, isolation tests, architecture tests, frontend tests, end-to-end tests,
production build per deliverable, migration check, dependency audit.

If a check has no documented command, write "no documented command". Do not improvise a substitute and
present it as the project's check. Use the same scope CI uses (for example, the whole tree rather than one
folder) when reporting a clean result.

## 2. Understand what the checks touch before running them

Read test configuration and fixtures. Note: which database or service the tests connect to; whether each test
runs in a rolled-back transaction or writes permanent data; whether tests call paid or external services;
which ports they bind; what environment they read; whether other people or agents are using the same
instance. Then choose isolation: a dedicated instance or namespace, different ports, override variables for
the run, mocks that the project already provides.

Configuration for the local machine (ports, connection strings) may be wrong for the environment you are in.
Diagnose it by comparing what is running with what is configured. Fix untracked local configuration only when
the task calls for it and the change is small and reversible; never edit tracked shared configuration to suit
your machine.

## 3. Baseline

Before any change, run the checks on the untouched tree and record (format in `verification.md` section 1): commit, date,
environment, each command, exact result counts, duration, the identifiers of every failing test, and whatever
could not run and why. This record is what you compare against later.

Long suites: run them in the background with output to a file, bounded by a timeout, and poll. Do not run two
suites against the same shared datastore at once unless you know they do not collide (unique keys, shared
rows, and locks make such runs stall or fail for reasons unrelated to the code).

## 4. Classify every failure

Label each failure with exactly one of: baseline failure, regression, environment failure, flaky failure, unknown
(definitions and evidence in `SKILL.md`). Procedure:

1. Record the failing identifier and the first informative error line.
2. Re-run that test alone. Passing alone but failing in the suite suggests order, contention or state.
3. Reproduce on the base: create a temporary worktree at the base commit or merge base (see
   `git-discipline.md`), or use a recorded CI result or documented baseline, and run the same test there with
   the same environment. Same identifiers and same error means baseline failure. Do not
   rely on memory, documentation or an earlier report.
4. If it passes on the base and fails with the changes, it is a regression: investigate. If you cannot
   reproduce the base (cannot build it, no CI result, no documented baseline), say so and use unknown.
5. If the cause is outside the code, show the message that proves it (network refused, download failed,
   service unavailable, credential missing) and label it environment failure. Say what would make it pass; do
   not change behavior to make an environment failure go green.
6. If results vary on identical code, repeat and record the counts (for example 3 of 3 pass alone, 1 of 2 fail
   in the suite) and label it flaky failure. State the cause as unproven unless you proved it.
7. If none of the above fits, label it unknown and say what you tried.

When you compare two runs, compare the **lists of failing identifiers**, not just the totals; equal counts
can hide a swap.

## 5. Hangs and stalls

A run that stops making progress is a finding, not something to wait out. Bound runs with a timeout. If the
test runner can dump stacks after a per-test time limit, enable it. When a stall happens: note which test the
run was on (count completed tests against the collected order), check whether the datastore has waiting or
idle connections, check for other processes competing for the same resources, then kill only what you
started, and re-run. If a re-run passes, record the stall as an unexplained flake with the evidence you
gathered.

## 6. End-to-end tests

Read the configuration and any wrapper script first. Determine required services, ports, base URLs, seed or
fixture data, environment variables, browser installation, and what the run leaves behind or deletes.

- Prefer an isolated stack: your own application instance, worker and datastore namespace on separate ports,
  with any mock or deterministic provider setting the project documents for keyless runs.
- Do not point tests at someone else's running services. Do not stop processes you did not start.
- A wrapper that cleans up after the run may delete data (fixtures, seed accounts). Read what it targets and
  dry-run it before using it; if it could delete something that matters, run the raw test command instead and
  report the debris left behind.
- If the suite cannot run, record the exact reason. Do not report a partial run as a pass.
- For failures: classify against the base as above (running the same specs against base-commit application
  code in a temporary worktree is a good discriminator). Do not change application behavior to satisfy a
  flaky or environment-specific test.

## 7. Static checks and builds

Run lint and typecheck at CI scope. Fix only failures caused by the current change, or ones the task asks you
to fix. Do not reformat unrelated files. For builds, check the worktree afterward: builds and installs can
rewrite tracked files (lockfiles, generated code, build metadata). Investigate any such change before
deciding what to do with it (see `git-discipline.md`).

## 8. Proving a test can fail

A test that has never failed proves little. For tests that guard a security or architecture boundary, do a
mutation check: in an isolated worktree, break the thing the test protects, confirm the test fails, discard
the worktree. Never mutate the shared tree; anything running against it will see the broken code.

## 9. What "pass" means in the report

State, per check: the command's source, the scope, the counts, and the outcome, labeled with an evidence level
(`SKILL.md`, "Evidence levels") so a reader can tell a full run from a targeted probe. "Not run" and "could not
run" are valid outcomes with reasons. A check that passed at an earlier commit and was not re-run after later
changes is historical evidence, not current evidence about the state in front of you
(`state-and-sources.md`, claim freshness).
