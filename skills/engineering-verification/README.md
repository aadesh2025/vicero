# engineering-verification

A portable behavioral protocol that makes an AI coding agent work like a disciplined senior engineer inside a
repository it did not write. It contains no project-specific facts; the agent discovers the stack, commands and
architecture of whatever repository it is given.

## The problem it solves

Coding agents tend to assume a stack, invent commands, call every red test a regression, "fix" things nobody
asked about, restructure architecture that works, create a second copy of an abstraction that already exists,
spread a small feature across unrelated modules, and overwrite work in progress. This protocol replaces those
habits with: understand first, measure instead of guess, baseline before changing, classify failures with
evidence, reuse before creating, keep changes local, and stop when uncertain.

## The three modes

| Mode | Use it to | Default behavior | Output |
|---|---|---|---|
| **1. Investigation** | understand an unfamiliar or existing repository | read, analyze, report; no code changes unless asked | `templates/investigation-report.md` |
| **2. Verification** | decide whether the repository is a stable baseline, or check a recent change | baseline, run the checks that exist, classify failures, fix only justified issues, re-verify | `templates/verification-report.md` |
| **3. Feature safety** | add a feature without collateral damage | investigate, identify the owner, reuse existing abstractions, write an impact plan, implement locally | `templates/feature-impact-plan.md` |

It is not for redesigns, framework migrations or broad cleanups; it says so and asks what outcome you want.

## Contents

```
engineering-verification/
├── SKILL.md              the authoritative protocol; usable without the other files
├── README.md             this file
├── references/           deeper methodology
│   ├── investigation.md      how to investigate a repository; facts vs inferences vs open questions
│   ├── verification.md       baseline record, check sequence, fix loop, status rules
│   ├── security.md           risk-based attack-surface method, SSRF trace, tenant isolation, fix design
│   ├── architecture.md       discovering boundaries, measuring dependencies, lightweight enforcement
│   ├── testing.md            command discovery, running checks, failure classification, flakes, E2E
│   ├── git-discipline.md     preflight, concurrent sessions, worktrees, staging, lockfiles
│   ├── feature-safety.md     impact plan, change locality, duplicate-abstraction search
│   ├── state-and-sources.md  current/historical/planned/deferred, source-of-truth hierarchy, claim freshness
│   └── self-check.md         for maintainers of this skill package: its own structural self-test
└── templates/            reusable output structures
    ├── investigation-report.md
    ├── verification-report.md
    ├── architecture-map.md
    └── feature-impact-plan.md
```

`SKILL.md` holds the operating rules. References hold method. Templates hold output shapes. Nothing is
duplicated across them; agent-specific files are thin pointers with no protocol text.

## Using it

Point the agent at `SKILL.md` (`<skill dir>` below is wherever you put this directory) and name the mode. In
Claude Code, `/engineering-verification` followed by the same words works once it is installed.

- `Follow <skill dir>/SKILL.md, mode 1 (investigation), on this repository.`
- `Follow <skill dir>/SKILL.md, mode 2 (verification). Do not change application code unless a failure is
  proven a regression.`
- `Follow <skill dir>/SKILL.md, mode 3 (feature safety), before implementing: <feature>.`
- Scope a pass: `..., mode 2, scoped to commits <a>..<b>.`

Expected outputs: an investigation report, a verification report ending in `PASS`, `PASS WITH FOLLOW-UP` or
`BLOCKED` (no scores), or a feature impact plan followed by a feature impact report. Every report separates
what was observed from what was inferred and lists what was not run.

## Installation

Put this directory in the repository, or a shared location, and keep its internal structure: `SKILL.md` links to
`references/` and `templates/` by relative path.

**Claude Code.** Project skills are directories under `.claude/skills/<name>/` with a `SKILL.md` that has
`name` and `description` frontmatter, and `SKILL.md` here already has both, so no adapter is needed:

```
# per project (commit it so the team gets it)
git clone https://github.com/aadesh2025/engineering-verification-skill .claude/skills/engineering-verification

# or personal, available in every repository (Windows: %USERPROFILE%\.claude\skills\)
git clone https://github.com/aadesh2025/engineering-verification-skill ~/.claude/skills/engineering-verification
```

