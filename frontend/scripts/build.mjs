import { cp, mkdir, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const source = new URL("../public/", import.meta.url);
const target = new URL("../dist/", import.meta.url);
await mkdir(target, { recursive: true });
for (const file of await readdir(source)) {
  if (file.endsWith(".js")) {
    const check = spawnSync(process.execPath, ["--check", fileURLToPath(new URL(file, source))], { stdio: "inherit" });
    if (check.status !== 0) process.exit(1);
  }
  await cp(new URL(file, source), new URL(file, target));
}
console.log("Production assets: frontend/dist (native ES modules; no transpilation).");
