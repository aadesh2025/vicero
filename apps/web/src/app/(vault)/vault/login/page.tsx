import { redirect } from "next/navigation";
import { VaultLoginForm } from "@/components/vault/login-form";
import { isConfigured } from "@/lib/vault/config";
import { getVaultSession } from "@/lib/vault/session";

export const dynamic = "force-dynamic";
export const metadata = { title: "Sign in" };

export default async function VaultLoginPage() {
  if (await getVaultSession()) redirect("/vault");
  const configured = isConfigured();

  return (
    <main className="mx-auto grid min-h-[70vh] max-w-sm place-items-center px-4">
      <div className="w-full rounded-xl border border-border bg-surface p-6 shadow-pop">
        <h1 className="font-display text-xl font-semibold text-text">Private area</h1>
        <p className="mt-1 mb-5 text-sm text-muted">
          Restricted to the platform administrator. This sign-in is separate from your BotForge account.
        </p>
        {configured ? (
          <VaultLoginForm />
        ) : (
          // Naming the missing variables is fine — this tells the operator what to do, and
          // an outsider learns only that the door is locked, which the form would say anyway.
          <p role="status" className="rounded-md border border-warn/40 bg-surface-2 px-3 py-2 text-sm text-muted">
            Not set up on this server. Set <code>VAULT_ADMIN_EMAILS</code>, <code>VAULT_PASSWORD_HASH</code> and{" "}
            <code>VAULT_SESSION_SECRET</code>, then restart the web app. See <code>docs/ENV.md</code>.
          </p>
        )}
      </div>
    </main>
  );
}
