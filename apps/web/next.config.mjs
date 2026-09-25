import { fileURLToPath } from "url";

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Emit a self-contained server bundle for a small production image (infra/apps/web Dockerfile).
  output: "standalone",
  // Pin the workspace root to this app so Next/Turbopack never infers it from a stray
  // package-lock.json further up the tree (the repo root has no package.json).
  turbopack: {
    root: fileURLToPath(new URL(".", import.meta.url)),
  },
  // The docs site's MDX toolchain. Left to itself Turbopack treats these as external ESM
  // packages and links each one into `.next/node_modules` with a junction — which on
  // Windows fails the whole build with "The file exists (os error 80)" on any cold `.next`,
  // because the same junction is requested twice and creating it is not idempotent.
  // Transpiling them bundles them instead, so no junction is ever created.
  transpilePackages: ["next-mdx-remote", "shiki", "@shikijs/rehype"],
};

export default nextConfig;