Then invoke `/engineering-verification`. Delete the cloned `.git` folder if you want to commit the skill into
your own repository rather than track this one. A repository that keeps the canonical copy elsewhere can add a
thin `.claude/skills/engineering-verification/SKILL.md` that repeats the `description` and tells the agent to
read the canonical `SKILL.md`; it must contain no protocol text.

**Other agents (no adapter provided).** The skill and rule formats of Codex, Gemini CLI, Cursor, OpenCode and
similar tools are not verified here, so none are provided and none are guessed. The protocol is plain Markdown:
place the directory in the repository and add a pointer to it in the instruction file that tool reads (check
its documentation for the name and format), or paste the pointer into the session. If a tool has its own skill
or rule format, write a thin file in that format that points at `SKILL.md`; do not copy protocol text into it.

## Portability

The core makes no assumption about language, framework, package manager, database, test runner, CI system,
agent or operating system. Commands are discovered from the target repository. It assumes git for its
examples and says how to adapt when the project uses something else. Examples in the references are
illustrative, never mandatory.

## Limitations

- It is a procedure the agent follows, not an enforcer; stop conditions rely on the agent noticing them.
- It cannot discover what is not in the repository or environment (production data, credentials, services you
  did not provide). It requires the agent to say so rather than guess.
- Concurrent-session detection is by observation (moving `HEAD`, unexpected changes, foreign processes).
- Security checks are conditional on the project having those surfaces; it is not a penetration test.
- Real checks can be slow and can touch shared services; the protocol requires isolation, but the environment
  must allow it.
- It does not cover redesigns, framework migrations or dependency-upgrade projects.

## License

Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0); see `LICENSE`. In plain terms,
from Creative Commons: you may share and adapt this material with attribution, for non-commercial purposes.
Copyright (c) 2026 Aadesh (aadesh2025). The `LICENSE` file is the authoritative text.

## Version

**engineering-verification v1.1.0.** v1.0.0 is the frozen protocol baseline; v1.0.1 changed no protocol
behavior (added the `LICENSE`, made this README independent of where the directory is placed). **v1.1.0** is a
MINOR, additive release — new capabilities, nothing an existing v1.0.x usage relies on was removed or
redefined:

- A current/historical/planned/deferred/unknown state classification, and a per-claim-type source-of-truth
  hierarchy, replacing the old blanket "code is truth" framing (`references/state-and-sources.md`).
- Claim-freshness checking: a document's own "verified"/"complete"/"all tests pass" wording is now explicitly
  historical evidence, not current evidence, until re-checked.
- A lightweight, provider-neutral CI/CD consistency check (branch triggers vs. the repository's actual
  policy, release gating vs. documented process).
- Evidence levels (E0-E5) for labeling how a claim was checked, without turning it into a score.
- A named change-scope guard (REQUIRED FOR REQUEST / CORRECTNESS / SECURITY vs. OPTIONAL FOLLOW-UP / UNRELATED)
  applied uniformly to anything found mid-task, not only to debt found during investigation.
- A scoped "documentation reconciliation" procedure under Mode 2, for tasks that are specifically about fixing
  stale documentation without rewriting history.
- An explicit negative-verification checklist (no unrelated file, dependency, migration, API, security-policy
  or architecture change) as a stated-and-evidenced fact, not a silent assumption.
- A sharper concurrent-session rule: another agent's uncommitted work outranks finishing your own task, and
  must never be auto-merged.
- `references/self-check.md`, a portable checklist for validating this skill package's own structure and
  version consistency when maintaining it.

- The universal skill answers "what should an AI coding agent do?". Facts about a particular repository (its
  commands, layout, rules, decisions) belong in that repository's own instructions, never in this skill. Do not
  customize the skill per project.
- Changes are intentional and versioned with semantic versioning:
  **MAJOR** for breaking changes to the protocol's behavior; **MINOR** for new capabilities that do not
  invalidate existing behavior; **PATCH** for clarifications, corrections and non-breaking improvements.
- Keep the version in three places: the heading of `SKILL.md`, this section, and the commit message of the
  change. Record what changed in the commit that bumps it. The Claude adapter repeats the `description` line;
  when it changes, change it in both files.
