import { existsSync } from "node:fs";
import { join } from "node:path";
import Database from "better-sqlite3";
import { Router } from "express";
import { dataDir } from "../db.js";

// The capture database is written by the research collector, not by this API.
// Opened readonly so an export can never corrupt or lock a 9GB backtest asset.
const researchFile = join(dataDir, "otc_research.sqlite3");

let handle: Database.Database | null = null;
function research(): Database.Database {
  if (!existsSync(researchFile)) throw new Error("otc_research.sqlite3 not found");
  handle ??= new Database(researchFile, { readonly: true, fileMustExist: true });
  return handle;
}

const router = Router();

router.get("/stats", (_req, res) => {
  try {
    const db = research();
    const row = db
      .prepare("SELECT COUNT(*) AS candles, COUNT(DISTINCT symbol) AS symbols, MIN(time) AS first, MAX(time) AS last FROM candles_m1")
      .get() as { candles: number; symbols: number; first: number; last: number };
    res.json(row);
  } catch (error) {
    res.status(503).json({ error: (error as Error).message });
  }
});

// Streams rather than buffers: a symbol can hold millions of rows.
router.get("/export/ticks.csv", (req, res) => {
  const symbol = String(req.query.symbol ?? "").trim();
  const limit = Math.min(Number(req.query.limit ?? 500_000), 5_000_000);
  try {
    const db = research();
    const rows = symbol
      ? db.prepare("SELECT symbol, time, price FROM ticks WHERE symbol = ? ORDER BY time LIMIT ?").iterate(symbol, limit)
      : db.prepare("SELECT symbol, time, price FROM ticks ORDER BY time LIMIT ?").iterate(limit);
    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="ticks${symbol ? "-" + symbol.replace(/\W+/g, "") : ""}.csv"`);
    res.write("symbol,time,price\n");
    for (const row of rows as Iterable<{ symbol: string; time: number; price: number }>) {
      res.write(`${row.symbol},${row.time},${row.price}\n`);
    }
    res.end();
  } catch (error) {
    if (!res.headersSent) res.status(503).json({ error: (error as Error).message });
    else res.end();
  }
});

router.get("/export/candles.csv", (req, res) => {
  const symbol = String(req.query.symbol ?? "").trim();
  if (!symbol) return res.status(400).json({ error: "symbol required" });
  try {
    const rows = research()
      .prepare("SELECT time, open, high, low, close FROM candles_m1 WHERE symbol = ? ORDER BY time")
      .iterate(symbol);
    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="candles-${symbol.replace(/\W+/g, "")}.csv"`);
    res.write("time,open,high,low,close\n");
    for (const row of rows as Iterable<Record<string, number>>) {
      res.write(`${row.time},${row.open},${row.high},${row.low},${row.close}\n`);
    }
    res.end();
  } catch (error) {
    if (!res.headersSent) res.status(503).json({ error: (error as Error).message });
    else res.end();
  }
});

export const researchRouter = router;
