"use client";

import * as React from "react";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cn } from "@/lib/utils";

const Tabs = TabsPrimitive.Root;

// Segmented control: a `surface-3` track with the active tab lifted as a pill. Callers that
// want a different layout (a full-width bar, an underline) override via `className`.
const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    className={cn("flex items-center gap-0.5 overflow-x-auto rounded-[10px] bg-surface-3 p-[3px] no-scrollbar", className)}
    {...props}
  />
));
TabsList.displayName = TabsPrimitive.List.displayName;

// Weight is 700 in both states: the spec's 800-when-active makes a segmented control jitter
// sideways as the selection moves. The pill and the text colour carry "active".
const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(
      "relative whitespace-nowrap rounded-lg px-3 py-1.5 text-[13px] font-bold text-muted transition-colors outline-none",
      "hover:text-text focus-visible:ring-2 focus-visible:ring-ring",
      "data-[state=active]:bg-surface data-[state=active]:text-text data-[state=active]:shadow-card dark:data-[state=active]:bg-surface-2",
      className,
    )}
    {...props}
  />
));
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName;

const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn("outline-none focus-visible:outline-none animate-fade-up", className)}
    {...props}
  />
));
TabsContent.displayName = TabsPrimitive.Content.displayName;

export { Tabs, TabsList, TabsTrigger, TabsContent };
