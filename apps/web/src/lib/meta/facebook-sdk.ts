/**
 * Facebook JS SDK glue for one-click Meta connect.
 *
 * Nothing here touches Vicero's API: it only talks to Meta's popup and hands back what Meta gave the
 * browser (an authorization code, or a short-lived user token). Exchanging those for long-lived tokens
 * happens on the server; the browser never sees an access token that outlives the popup.
 */

export interface FbAuthResponse {
  code?: string;
  accessToken?: string;
  grantedScopes?: string;
}

export interface FbLoginResponse {
  authResponse?: FbAuthResponse | null;
  status?: string;
}

export interface FbSdk {
  init(options: { appId: string; cookie?: boolean; xfbml?: boolean; version: string }): void;
  login(callback: (response: FbLoginResponse) => void, options: Record<string, unknown>): void;
}

declare global {
  interface Window {
    FB?: FbSdk;
    fbAsyncInit?: () => void;
  }
}

/** Why a flow stopped; the UI maps each kind to plain-English copy. */
export type MetaFlowErrorKind = "sdk" | "blocked" | "cancelled" | "meta" | "timeout" | "permissions";

export class MetaFlowError extends Error {
  kind: MetaFlowErrorKind;
  constructor(kind: MetaFlowErrorKind, message: string) {
    super(message);
    this.kind = kind;
  }
}

/** Permissions the Messenger + Instagram login asks for (docs/26). `business_management` is not needed. */
export const PAGE_SCOPES = [
  "pages_show_list",
  "pages_messaging",
  "pages_manage_metadata",
  "instagram_basic",
  "instagram_manage_messages",
] as const;

/** A popup that is closed within this long of the click almost certainly never opened. */
const BLOCKED_WITHIN_MS = 800;
/** After the login callback, how long to wait for Meta's separate "which number" message. */
const SIGNUP_EVENT_GRACE_MS = 15_000;

let loading: Promise<FbSdk> | null = null;

/** Load Meta's script once. Call it when the connect UI opens, not at app start, and not from inside the
 *  click: `FB.login` must run synchronously in the click handler or browsers block its popup. */
export function loadFacebookSdk(appId: string, version: string): Promise<FbSdk> {
  if (loading) return loading;
  loading = new Promise<FbSdk>((resolve, reject) => {
    if (typeof window === "undefined") {
      reject(new MetaFlowError("sdk", "Facebook sign-in is only available in the browser."));
      return;
    }
    const ready = () => {
      const fb = window.FB;
      if (!fb) {
        reject(new MetaFlowError("sdk", "Facebook's sign-in script loaded but is unavailable."));
        return;
      }
      fb.init({ appId, cookie: true, xfbml: false, version });
      resolve(fb);
    };
    if (window.FB) {
      ready();
      return;
    }
    window.fbAsyncInit = ready;
    const script = document.createElement("script");
    script.src = "https://connect.facebook.net/en_US/sdk.js";
    script.async = true;
    script.defer = true;
    script.crossOrigin = "anonymous";
    script.onerror = () => {
      loading = null; // allow a retry
      script.remove();
      reject(new MetaFlowError("sdk", "Couldn't load Facebook's sign-in. Check your connection or ad blocker."));
    };
    document.head.appendChild(script);
  });
  return loading;
}

/** Test seam: forget the loaded SDK. */
export function resetFacebookSdkForTests() {
  loading = null;
}

/** Only messages from Meta's own origins are trusted (a page could post any message to this window). */
export function isFacebookOrigin(origin: string): boolean {
  try {
    const { protocol, hostname } = new URL(origin);
    return protocol === "https:" && (hostname === "facebook.com" || hostname.endsWith(".facebook.com"));
  } catch {
    return false;
  }
}

