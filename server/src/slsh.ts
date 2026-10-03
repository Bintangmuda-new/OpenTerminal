export type Candle = { time: number; open: number; high: number; low: number; close: number };
export type SlShMark = { time: number; price: number; kind: "SL" | "SH" };

/** Locks a mark from completed candles only; the live candle is never inspected. */
export function lockSlShClosedCandles(candles: readonly Candle[], lookback = 4): SlShMark[] {
  if (!Number.isInteger(lookback) || lookback < 1) throw new RangeError("lookback must be a positive integer");
  const closed = candles.slice(0, -1);
  const marks: SlShMark[] = [];
  for (let index = lookback; index < closed.length; index++) {
    const candle = closed[index];
    const prior = closed.slice(index - lookback, index);
    if (candle.high >= Math.max(...prior.map((item) => item.high))) marks.push({ time: candle.time, price: candle.high, kind: "SH" });
    else if (candle.low <= Math.min(...prior.map((item) => item.low))) marks.push({ time: candle.time, price: candle.low, kind: "SL" });
  }
  return marks;
}
