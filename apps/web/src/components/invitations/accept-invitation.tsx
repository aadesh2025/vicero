"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertTriangle, Loader2, MailX, UserX } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { acceptInvitation, listOrgs, previewInvitation, type InvitationPreview } from "@/lib/api/orgs";
import { login, me, signup } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { clearAuth, getAccessToken, setActiveOrgId } from "@/lib/api/tokens";
import { useSession } from "@/lib/store/session";

/** The three failures the server actually models, each worth its own explanation. */
const FAILURES: Record<string, { icon: typeof MailX; title: string; detail: string }> = {
  "org.invitation_invalid": {
    icon: MailX,
    title: "This invitation has expired",
    detail:
      "Invitations are valid for 7 days, and each one can only be used once. Ask whoever invited you to send a new one.",
  },
  "org.invite_email_mismatch": {
    icon: UserX,
    title: "This invitation is for a different email",
    detail:
      "You're signed in with an account the invitation wasn't sent to. Sign out and use the address the email was sent to.",
  },
  "org.not_found": {
    icon: AlertTriangle,
    title: "That organization no longer exists",
    detail: "It looks like it was deleted after the invitation was sent.",
  },
};

type Mode = "login" | "signup";
type Status = "working" | "needs-account" | "failed";

/** Whether there's a stored session, read without breaking hydration.
 *
 * Cookies don't exist on the server, so reading them while deriving initial state renders one
 * tree on the server and a different one on the client — the mismatch React reports as
 * "server/client branch `if (typeof window !== 'undefined')`". `useSyncExternalStore` uses the
 * *server* snapshot for the first client render too, so hydration matches, and React re-renders
 * with the real value immediately afterwards. `unknown` is what both sides agree on first.
 */
const NEVER_CHANGES = () => () => {};
function useStoredSession(): "in" | "out" | "unknown" {
  return useSyncExternalStore(
    NEVER_CHANGES,
    () => (getAccessToken() ? "in" : "out"),
    () => "unknown",
  );
}

