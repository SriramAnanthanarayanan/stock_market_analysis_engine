// Frontend unit tests (pure TypeScript modules) with Node's built-in test runner.
//   node tests/run-unit.mjs        (or: npm test)
// Each src module under test is transpiled with the project's TypeScript compiler
// into a temporary .mjs file, so no extra test framework is needed.
import { mkdtempSync, readFileSync, writeFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const out = mkdtempSync(join(tmpdir(), "pas-unit-"));
const cache = new Map();

/** Transpile src/<rel>.ts (and its relative imports) and return a file URL to import. */
export function load(rel) {
  const src = resolve(here, "..", "src", rel.endsWith(".ts") ? rel : `${rel}.ts`);
  if (cache.has(src)) return cache.get(src);
  const code = readFileSync(src, "utf8");
  let js = ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
  js = js.replace(/from\s+["'](\.{1,2}\/[^"']+)["']/g, (m, p) => {
    const dep = resolve(dirname(src), p).slice(resolve(here, "..", "src").length + 1);
    return `from "${load(dep)}"`;
  });
  const file = join(out, rel.replace(/[\\/]/g, "__") + ".mjs");
  writeFileSync(file, js);
  const url = pathToFileURL(file).href;
  cache.set(src, url);
  return url;
}

globalThis.__pasLoad = load;
const files = readdirSync(here).filter((f) => f.endsWith(".test.mjs"));
for (const f of files) await import(pathToFileURL(join(here, f)).href);
