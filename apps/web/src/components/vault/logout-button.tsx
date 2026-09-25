"use client";

import { useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";

export function VaultLogoutButton() {
  const router = useRouter();

  async function signOut() {
    await fetch("/api/vault/logout", { method: "POST" }).catch(() => {});
    router.replace("/vault/login");
    router.refresh();
  }

  return (
    <Button variant="ghost" size="sm" onClick={signOut}>
      <LogOut /> Sign out
    </Button>
  );
}
