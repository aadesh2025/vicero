// The widget is vanilla JS with no runtime dependencies. This build minifies it with esbuild (a
// devDependency of this package only: `npm ci` in packages/widget) and writes it to dist/ and to the
// web app's public/ dir (served at /widget.js). It fails loudly if esbuild is missing, so an
// unminified bundle can never be shipped by accident.
import { readFileSync, writeFileSync, mkdirSync, copyFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const srcJs = resolve(here, "src/widget.js");
const srcCss = resolve(here, "src/widget.css");
const distDir = resolve(here, "dist");
const webPublic = resolve(here, "../../apps/web/public");

mkdirSync(distDir, { recursive: true });
mkdirSync(webPublic, { recursive: true });

let js = readFileSync(srcJs, "utf8");
let esbuild;
try {
  esbuild = await import("esbuild");
} catch {
  console.error("[widget] esbuild is not installed. Run `npm ci` in packages/widget, then build again.");
  process.exit(1);
}
js = (await esbuild.transform(js, { minify: true, loader: "js", legalComments: "inline" })).code;
console.log("[widget] minified with esbuild");

writeFileSync(resolve(distDir, "widget.js"), js);
writeFileSync(resolve(webPublic, "widget.js"), js);
copyFileSync(srcCss, resolve(distDir, "widget.css"));
copyFileSync(srcCss, resolve(webPublic, "widget.css"));
console.log("[widget] wrote dist/ and apps/web/public/{widget.js,widget.css}");
