/** Why a conversation is in the handoff queue, in words an owner can act on.
 *
 * `plan_limit` is written by the server when the plan (trial over / messages spent) stopped the
 * agent from answering a visitor (docs/18 §8) — a different situation from a visitor asking for a
 * person, so it gets its own wording. Every other reason is shown as it always was.
 */
export const PLAN_LIMIT_REASON = "plan_limit";

export function isPlanLimitReason(reason: string | null | undefined): boolean {
  return reason === PLAN_LIMIT_REASON;
}

export function handoffReasonLabel(reason: string): string {
  return isPlanLimitReason(reason) ? "agent couldn't reply (plan limit)" : reason;
}
