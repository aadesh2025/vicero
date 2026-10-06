# Git and worktree discipline

Goal: change only what the task requires, leave other people's work alone, and never make a state you cannot
explain. The commands below assume git; if the project uses another version control system or none, apply the
same steps with its equivalents, or take and record a manual snapshot of the files you may touch.

## 1. Preflight (read-only)

Inspect, in order: the current branch and upstream; the status (staged, unstaged, untracked); the diff and
diff statistics; the last several commits with authors; the stash list; whether an operation is in progress
(merge, rebase, cherry-pick); whether index or lock files exist.

Write down what was already modified or untracked before you start. Those changes are not yours. Decide for
each: pre-existing, generated, in-progress work by someone else, or noise. You will need this list to explain
the final diff.

## 2. Detect concurrent sessions

Another agent or person may be using the same working tree. Signs: the current commit changes between two of
your checks; new commits with authorship or trailers you did not produce; files change that you did not touch;
a lock file appears; test or dev processes you did not start are running against shared services; an in-flight
run of yours fails in ways that match someone else's edits.

If you see any of these: stop, say what you saw, and ask how to proceed. Safer options to propose: work in a
separate worktree of your own, or agree who edits when. Do not "win" the race by overwriting, and do not
attempt to automatically merge, rebase onto, or reconcile another agent's in-progress changes with yours —
that decision belongs to a person, not to whichever agent noticed first. **Preserving another agent's
uncommitted work takes priority over completing your own task.** If finishing the task would require touching
a file they appear to own, stop before that file, report what you can still safely do, and name what is
blocked and why.

Do not run whole-suite checks or mutation experiments in a tree another session is editing, and do not edit
shared source temporarily. If your own experiment changes shared files, another process may run against the
broken state and produce misleading failures. Do experiments in a temporary worktree.

## 3. Temporary worktrees

Use a worktree for baselines and experiments so the main tree stays untouched.

- Create it outside the repository (a scratch directory) at the commit you want.
- Untracked local configuration is not in a fresh worktree. Copy only what you need, without printing it.
  Do not commit or leave copies of secrets.
- Run the same commands there with the same environment. Remove the worktree when done and confirm it is
  gone from the worktree list. Never leave background processes running from it.

## 4. Making changes

- Change the fewest files that solve the problem. Do not reformat, rename or reorder unrelated code.
- Preserve each file's existing line endings and encoding. Detect them before scripted edits; a scripted edit
  that normalizes line endings produces a whole-file diff.
- After each verification command that can write (build, install, format, codegen), check the status for new
  or modified files.

## 4a. Before and after checklist

Before changes: status; current branch; current commit; recent history; diff; untracked files.
After changes: status; diff statistics; the full diff, read in full. Every changed file has a stated reason.
Protected without exception unless the task requires touching them: user and uncommitted work, generated
files, lockfiles, migrations (never edit historical ones), environment and local configuration files.

## 5. Staging and committing

Only commit when the task calls for it or the user asked. When you do:

- stage explicit paths, never a blanket add; review the staged diff and the staged file list;
- exclude pre-existing changes that are not yours, generated output, local configuration and secrets;
- follow the repository's commit convention and message style; one logical change per commit;
- describe behavior changes and their consequences in the message, and reference the decision record;
- add any required attribution trailer the environment specifies.

Do not push, tag, amend, force, or rewrite history unless explicitly instructed.

## 6. Lockfiles, generated files and build artifacts

If one changes, classify it: intentional (task changed dependencies), pre-existing (already modified before
you started), generated (a tool rewrote it), caused by your command, or accidental. Look at the actual diff:
a change that only removes metadata or reorders entries usually comes from a different tool version, not from
a dependency change. Do not blindly revert (it may be someone's work) and do not blindly commit (it is noise).
Compare a checksum before and after your own commands to prove whether they touched it. Do not regenerate
lockfiles or upgrade dependencies unless the task requires it. A large unexplained lockfile change is a stop
condition.

## 7. Commands to avoid without explicit instruction

Hard resets (for example `git reset --hard`), forced checkouts or restores that discard changes, blanket cleans
of untracked files (for example `git clean -fd`), force pushes, branch or tag deletion, history rewriting
(rebase, filter), recursive deletion outside paths you created, and any bulk operation that names everything. Prefer a safer equivalent (inspect first, restore a
single file you own, delete only what you created). Look at a target before deleting or overwriting it.

## 8. Final diff review

Before reporting, list the complete change set and classify every file as: directly required; verification-
generated and removed or explained; local environment (untracked, not committed); pre-existing; or accidental.
Revert only your own accidental changes. There must be no unexplained modification. Report the final clean or
dirty state and why.

### Negative verification checklist

State these explicitly as confirmed facts (with the `git diff --stat` / full-diff evidence), not as silence.
Scope each to what the task should have touched — a finding here is "this changed and the task did not call
for it," not "this category exists somewhere in the repository":

- No file outside the task's stated scope was modified.
- No dependency, package-manifest or lockfile changed, unless the task was explicitly about dependencies.
- No migration changed (new migrations added for a schema change are fine; editing a historical one is not).
- No public API (routes, exported functions, schemas, wire format) changed, unless that was the task.
- No security policy or guard changed, unless that was the task.
- No architectural boundary or layering changed, unless that was the task.
- No generated file changed beyond what a command you yourself ran explains (name the command).

Treat this the same way a security finding is treated: state it as evidence, not assumption. "I did not touch
X" is verified by the diff showing X absent, not by remembering not to have opened it.
