import { useQuery } from "@tanstack/react-query";
import { apiGet, type Candle } from "./api";

export type PocketTick = { price: number; time: number };
export type PocketMarket = { symbol: string; name: string; group: string };
export type PocketSnapshot = {
  mode: "DEMO";
  status: "live" | "stale" | "connecting" | "disconnected";
  receivedAt: number;
  markets: PocketMarket[];
  liveSymbols: string[];
  ticks: Record<string, PocketTick>;
  candles: Record<string, Candle[]>;
  trends: Record<string, { direction: string }>;
};

export function usePocketSnapshot(symbol: string) {
  return useQuery({
    queryKey: ["pocket-snapshot", symbol],
    queryFn: () => apiGet<PocketSnapshot>(`/api/pocket/snapshot?symbol=${encodeURIComponent(symbol.trim())}`),
    refetchInterval: 1_000,
    refetchIntervalInBackground: true,
    staleTime: 0,
    retry: 1,
  });
}

export function latestPocketCandle(snapshot: PocketSnapshot | undefined, symbol: string): Candle | undefined {
  const candles = snapshot?.candles[symbol] ?? Object.values(snapshot?.candles ?? {})[0] ?? [];
  return candles[candles.length - 1];
}
