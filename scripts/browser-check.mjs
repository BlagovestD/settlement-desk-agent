import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import assert from "node:assert/strict";

const [edge, workspace, capture, initialUrl, python, run, db, demo] = process.argv.slice(2);
const directory = join(workspace, "edge-profile");
await mkdir(capture, { recursive: true });
const browser = spawn(edge, [
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--disable-background-networking", "--window-size=1360,900",
  "--remote-debugging-port=0", `--user-data-dir=${directory}`, "about:blank",
], { stdio: "ignore", windowsHide: true });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(operation, label) {
  let last;
  for (let attempt = 0; attempt < 150; attempt++) {
    try {
      const value = await operation();
      if (value) return value;
    } catch (error) {
      last = error;
    }
    await sleep(100);
  }
  throw new Error(`Timed out waiting for ${label}: ${last ?? ""}`);
}

async function importCorrection(name) {
  await new Promise((resolve, reject) => {
    const child = spawn(python, [run, "import", join(demo, name, "manifest.json"), "--db", db],
      { stdio: ["ignore", "ignore", "inherit"], windowsHide: true });
    child.on("error", reject);
    child.on("exit", (code) => code === 0 ? resolve() : reject(new Error(`Import exited ${code}`)));
  });
}

let socket;
let complete = false;
try {
  const port = await until(async () => (await readFile(join(directory, "DevToolsActivePort"), "utf8")).split("\n")[0]?.trim(), "Edge DevTools");
  const target = await until(async () => {
    const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    return list.find((item) => item.type === "page");
  }, "browser page");
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let serial = 0;
  const pending = new Map();
  socket.addEventListener("message", ({ data }) => {
    const { id, result, error } = JSON.parse(data);
    if (!pending.has(id)) return;
    const { resolve, reject } = pending.get(id);
    pending.delete(id);
    if (error) reject(error);
    else resolve(result);
  });
  const cdp = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++serial;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1360, height: 880, deviceScaleFactor: 1, mobile: false });
  const evaluate = async (expression) => {
    const response = await cdp("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.text);
    return response.result.value;
  };
  const text = (term) => until(
    () => evaluate(`document.body?.innerText.includes(${JSON.stringify(term)})`), term);
  const navigate = async (path, marker) => {
    await cdp("Page.navigate", { url: new URL(path, initialUrl).href });
    await text(marker);
  };
  const click = (selector) => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const captureFrame = async (name) => {
    await sleep(200);
    const frame = await cdp("Page.captureScreenshot", { format: "png", fromSurface: true });
    await writeFile(join(capture, `${name}.png`), Buffer.from(frame.data, "base64"));
  };

  await navigate("/", "Daily close");
  assert.equal(await evaluate("document.querySelectorAll('tbody .case-link').length"), 2);
  await captureFrame("01-queue");
  await click("a[href='/cases/1']");
  await text("−$150.00");
  await captureFrame("02-refund");
  await click("form[action='/investigate'] button");
  await text("Missing ledger refund");
  await captureFrame("03-finding");
  await click(".detail-grid section .source-links a");
  await text("SHA-256");
  await captureFrame("04-source");
  await click("main a[href='/cases/1']");
  await text("Missing ledger refund");
  await click("button[value='accept']");
  await text("Explained");
  await captureFrame("05-explained");

  await navigate("/cases/2", "Unknown");
  await click("form[action='/investigate'] button");
  await text("Request bank trace");
  await click("button[value='request']");
  await text("Evidence requested");
  await captureFrame("06-ambiguous");

  await importCorrection("03-corrected-ledger");
  await importCorrection("04-bank-trace");
  await navigate("/", "Daily close");
  assert.equal(await evaluate("document.querySelectorAll('tbody .badge.reconciled').length"), 2);
  await captureFrame("07-reconciled");
  await click("a[href='/cases/3']");
  await text("Completed");
  await captureFrame("08-history");

  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await navigate("/cases/1", "Missing ledger refund");
  assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
  await captureFrame("09-mobile");
  complete = true;
  socket.send(JSON.stringify({ id: ++serial, method: "Browser.close" }));
} finally {
  if (complete && browser.exitCode === null) {
    await Promise.race([new Promise((resolve) => browser.once("exit", resolve)), sleep(2500)]);
  }
  socket?.close();
  if (browser.exitCode === null) browser.kill();
  await sleep(300);
}
