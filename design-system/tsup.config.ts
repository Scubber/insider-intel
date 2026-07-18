import { defineConfig } from "tsup";

export default defineConfig({
  entry: ["src/index.ts"],
  format: ["esm"],
  dts: true,
  clean: true,
  sourcemap: false,
  // styles.css is imported from index.ts; esbuild emits it as dist/index.css
  loader: { ".css": "css" },
});