export interface WhatsAppSignupResult {
  code: string;
  waba_id: string;
  phone_number_id: string;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

/**
 * WhatsApp Embedded Signup. Meta reports the outcome through two independent channels: the `FB.login`
 * callback (the authorization code) and a `window` message (which WABA / number the customer picked).
 * They arrive in either order, so both are collected and the promise settles once we hold both.
 * Must be called synchronously from a click handler.
 */
export function startWhatsAppSignup(fb: FbSdk, configId: string): Promise<WhatsAppSignupResult> {
  return new Promise((resolve, reject) => {
    const startedAt = Date.now();
    let code: string | undefined;
    let ids: { waba_id: string; phone_number_id: string } | undefined;
    let settled = false;
    let grace: ReturnType<typeof setTimeout> | undefined;

    const cleanup = () => {
      window.removeEventListener("message", onMessage);
      if (grace) clearTimeout(grace);
    };
    const fail = (error: MetaFlowError) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error);
    };
    const tryFinish = () => {
      if (settled || !code || !ids) return;
      settled = true;
      cleanup();
      resolve({ code, ...ids });
    };

    function onMessage(event: MessageEvent) {
      if (!isFacebookOrigin(event.origin)) return;
      let payload: unknown = event.data;
      if (typeof payload === "string") {
        try {
          payload = JSON.parse(payload);
        } catch {
          return;
        }
      }
      if (!isRecord(payload) || payload.type !== "WA_EMBEDDED_SIGNUP") return;
      const data = isRecord(payload.data) ? payload.data : {};
      if (typeof data.error_message === "string" && data.error_message) {
        fail(new MetaFlowError("meta", data.error_message));
      } else if (payload.event === "CANCEL") {
        fail(new MetaFlowError("cancelled", "You closed WhatsApp sign-up before it finished."));
      } else if (payload.event === "FINISH") {
        if (typeof data.waba_id === "string" && typeof data.phone_number_id === "string") {
          ids = { waba_id: data.waba_id, phone_number_id: data.phone_number_id };
          tryFinish();
        } else {
          fail(new MetaFlowError("meta", "Meta didn't say which phone number was connected."));
        }
      } else if (payload.event === "FINISH_ONLY_WABA") {
        fail(new MetaFlowError("meta", "Sign-up finished without adding a phone number. Add a number and try again."));
      }
    }

    window.addEventListener("message", onMessage);
    fb.login(
      (response) => {
        const authCode = response.authResponse?.code;
        if (!authCode) {
          const quick = Date.now() - startedAt < BLOCKED_WITHIN_MS;
          fail(
            quick
              ? new MetaFlowError("blocked", "The Meta window didn't open. Allow pop-ups for this site and try again.")
              : new MetaFlowError("cancelled", "WhatsApp sign-up was cancelled."),
          );
          return;
        }
        code = authCode;
        tryFinish();
        if (!settled) {
          grace = setTimeout(
            () => fail(new MetaFlowError("timeout", "Meta didn't report which number you connected. Please try again.")),
            SIGNUP_EVENT_GRACE_MS,
          );
        }
      },
      {
        config_id: configId,
        response_type: "code",
        override_default_response_type: true,
        extras: { setup: {}, featureType: "", sessionInfoVersion: "3" },
      },
    );
  });
}

/** Facebook login for Messenger + Instagram. Resolves with a short-lived user token for the server to exchange. */
export function loginForPages(fb: FbSdk): Promise<string> {
  return new Promise((resolve, reject) => {
    const startedAt = Date.now();
    fb.login(
      (response) => {
        const token = response.authResponse?.accessToken;
        if (!token) {
          const quick = Date.now() - startedAt < BLOCKED_WITHIN_MS;
          reject(
            quick
              ? new MetaFlowError("blocked", "The Facebook window didn't open. Allow pop-ups for this site and try again.")
              : new MetaFlowError("cancelled", "Facebook login was cancelled."),
          );
          return;
        }
        const granted = response.authResponse?.grantedScopes;
        if (granted) {
          const have = new Set(granted.split(","));
          const missing = PAGE_SCOPES.filter((s) => !have.has(s));
          if (missing.length > 0) {
            reject(
              new MetaFlowError(
                "permissions",
                `Vicero needs every permission to manage your messages. Missing: ${missing.join(", ")}.`,
              ),
            );
            return;
          }
        }
        resolve(token);
      },
      { scope: PAGE_SCOPES.join(","), return_scopes: true },
    );
  });
}
