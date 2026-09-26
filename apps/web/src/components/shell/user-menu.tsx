"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { LogOut, Settings, User } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { logout } from "@/lib/api/auth";
import { useSession } from "@/lib/store/session";
import { initials } from "@/lib/utils";

export function UserMenu() {
  const router = useRouter();
  const { user, reset } = useSession();

  async function onLogout() {
    await logout();
    reset();
    router.replace("/login");
  }

  if (!user) return null;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="rounded-md outline-none ring-offset-2 focus-visible:ring-2 focus-visible:ring-accent">
        <Avatar className="size-8 border border-border">
          <AvatarFallback className="bg-accent-strong text-on-accent">
            {initials(user.full_name, user.email)}
          </AvatarFallback>
        </Avatar>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-[220px]">
        <DropdownMenuLabel className="normal-case tracking-normal">
          <div className="flex flex-col">
            <span className="text-sm font-medium text-text">{user.full_name ?? "Account"}</span>
            <span className="text-xs font-normal text-faint">{user.email}</span>
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/settings/profile">
            <User className="size-4" /> Profile
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href="/settings/org">
            <Settings className="size-4" /> Settings
          </Link>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem className="text-error-text focus:text-error-text" onSelect={onLogout}>
          <LogOut className="size-4" /> Log out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