export function AcceptInvitation() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const setSession = useSession((s) => s.setSession);

  const session = useStoredSession();
  // Transitions only — a failed redeem, or signing out to use another account. Until one of
  // those happens the status is derived, so it can never disagree with the session cookie.
  const [override, setOverride] = useState<Status | null>(null);
  const status: Status =
    override ??
    (session === "unknown"
      ? "working" // both sides render the spinner until the client can read cookies
      : !token
        ? "failed"
        : session === "in"
          ? "working"
          : "needs-account");
  const setStatus = setOverride;
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("signup");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [busy, setBusy] = useState(false);
  // What the invitation says, read without redeeming it. Null while loading, or if the preview
  // failed — in which case the form still works, just without the org name and a locked email.
  const [preview, setPreview] = useState<InvitationPreview | null>(null);
  // A React 18 double-mount in dev would otherwise burn the single-use token.
  const attempted = useRef(false);

  // Fetch regardless of whether there's a session: with one it names the invited address in the
  // mismatch case, without one it decides sign-in vs signup before the user types anything.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    previewInvitation(token)
      .then((p) => {
        if (cancelled) return;
        setPreview(p);
        setEmail(p.email);
        setMode(p.account_exists ? "login" : "signup");
      })
      .catch(() => {
        /* The form falls back to asking for the address; the token is still what matters. */
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  /** Redeem the token with whatever session is current, then land in the new org. */
  async function accept(): Promise<boolean> {
    try {
      const org = await acceptInvitation(token);
      // Make the org they just joined the active one, so /dashboard opens on it.
      setActiveOrgId(org.id);
      const [profile, orgs] = await Promise.all([me(), listOrgs()]);
      setSession(profile.user, orgs.length ? orgs : [org], org.id);
      router.replace("/dashboard");
      return true;
    } catch (e) {
      const code = e instanceof ApiError ? e.code : null;
      setErrorCode(code);
      setStatus("failed");
      return false;
    }
  }

  // Redeem on load when there's already a session. `session === "in"` rather than
  // `status === "working"`: the status is also "working" before the client has read cookies,
  // and redeeming then would spend the single-use token on an unauthenticated request. The ref
  // guards against React's double-mount in dev burning it.
  useEffect(() => {
    if (attempted.current || session !== "in" || override !== null || !token) return;
    attempted.current = true;
    void accept();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, session, override]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFormError(null);
    setNotice(null);
    try {
      if (mode === "signup") {
        await signup(email.trim(), password, fullName.trim() || undefined);
      } else {
        await login(email.trim(), password);
      }
      // Straight on to accepting — don't drop them on the dashboard having silently
      // failed to join the org they were invited to.
      await accept();
    } catch (err) {
      // Belt to the preview's braces: the address may have been registered since the preview,
      // or the preview may not have loaded at all. Either way "an account already exists" is a
      // fact about which form to show, not a failure to report at someone.
      if (err instanceof ApiError && err.code === "auth.email_taken") {
        setMode("login");
        setNotice("You already have an account. Enter your password to sign in and join.");
      } else {
        setFormError(err instanceof Error ? err.message : "Something went wrong. Try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  /** Drop the current session and come back to this same invitation. */
  function useAnotherAccount() {
    clearAuth();
    attempted.current = false;
    setErrorCode(null);
    setFormError(null);
    setNotice(null);
    setMode(preview?.account_exists ? "login" : "signup");
    setStatus("needs-account");
  }

  if (status === "working") {
    return (
      <div className="flex items-center gap-3 text-sm text-muted">
        <Loader2 className="size-4 animate-spin text-accent" />
        Joining…
      </div>
    );
  }

  if (status === "failed") {
    // A missing token is an unusable invitation, same as an expired one — that's the honest
    // explanation, and there is no server error to report because nothing was ever sent.
    const code = errorCode ?? (!token ? "org.invitation_invalid" : "");
    const failure = FAILURES[code] ?? {
      icon: AlertTriangle,
      title: "We couldn't accept this invitation",
      detail: "Please ask whoever invited you to send a new one.",
    };
    const Icon = failure.icon;
    const mismatch = errorCode === "org.invite_email_mismatch";
    return (
      <div className="rounded-lg border border-border bg-surface p-6">
        <Icon className="mb-3 size-6 text-warn-text" aria-hidden />
        <h1 className="font-display text-lg font-semibold text-text">{failure.title}</h1>
        <p className="mt-2 text-sm text-muted">{failure.detail}</p>
        {mismatch && preview && (
          <p className="mt-2 text-sm text-muted">
            It was sent to <span className="font-medium text-text">{preview.email}</span>.
          </p>
        )}
        <div className="mt-4 flex flex-wrap gap-2">
          {/* Signing out is the actual remedy for a mismatch, so offer it rather than sending
              them to a sign-in page that keeps the wrong session and loops straight back. */}
          {mismatch && (
            <Button variant="primary" onClick={useAnotherAccount}>
              Use a different account
            </Button>
          )}
          <Button variant="outline" onClick={() => router.push("/login")}>
            Go to sign in
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-surface p-6">
      <h1 className="font-display text-lg font-semibold text-text">You&rsquo;ve been invited</h1>
      {preview ? (
        <p className="mt-1 text-sm text-muted">
          Join <span className="font-medium text-text">{preview.organization_name}</span> as{" "}
          <span className="font-medium text-text">{preview.role}</span>.{" "}
          {mode === "login"
            ? "Sign in with your existing password."
            : "Choose a password to create your account."}
        </p>
      ) : (
        <p className="mt-1 text-sm text-muted">
          {mode === "signup"
            ? "Create your account with the email the invitation was sent to."
            : "Sign in with the email the invitation was sent to."}
        </p>
      )}

      <form onSubmit={onSubmit} className="mt-5 space-y-3">
        {mode === "signup" && (
          <div>
            <label htmlFor="invite-name" className="mb-1 block text-xs text-muted">
              Your name
            </label>
            <Input
              id="invite-name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Aadesh Kumar"
            />
          </div>
        )}
        <div>
          <label htmlFor="invite-email" className="mb-1 block text-xs text-muted">
            Email
          </label>
          {/* Locked to the invited address when we know it: the server rejects anything else
              with `org.invite_email_mismatch`, so letting it be edited only invites that error. */}
          <Input
            id="invite-email"
            type="email"
            autoComplete="email"
            required
            readOnly={preview !== null}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
            className={preview !== null ? "text-muted" : undefined}
          />
        </div>
        <div>
          <label htmlFor="invite-password" className="mb-1 block text-xs text-muted">
            Password
          </label>
          <Input
            id="invite-password"
            type="password"
            autoComplete={mode === "signup" ? "new-password" : "current-password"}
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>

        {notice && <p className="text-sm text-accent">{notice}</p>}
        {formError && <p className="text-sm text-error-text">{formError}</p>}

        <Button type="submit" variant="primary" className="w-full" disabled={busy}>
          {busy && <Loader2 className="size-4 animate-spin" />}
          {mode === "signup" ? "Create account & join" : "Sign in & join"}
        </Button>
      </form>

      <button
        type="button"
        onClick={() => {
          setMode(mode === "signup" ? "login" : "signup");
          setFormError(null);
          setNotice(null);
        }}
        className="mt-4 text-xs text-muted underline hover:text-text"
      >
        {mode === "signup" ? "I already have an account" : "I need to create an account"}
      </button>
    </div>
  );
}
