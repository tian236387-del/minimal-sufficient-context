import fs from "node:fs";
import path from "node:path";


function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 2) {
    args[argv[index].replace(/^--/, "")] = argv[index + 1];
  }
  return args;
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

const args = parseArgs(process.argv.slice(2));
const debugPort = Number(args["debug-port"] || 9223);
const pageUrl = args.url;
const sessionId = args.session;
const outputDir = path.resolve(args["output-dir"] || "visual-artifacts");

if (!pageUrl || !sessionId) {
  throw new Error("--url and --session are required");
}

const targets = await fetch(`http://127.0.0.1:${debugPort}/json/list`).then((response) => response.json());
const target = targets.find((item) => item.type === "page");
if (!target) throw new Error("No browser page target found");

const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", reject, { once: true });
});

let nextId = 1;
const pending = new Map();
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id) return;
  const request = pending.get(message.id);
  if (!request) return;
  pending.delete(message.id);
  if (message.error) request.reject(new Error(message.error.message));
  else request.resolve(message.result || {});
});

function command(method, params = {}) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const result = await command("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result.value;
}

async function waitForWorkspace(timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ready = await evaluate(
      "document.readyState === 'complete' && !document.querySelector('#workspace-view')?.hidden",
    );
    if (ready) return;
    await sleep(100);
  }
  throw new Error("Workspace did not become ready");
}

async function capture(name, width, height, mobile) {
  await command("Emulation.setDeviceMetricsOverride", {
    width,
    height,
    deviceScaleFactor: 1,
    mobile,
  });
  await command("Page.navigate", { url: pageUrl });
  await sleep(300);
  await evaluate(
    `localStorage.setItem('msc-human-study-session', ${JSON.stringify(sessionId)}); location.reload()`,
  );
  await waitForWorkspace();
  await sleep(300);

  const metrics = await evaluate(`(() => {
    const visible = (element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    };
    const overflow = [...document.querySelectorAll('body *')]
      .filter((element) => {
        if (!visible(element)) return false;
        const rect = element.getBoundingClientRect();
        return rect.left < -1 || rect.right > window.innerWidth + 1;
      })
      .slice(0, 20)
      .map((element) => ({
        tag: element.tagName,
        id: element.id,
        className: String(element.className || ''),
        left: Math.round(element.getBoundingClientRect().left),
        right: Math.round(element.getBoundingClientRect().right),
      }));
    return {
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      scrollWidth: document.documentElement.scrollWidth,
      scrollHeight: document.documentElement.scrollHeight,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth,
      overflow,
    };
  })()`);
  const screenshot = await command("Page.captureScreenshot", {
    format: "png",
    fromSurface: true,
    captureBeyondViewport: true,
  });
  fs.writeFileSync(path.join(outputDir, `${name}.png`), Buffer.from(screenshot.data, "base64"));
  return metrics;
}

fs.mkdirSync(outputDir, { recursive: true });
await command("Page.enable");
await command("Runtime.enable");
const results = {
  desktop: await capture("workspace-desktop", 1440, 1000, false),
  mobile: await capture("workspace-mobile", 390, 844, true),
};
fs.writeFileSync(path.join(outputDir, "metrics.json"), JSON.stringify(results, null, 2));
socket.close();
console.log(JSON.stringify(results, null, 2));
