# State classification, source-of-truth hierarchy, and claim freshness

Goal: stop two specific mistakes. First, rewriting or "fixing" a document that correctly describes the past
because it disagrees with today. Second, blindly trusting whichever source (code or docs) is habitual, instead
of the one that actually controls the kind of claim in front of you. Both mistakes come from treating "what is
true" as a single question; it is really two questions — *when* is this claim about, and *which source
controls this kind of claim*.

## 1. State classification

Before correcting, relying on, or reporting any claim a repository makes about itself, classify it:

- **CURRENT** — describes behavior the repository has now. Evidence: the implementation, current
  configuration, a check you ran, or a document with no historical framing that plainly describes "how this
  works."
- **HISTORICAL** — describes a past event, decision or state, and is not claiming to describe today. Evidence:
  a date, a commit hash, a phase/version marker, past tense, a changelog or decision-record entry, an explicit
  "kept as history" note, or a document whose entire genre is historical (a changelog, an ADR, a dated session
  handoff, a point-in-time audit).
- **PLANNED** — describes intended future behavior that does not exist yet. Evidence: a roadmap, a TODO, a
  phase not yet reached, an open issue, explicit "will" / "next" language.
- **DEFERRED** — was intentionally not built or not enabled, by a recorded decision, not by oversight. Evidence:
  a decision record, a feature flag documented as off, an explicit "not doing this and here is why."
- **UNKNOWN** — the claim's age or currency cannot be determined from available evidence. Say so; do not guess
  a state to make a report feel complete.

A single document can mix states. Classify at the level of the specific claim or section, not only the
document as a whole — a session-handoff file commonly has a "current state" block at the top and an explicitly
superseded historical block below it; treat them differently even though they share a file.

### The rule this protects

**Never rewrite a HISTORICAL record merely to make it agree with the current repository.** A past phase
summary, a dated audit, a changelog entry, or a superseded session handoff is a record of what was true then;
editing it to match today falsifies history and destroys the one thing the record was for. Instead:

- Fix the **current** artifact that actually makes the false claim (a live README, an architecture doc with no
  historical framing, an operating contract).
- Leave the **historical** artifact exactly as it is, even if it mentions an old version, an old test count, an
  old branch name, or a superseded architecture — that is what happened, verified against its own stated date
  or commit where possible.
- If a document mixes current and historical content without distinguishing them, do not silently pick one;
  either add an explicit boundary (a note saying where "current" ends and "history" begins, if the task's scope
  includes that document) or flag the ambiguity and ask, rather than rewrite indiscriminately.
- A HISTORICAL claim can still be *wrong even for its own stated time* (for example, a dated audit that
  describes a dependency version the repository had already moved past by that date). That is a factual error
  to correct narrowly — fix only the wrong fact, not the record's framing, date, or surrounding context.

PLANNED and DEFERRED claims are not contradictions to "fix" either: a document correctly describing a feature
as not yet built, or intentionally off, is accurate. Do not implement it, enable it, or edit the document to
claim it is current, unless the user's task is specifically to change that state.

## 2. Source-of-truth hierarchy

Different kinds of claims are controlled by different sources. There is no single answer to "code or docs?" —
identify the claim's type first, then its controlling source:

| Claim type | Controlling source |
|---|---|
| Runtime/behavioral ("how does X work") | The current executable code, plus its tests. |
| Dependency or framework version | Package manifests and lockfiles, not prose that names a version. |
| CI/CD behavior (what runs, what triggers it) | The actual workflow/pipeline configuration. |
| Database structure | Migrations plus the current models/schema, not a schema doc that may have drifted. |
| Security policy, especially an intentional trade-off | The implementation **and** any explicit security
  decision record (ADR or equivalent) that names the trade-off — not code alone, since a deliberate,
  documented exception is not a bug the code should be "corrected" to remove. |
| Current architecture | The implementation **and** current architecture documentation together — prefer
  measurement (imports, route tables) over either one's prose when they disagree. |
| A historical event (what happened, when, why) | The historical record itself (commit history, changelog,
  decision record, dated report) — not a re-derivation from today's code, which cannot see the past. |

When two sources disagree:

1. **Identify the claim** precisely (what exactly is being asserted).
2. **Identify each source** making or implying a position on it.
3. **Determine which source controls this type of claim**, from the table above or by the same reasoning if
   the claim type is not listed.
4. **Classify the contradiction**: a stale current-document (fix the document); a code bug relative to a
   documented, explicit policy (report as a finding, do not silently "fix" the policy or the code without a
   decision); a historical document being factually compared against today (not a contradiction at all — see
   §1); or genuinely unresolved (stop and ask — Stop conditions).
5. **Fix only the incorrect current artifact.** Do not fix the controlling source to match the non-controlling
   one — that direction is exactly backwards and is how, for example, an intentional security trade-off gets
   "corrected" into a regression.

A security or architecture decision that is explicit and current (an ADR, a dated ruling) outranks an
unannotated code reading when the two could be read either way — code without context cannot tell you whether
a given shape is an oversight or a decision. Look for the decision record before assuming either.

## 3. Claim freshness

Treat words like *current*, *latest*, *complete*, *implemented*, *verified*, *production-ready*, and *all
tests pass* as claims to check, not facts to repeat — especially in investigation and reporting, where it is
tempting to quote a document's own self-assessment.

Do not require every document to carry a timestamp, and do not demand re-verification of every such phrase you
encounter — that turns a focused task into an audit of the whole repository. Re-verify only when the claim
**materially affects the task**: a conclusion you are about to state, a decision the user will act on, or a
thing you are about to rely on without checking yourself.

When you do re-verify, state the result as one of:

- **"Historical evidence: …"** — what an older document claims, with its own date/commit if known. Still
  useful context; not current evidence.
- **"Current evidence: …"** — what you yourself observed just now, with the command or file you used.
- **"Not reverified: …"** — you are relying on the document's claim without independently checking it, and you
  are saying so rather than presenting it as your own finding.

A concrete example of the failure this prevents: a document says "10/10 E2E tests pass," dated before many
subsequent commits. That sentence is **historical evidence** about the state at that commit, not **current
evidence** about the repository today. Reporting it as "tests pass" without the distinction manufactures
confidence the investigation never earned. Pair it with an evidence level (`SKILL.md`, "Evidence levels") so
the reader can see exactly how the claim was checked, if at all.
