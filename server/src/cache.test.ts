import { describe, expect, it } from "vitest";
import { cached, cacheGet, cacheSet } from "./cache.js";
import { lockSlShClosedCandles, type Candle } from "./slsh.js";

describe("cache", () => {
  it("stores and retrieves values within TTL", () => {
    cacheSet("k1", 42, 1000);
    expect(cacheGet("k1")).toBe(42);
  });

  it("expires values after TTL", async () => {
    cacheSet("k2", "x", 1);
    await new Promise((r) => setTimeout(r, 10));
    expect(cacheGet("k2")).toBeUndefined();
  });

  it("serves stale data when the loader fails after a prior success", async () => {
    let calls = 0;
    const loader = async () => {
      calls++;
      if (calls === 1) return "fresh";
      throw new Error("provider down");
    };
    expect(await cached("k3", 1, loader)).toBe("fresh");
    await new Promise((r) => setTimeout(r, 10));
    expect(await cached("k3", 1, loader)).toBe("fresh"); // stale fallback
    expect(calls).toBe(2);
  });

  it("propagates errors when no stale data exists", async () => {
    await expect(cached("k4", 1000, async () => Promise.reject(new Error("boom")))).rejects.toThrow("boom");
  });
});

describe("SL/SH candle-close locks", () => {
  const base: Candle[] = [
    { time: 0, open: 10, high: 10, low: 9, close: 9.5 },
    { time: 60, open: 9.5, high: 11, low: 9, close: 10.5 },
    { time: 120, open: 10.5, high: 10.8, low: 9.8, close: 10 },
    { time: 180, open: 10, high: 10.6, low: 9.7, close: 10.2 },
    { time: 240, open: 10.2, high: 12, low: 10, close: 11.5 }, // closed
    { time: 300, open: 11.5, high: 11.8, low: 8, close: 8.5 }, // live
  ];

  it("locks an SH immediately when its candle closes", () => {
    expect(lockSlShClosedCandles(base, 4)).toEqual([{ time: 240, price: 12, kind: "SH" }]);
  });

  it("never changes a locked mark when the live candle changes", () => {
    const before = lockSlShClosedCandles(base, 4);
    const mutatedLive = [...base.slice(0, -1), { time: 300, open: 11.5, high: 99, low: 0.01, close: 50 }];
    expect(lockSlShClosedCandles(mutatedLive, 4)).toEqual(before);
  });
});

