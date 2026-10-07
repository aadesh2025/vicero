"use client";

// One refresh at a time per browser, across every open tab of this origin.
//
// A refresh token can be spent once. With two tabs (or a reload racing a retry) both holding an
// expired access token, both would call the refresh endpoint with the same token: the loser's
// failure then looked like a logout. So refreshes take turns here, and a tab that waited re-reads the
// shared access-token cookie afterwards and reuses what the winner stored instead of refreshing again.

const LOCK_NAME = "vicero-auth-refresh";
const LEASE_KEY = "vicero:refresh-lease";
const CHANNEL_NAME = "vicero-refresh";
const LEASE_MS = 15_000;
// A tab that died holding the lease must not wedge sign-in forever: after this long, go ahead anyway.
const MAX_WAIT_MS = 20_000;

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Run `fn` while holding the cross-tab refresh lock. */
export async function withRefreshLock<T>(fn: () => Promise<T>): Promise<T> {
  const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
  if (locks && typeof locks.request === "function") {
    // Web Locks: queued FIFO by the browser, released automatically if the tab dies.
    return locks.request(LOCK_NAME, () => fn());
  }
  return withLeaseLock(fn);
}

interface Lease {
  id: string;
  exp: number;
}

function readLease(): Lease | null {
  try {
    const raw = localStorage.getItem(LEASE_KEY);
    return raw ? (JSON.parse(raw) as Lease) : null;
  } catch {
    return null;
  }
}

function writeLease(lease: Lease) {
  try {
    localStorage.setItem(LEASE_KEY, JSON.stringify(lease));
  } catch {
    /* storage blocked: we still run, just without mutual exclusion */
  }
}

function dropLease(id: string) {
  try {
    if (readLease()?.id === id) localStorage.removeItem(LEASE_KEY);
  } catch {
    /* ignore */
  }
}

/** Resolve when another tab says it released, or after `ms`, whichever is first. */
function waitForRelease(channel: BroadcastChannel | null, ms: number): Promise<void> {
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      channel?.removeEventListener("message", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    channel?.addEventListener("message", done);
  });
}

/** Fallback for browsers without Web Locks: a short-lived lease in localStorage, with a
 * BroadcastChannel to wake waiters early (they also poll, so it works without one). */
async function withLeaseLock<T>(fn: () => Promise<T>): Promise<T> {
  const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel(CHANNEL_NAME) : null;
  const deadline = Date.now() + MAX_WAIT_MS;
  try {
    for (;;) {
      const lease = readLease();
      if (!lease || lease.exp < Date.now()) {
        writeLease({ id, exp: Date.now() + LEASE_MS });
        await sleep(25); // let a tab that wrote at the same instant land its write; the last writer wins
        if (readLease()?.id === id) break;
      }
      if (Date.now() >= deadline) break;
      await waitForRelease(channel, 250);
    }
    return await fn();
  } finally {
    dropLease(id);
    channel?.postMessage("released");
    channel?.close();
  }
}
