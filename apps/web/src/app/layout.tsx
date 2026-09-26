import type { Metadata } from "next";
import { Plus_Jakarta_Sans, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Providers } from "@/lib/providers";

// One family for display and body (docs/20 §6). Two instances only so both existing CSS
// variables (--font-display, --font-body) keep resolving; next/font ships one file.
// next/font needs literal options, so the two instances repeat them.
const display = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-display",
  weight: ["400", "500", "600", "700", "800"],
  display: "swap",
});
const body = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-body",
  weight: ["400", "500", "600", "700", "800"],
  display: "swap",
});
const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "BotForge — Build, deploy & operate AI agents",
  description:
    "An enterprise-grade platform to build knowledge-grounded AI chatbots, wire automations, and run them across every channel.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${display.variable} ${body.variable} ${mono.variable} font-sans`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
