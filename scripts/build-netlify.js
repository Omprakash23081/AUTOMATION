const fs = require("node:fs");
const path = require("node:path");

const baseValue = process.env.CUTROOM_API_BASE_URL;
if (!baseValue) {
  throw new Error("Set CUTROOM_API_BASE_URL to the HTTPS origin of the Render API before building for Netlify.");
}

let apiUrl;
try {
  apiUrl = new URL(baseValue);
} catch {
  throw new Error("CUTROOM_API_BASE_URL must be a valid absolute URL.");
}
if (
  !["https:", "http:"].includes(apiUrl.protocol) ||
  (apiUrl.protocol !== "https:" && !["localhost", "127.0.0.1"].includes(apiUrl.hostname)) ||
  apiUrl.username ||
  apiUrl.password ||
  apiUrl.search ||
  apiUrl.hash ||
  (apiUrl.pathname !== "/" && apiUrl.pathname !== "")
) {
  throw new Error("CUTROOM_API_BASE_URL must be an HTTPS origin only (no credentials, path, query, or fragment).");
}

const root = path.resolve(__dirname, "..");
const output = path.join(root, "netlify-dist");
const publicFiles = [
  "index.html",
  "auth.js",
  "app.js",
  "automation.js",
  "job-history.js",
  "editor.js",
  "transcript-search.js",
  "project-actions.js",
  "content-planner.js",
  "media-library.js",
  "all-clips.js",
  "brand-kit.js",
  "storage.js",
  "youtube-dashboard.js",
  "styles.css",
  "production.css",
];

fs.rmSync(output, { recursive: true, force: true });
fs.mkdirSync(output, { recursive: true });
for (const file of publicFiles) {
  fs.copyFileSync(path.join(root, file), path.join(output, file));
}
const configPath = path.join(root, "api-config.js");
const config = fs.readFileSync(configPath, "utf8").replace(
  'window.CUTROOM_API_BASE_URL = "";',
  `window.CUTROOM_API_BASE_URL = ${JSON.stringify(apiUrl.origin)};`,
);
if (config === fs.readFileSync(configPath, "utf8")) {
  throw new Error("Could not inject CUTROOM_API_BASE_URL into api-config.js.");
}
fs.writeFileSync(path.join(output, "api-config.js"), config, { encoding: "utf8" });
