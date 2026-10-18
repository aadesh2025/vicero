import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

// ESLint 9 flat config. `next lint` was removed in Next 16; eslint-config-next 16
// ships native flat-config arrays, so we import them directly and run the ESLint CLI.
const eslintConfig = [
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "out/**",
      "playwright-report/**",
      "test-results/**",
      "public/widget.js", // built artifact from packages/widget, not source
      "e2e/fixtures/**", // captured bundles and test data, not source
    ],
  },
  ...nextCoreWebVitals,
  ...nextTypescript,
];

export default eslintConfig;
