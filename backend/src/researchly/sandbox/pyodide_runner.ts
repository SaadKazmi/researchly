// Light-cell runner (spec §19.4, ADR 0006). Deno's permission flags, set by light.py, are the sandbox.
//   deno run … pyodide_runner.ts --warm PKG_DIR    fill the package cache (network allowed to the Pyodide CDN)
//   deno run … pyodide_runner.ts PKG_DIR WORK_DIR  run Python from stdin with WORK_DIR mounted at /work;
//                                                  the last stdout line is {"ok", "stdout", "error"}
import { loadPyodide } from "npm:pyodide@314.0.7";

const warm = Deno.args[0] === "--warm";
const [pkgDir, workDir] = warm ? Deno.args.slice(1) : Deno.args;
const out: string[] = [];
const py = await loadPyodide({
  packageCacheDir: pkgDir,
  stdout: (s) => out.push(s),
  stderr: (s) => out.push(s),
});
const quiet = { messageCallback: () => {} };
if (warm) {
  await py.loadPackage(["numpy", "pandas", "matplotlib"], quiet);
  Deno.exit(0);
}
py.mountNodeFS("/work", workDir);
py.FS.chdir("/work");
const code = await new Response(Deno.stdin.readable).text();
let error: string | null = null;
try {
  await py.loadPackagesFromImports(code, quiet);
  await py.runPythonAsync(code);
} catch (e) {
  error = String(e);
}
console.log(JSON.stringify({ ok: error === null, stdout: out.join("\n"), error }));
