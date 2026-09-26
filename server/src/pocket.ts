import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createInterface } from "node:readline";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { join } from "node:path";
import { Router } from "express";
import { dataDir } from "./db.js";

type Snapshot = {
  mode: string;
  status: string;
  receivedAt: number;
  markets: Array<{ symbol: string; name: string; group: string }>;
  liveSymbols: string[];
  ticks: Record<string, { price: number; time: number }>;
  candles: Record<string, Array<Record<string, number>>>;
  trends: Record<string, { direction: string }>;
};
type Pending = { resolve: (snapshot: Snapshot) => void; reject: (error: Error) => void };

const root = join(dataDir, "..");
const ssidFile = join(dataDir, ".pocket-ssid");
const router = Router();
let worker: ChildProcessWithoutNullStreams | undefined;
let workerExit: Promise<void> | undefined;
let starting: Promise<void> | undefined;
let sequence = 0;
const pending = new Map<number, Pending>();
let browserLogin: Promise<string> | undefined;

function pythonPath(): string {
  return process.env.POCKET_PYTHON ?? "E:\\JANGAN DI HAPUS\\OpenTerminal\\server\\.venv\\Scripts\\python.exe";
}

function browserPythonPath(): string {
  return process.env.POCKET_BROWSER_PYTHON ?? "C:\\Users\\User\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe";
}

function rejectPending(error: Error): void {
  for (const request of pending.values()) request.reject(error);
  pending.clear();
}

function startWorker(): Promise<void> {
  if (worker && !worker.killed) return Promise.resolve();
  if (starting) return starting;
  if (!existsSync(ssidFile)) return Promise.reject(new Error("DEMO SSID belum dikonfigurasi"));
  starting = new Promise<void>((resolve, reject) => {
    const child = spawn(pythonPath(), [join(root, "server", "pocket_feed.py")], {
      cwd: root,
      env: { ...process.env, POCKET_SSID_FILE: ssidFile, PYTHONPATH: join(root, "server") },
      stdio: "pipe",
      windowsHide: true,
    });
    worker = child;
    createInterface({ input: child.stdout }).on("line", (line) => {
      try {
        const message = JSON.parse(line) as { type?: string; id?: number; ok?: boolean; snapshot?: Snapshot; error?: string };
        if (message.type === "ready") return resolve();
        if (message.type === "error") return reject(new Error(message.error ?? "Pocket worker error"));
        if (typeof message.id !== "number") return;
        const request = pending.get(message.id);
        if (!request) return;
        pending.delete(message.id);
        if (message.ok && message.snapshot) request.resolve(message.snapshot);
        else request.reject(new Error("Pocket worker returned no snapshot"));
      } catch {
        reject(new Error("Pocket worker returned malformed data"));
      }
    });
    child.stderr.on("data", (data) => process.stderr.write(`[pocket] ${String(data)}`));
    workerExit = new Promise((resolve) => child.once("exit", () => resolve()));
    child.on("error", (error) => { worker = undefined; rejectPending(error); reject(error); });
    child.on("exit", (code) => { worker = undefined; rejectPending(new Error(`Pocket worker stopped (${code ?? "unknown"})`)); if (code !== 0) reject(new Error(`Pocket worker stopped (${code ?? "unknown"})`)); });
  }).finally(() => { starting = undefined; });
  return starting;
}

async function snapshot(symbol: string): Promise<Snapshot> {
  await startWorker();
  return new Promise((resolve, reject) => {
    if (!worker) return reject(new Error("Pocket worker unavailable"));
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    worker.stdin.write(`${JSON.stringify({ type: "snapshot", id, symbol })}\n`);
    setTimeout(() => { const request = pending.get(id); if (request) { pending.delete(id); request.reject(new Error("Pocket worker timeout")); } }, 20_000).unref();
  });
}

