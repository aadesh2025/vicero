"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { ChevronsUpDown, LogOut, Settings, User } from "lucide-react";
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
import { activeOrg, useSession } from "@/lib/store/session";
import { cn, initials } from "@/lib/utils";

/** The signed-in user at the foot of the sidebar: avatar, name and role, opening the account menu. */
export function UserMenu({ collapsed = false }: { collapsed?: boolean }) {
  const router = useRouter();
  const { user, reset } = useSession();
  const role = useSession((s) => activeOrg(s)?.role);

  async function onLogout() {
    await logout();
    reset();
    router.replace("/login");
  }

  if (!user) return null;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label="Account menu"
        className={cn(
          "flex w-full items-center gap-2.5 rounded-[10px] p-1.5 text-left outline-none transition-colors hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring",
          collapsed && "justify-center",
        )}
      >
        <Avatar className="size-8 rounded-full">
          <AvatarFallback className="rounded-full bg-accent-strong text-on-accent">
            {initials(user.full_name, user.email)}
          </AvatarFallback>
        </Avatar>
        {!collapsed && (
          <>
            <span className="flex min-w-0 flex-1 flex-col leading-tight">
              <span className="truncate text-[13px] font-extrabold text-text">{user.full_name ?? "Account"}</span>
              {role && <span className="truncate text-[11.5px] font-semibold capitalize text-faint">{role}</span>}
            </span>
            <ChevronsUpDown className="size-4 shrink-0 text-faint" aria-hidden />
          </>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="top" className="w-[220px]">
        <DropdownMenuLabel className="normal-case tracking-normal">
          <div className="flex flex-col">
            <span className="text-sm font-bold text-text">{user.full_name ?? "Account"}</span>
            <span className="text-xs font-medium text-faint">{user.email}</span>
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
