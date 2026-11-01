import fs from "node:fs";

const configPath = "src/Config.ts";
let config = fs.readFileSync(configPath, "utf8");
for (const [before, after] of [
  ["export let BYOS_ENABLED = false;", "export let BYOS_ENABLED = process.env.BYOS_ENABLED === 'true';"],
  ["export const TIMEZONE = 'Europe/Warsaw';", "export const TIMEZONE = process.env.TZ || 'UTC';"],
  ["export const ALLOW_FIRMWARE_UPDATE = true;", "export const ALLOW_FIRMWARE_UPDATE = false;"],
]) {
  if (!config.includes(before)) throw new Error(`Upstream configuration changed: ${before}`);
  config = config.replace(before, after);
}
fs.writeFileSync(configPath, config);
const serverPath = "src/Server.ts";
let server = fs.readFileSync(serverPath, "utf8");
const startupLog = "console.log(`Server started. Check it http://127.0.0.1:${SERVER_PORT + ROUTE_IMAGE}?secret_key=... OR ${SCREEN_URL}`);";
if (!server.includes(startupLog) || !server.includes("checkImageUrl(SCREEN_URL);")) {
  throw new Error("Upstream startup logging changed");
}
server = server.replace(startupLog, "console.log('Node Lite trial server ready');");
server = server.replace("checkImageUrl(SCREEN_URL);", "// Skip the startup self-request: it races readiness and logs the image key.");
fs.writeFileSync(serverPath, server);