function captureBrowserSsid(): Promise<string> {
  if (browserLogin) return browserLogin;
  browserLogin = new Promise<string>((resolve, reject) => {
    const sessionDir = mkdtempSync(join(dataDir, ".pocket-browser-"));
    const child = spawn(browserPythonPath(), ["-m", "pocket_client.auth"], {
      cwd: root,
      env: { ...process.env, POCKET_SESSION_DIR: sessionDir, PYTHONPATH: join(root, "server") },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: false,
    });
    let output = "";
    let errorOutput = "";
    const cleanup = () => { try { rmSync(sessionDir, { recursive: true, force: true }); } catch {} };
    const timer = setTimeout(() => { child.kill(); cleanup(); reject(new Error("Browser login timeout")); }, 310_000);
    child.stdout.on("data", (data) => { output += String(data); });
    child.stderr.on("data", (data) => { errorOutput += String(data); });
    child.on("error", (error) => { clearTimeout(timer); cleanup(); reject(error); });
    child.on("exit", (code) => {
      clearTimeout(timer);
      try {
        if (code !== 0) throw new Error(errorOutput.trim() || output.trim() || `Browser login stopped (${code ?? "unknown"})`);
        const saved = JSON.parse(readFileSync(join(sessionDir, "session.json"), "utf8")) as { ssid?: string };
        if (!saved.ssid) throw new Error("Browser login returned no SSID");
        resolve(saved.ssid);
      } catch (error) { reject(error instanceof Error ? error : new Error("Browser login failed")); }
      finally { cleanup(); }
    });
  }).finally(() => { browserLogin = undefined; });
  return browserLogin;
}

async function replaceCredential(ssid: string): Promise<Snapshot> {
  if (!demoSsid(ssid)) throw new Error("Only a valid DEMO SSID is accepted");
  const previous = existsSync(ssidFile) ? readFileSync(ssidFile, "utf8") : "";
  const oldWorker = worker;
  if (oldWorker && !oldWorker.killed) {
    oldWorker.kill();
    await (workerExit ?? Promise.resolve());
  }
  writeFileSync(ssidFile, ssid.trim(), { mode: 0o600 });
  try {
    const verified = await snapshot("#AAPL_otc");
    if (verified.status !== "live") throw new Error(`Pocket feed status: ${verified.status}`);
    return verified;
  } catch (error) {
    if (previous) {
      writeFileSync(ssidFile, previous, { mode: 0o600 });
      try { await snapshot("#AAPL_otc"); } catch {}
    }
    throw error;
  }
}

function demoSsid(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 20 && /"isDemo"\s*:\s*1/.test(value);
}

router.get("/credential", (_request, response) => {
  const configured = existsSync(ssidFile);
  response.json({ configured, demo: configured && /"isDemo"\s*:\s*1/.test(readFileSync(ssidFile, "utf8")) });
});

router.post("/credential", async (request, response) => {
  const ssid = request.body?.ssid;
  try {
    const verified = await replaceCredential(ssid);
    if (verified.status !== "live") {
      response.status(503).json({ ok: false, configured: true, demo: true, status: verified.status });
      return;
    }
    response.json({ ok: true, configured: true, demo: true, status: verified.status });
  } catch (error) {
    response.status(503).json({ ok: false, configured: true, demo: true, status: "disconnected", error: error instanceof Error ? error.message : "Pocket feed unavailable" });
  }
});

router.post("/browser-login", async (_request, response) => {
  try {
    const verified = await replaceCredential(await captureBrowserSsid());
    if (verified.status !== "live") {
      response.status(503).json({ ok: false, configured: true, demo: true, status: verified.status });
      return;
    }
    response.json({ ok: true, configured: true, demo: true, status: verified.status, source: "browser" });
  } catch (error) {
    response.status(503).json({ ok: false, status: "disconnected", error: error instanceof Error ? error.message : "Browser login unavailable" });
  }
});

router.get("/snapshot", async (request, response) => {
  try { response.json(await snapshot(String(request.query.symbol ?? "#AAPL_otc"))); }
  catch (error) { response.status(503).json({ status: "disconnected", error: error instanceof Error ? error.message : "Pocket feed unavailable" }); }
});

router.all("*", (_request, response) => {
  response.status(405).json({ error: "Pocket Option data-only: orders are disabled" });
});

export const pocketRouter = router;
