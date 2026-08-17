const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const root = process.cwd();
const staticRoot = path.join(root, "dist", "client");
const port = Number(process.env.PORT || 3000);
const targetPort = Number(process.env.PROXY_TARGET_PORT || 3005);

const types = {
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
};

function sendStatic(req, res, pathname) {
  const decoded = decodeURIComponent(pathname).replace(/^\/+/, "");
  const filePath = path.normalize(path.join(staticRoot, decoded));
  if (!filePath.startsWith(staticRoot)) {
    res.writeHead(403);
    res.end("Forbidden");
    return true;
  }

  if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    return false;
  }

  const ext = path.extname(filePath).toLowerCase();
  res.writeHead(200, {
    "content-type": types[ext] || "application/octet-stream",
    "cache-control": "public, max-age=31536000, immutable",
  });
  fs.createReadStream(filePath).pipe(res);
  return true;
}

function proxy(req, res) {
  const upstream = http.request(
    {
      hostname: "127.0.0.1",
      port: targetPort,
      path: req.url,
      method: req.method,
      headers: req.headers,
    },
    (upstreamRes) => {
      res.writeHead(upstreamRes.statusCode || 500, upstreamRes.headers);
      upstreamRes.pipe(res);
    },
  );

  upstream.on("error", (error) => {
    res.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
    res.end(`Frontend upstream is not ready: ${error.message}`);
  });

  req.pipe(upstream);
}

http
  .createServer((req, res) => {
    const url = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);
    if (url.pathname.startsWith("/assets/") && sendStatic(req, res, url.pathname)) {
      return;
    }
    proxy(req, res);
  })
  .listen(port, "0.0.0.0", () => {
    console.log(`static proxy listening on ${port}, vinext upstream ${targetPort}`);
  });
