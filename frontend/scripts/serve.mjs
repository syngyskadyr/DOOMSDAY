import { createServer } from "node:http";
import { readFile } from "node:fs/promises";

// API_BASE_URL is the backend origin, without /api. Never a model endpoint/key.
const backend = new URL(process.env.API_BASE_URL || "http://127.0.0.1:8000");
if (!["http:", "https:"].includes(backend.protocol) || backend.username || backend.password || backend.search || backend.hash || backend.pathname !== "/") {
  throw new Error("API_BASE_URL must be an http(s) origin without credentials, path or query.");
}
const root = new URL(process.argv.includes("--dist") ? "../dist/" : "../public/", import.meta.url);
const mime = { html: "text/html; charset=utf-8", js: "text/javascript; charset=utf-8", css: "text/css; charset=utf-8", svg: "image/svg+xml" };
const port = Number(process.env.PORT || 5173);

createServer(async (request, response) => {
  try {
    const url = new URL(request.url, "http://localhost");
    if (url.pathname.startsWith("/api/")) {
      const chunks = [];
      let size = 0;
      for await (const chunk of request) {
        size += chunk.length;
        if (size > 20 * 1024 * 1024) { response.writeHead(413).end(); return; }
        chunks.push(chunk);
      }
      const upstream = await fetch(new URL(url.pathname + url.search, backend), {
        method: request.method,
        headers: { Accept: "application/json", "Content-Type": request.headers["content-type"] || "application/json" },
        body: ["GET", "HEAD"].includes(request.method) ? undefined : Buffer.concat(chunks),
        signal: AbortSignal.timeout(19000), redirect: "error",
      });
      response.writeHead(upstream.status, { "Content-Type": upstream.headers.get("content-type") || "application/json", "Cache-Control": "no-store" });
      response.end(Buffer.from(await upstream.arrayBuffer()));
      return;
    }
    if (!["GET", "HEAD"].includes(request.method)) { response.writeHead(405).end(); return; }
    const name = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
    // Serve only flat public assets, never repository files or hidden files.
    if (!/^[a-zA-Z0-9][a-zA-Z0-9.-]*\.(html|js|css|svg)$/.test(name)) { response.writeHead(404).end(); return; }
    const content = await readFile(new URL(name, root));
    response.writeHead(200, { "Content-Type": mime[name.split(".").at(-1)], "Cache-Control": "no-cache" });
    response.end(request.method === "HEAD" ? undefined : content);
  } catch (error) {
    if (response.headersSent) { response.end(); return; }
    if (error.code === "ENOENT") { response.writeHead(404).end(); return; }
    response.writeHead(502, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ error: { code: "BACKEND_UNAVAILABLE", message: "Нет связи с backend. Проверьте API_BASE_URL и запущенный сервер." } }));
  }
}).listen(port, "127.0.0.1", () => console.log(`DOOMSDAY: http://127.0.0.1:${port} → ${backend.origin}/api`));
