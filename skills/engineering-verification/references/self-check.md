# Self-check: validating this skill package

For whoever is maintaining `engineering-verification` itself (editing `SKILL.md`, its references or templates,
or bumping its version) — not for an agent using the skill to verify a target repository. Run this before
releasing a change to the skill. It is a checklist, not a test framework; every check is a portable read or
grep, with no language runtime or build tool required.

## 1. Required files exist

Confirm every file `SKILL.md`'s own "Files" section names is present on disk, at the path it names, relative
to `SKILL.md`: each `references/*.md` and `templates/*.md` it lists. A file listed but missing, or present but
unlisted, is a finding.

## 2. Frontmatter is valid

`SKILL.md` must open with a `---`-delimited YAML frontmatter block containing at least `name` and
`description`. Confirm: the block is well-formed (parses as YAML — a manual read is enough for a small, fixed
set of keys), `name` matches the skill's directory name, and `description` is the single source of truth for
that text — any adapter file (for example a thin per-agent pointer) that repeats it must match verbatim.

## 3. Referenced files exist (no broken links)

Every relative link `SKILL.md` and each reference file makes to another file in this package must resolve.
Extract them (a simple search for markdown link syntax and bare backtick-quoted paths like `` `references/
foo.md` `` is enough — no need for a link-checking tool) and confirm each target exists at the stated relative
path. Do the same for templates referenced from references, and references referenced from templates.

## 4. Templates exist and match what modes reference

Each mode in `SKILL.md` names the template its output uses. Confirm that template file exists and that its
structure still matches what the mode and its reference file describe (for example, if a mode's method file
promises a named report section, the template should have it, and vice versa — drift here is the same class of
problem this skill asks agents to find in target repositories, applied to itself).

## 5. Version consistency

The version number must match, character for character, in every location the skill's own versioning policy
names: the heading of `SKILL.md`, the Version section of `README.md`, and any agent-specific adapter file that
repeats the version in its own heading or body text (not its frontmatter, which carries no version). Grep for
the previous version string after a bump to confirm nothing was missed, and for the new one to confirm it
landed everywhere required.

## 6. No project-specific terms leaked into the universal protocol

This is the most important check, because it is the easiest to violate without noticing while using the skill
on a real project and then "helpfully" folding in something learned there. `SKILL.md`, every `references/*.md`
and every `templates/*.md` must stay free of: specific product or company names; specific repository paths
that are not illustrative placeholders; specific framework or library names presented as a requirement rather
than an example (an example must read as optional — "for example, in a project using X" — not as instruction);
specific test counts, specific security rules, specific architecture rules, or specific CI providers presented
as mandatory. A name used purely as an illustrative example of a category (e.g., naming one real test runner
once, to show what "the convention for the detected stack" means) is acceptable; a rule that only makes sense
for one project is not. If in doubt, ask: would this sentence be false or meaningless for a repository in a
completely different language, domain and company? If yes, it does not belong in the universal skill.

## 7. Examples remain clearly illustrative

Every concrete example (a sample rule, a sample command, a sample file name) should read unmistakably as "for
example," not as a requirement. Scan for examples that have drifted toward sounding mandatory and soften the
wording if so.

## 8. No duplicated or contradictory protocol sections

Search for the same rule stated in two places with different wording, which tends to drift out of sync over
time. A short cross-reference ("see X") is fine; a second independent restatement of the same rule is not.
Also check for direct contradictions introduced by a partial edit (one section says "always" and another says
"except when," without the second being a deliberate, cross-referenced refinement of the first).

## 9. Semantic versioning was followed

Compare the nature of the change against the version bump: a breaking change to existing protocol behavior
(something an existing user would now have to do differently, or that invalidates a prior report's shape) is
**MAJOR**; a new capability that does not change or invalidate existing behavior is **MINOR**; a clarification,
correction or non-behavioral improvement is **PATCH**. If the bump does not match the change, fix the version
number, not the description of the change.

## 10. The change is documented

The commit that bumps the version states what changed and why, at the level of "what capability was added or
what was corrected," not just "various improvements." A future maintainer (or this same checklist, run again
later) should be able to tell from the commit message alone whether a given behavior change was intentional.

## Reporting a self-check

State each of the ten checks as pass/fail with the evidence (what you read, what you grepped for, what you
found). This is a short pass/fail list, not a filled copy of the investigation or verification report
templates — those are for verifying a target repository, not this package.
