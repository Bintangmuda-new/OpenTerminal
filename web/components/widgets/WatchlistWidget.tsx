"use client";

import { useState } from "react";
import { fmt } from "../../lib/api";
import { usePocketSnapshot } from "../../lib/pocket";
import { useTerminal } from "../../store/terminal";
import Flash from "../Flash";

export default function WatchlistWidget() {
  const watchlist = useTerminal((s) => s.watchlist);
  const addToWatchlist = useTerminal((s) => s.addToWatchlist);
  const removeFromWatchlist = useTerminal((s) => s.removeFromWatchlist);
  const setActiveSymbol = useTerminal((s) => s.setActiveSymbol);
  const [input, setInput] = useState("");
  const { data: pocket } = usePocketSnapshot(watchlist[0] ?? "#AAPL_otc");

  return (
    <div>
      <form
        className="flex gap-1 p-1"
        onSubmit={(e) => {
          e.preventDefault();
          if (input.trim()) {
            addToWatchlist(input.trim());
            setInput("");
          }
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Add ticker…"
          className="flex-1"
        />
        <button className="term-btn" type="submit">+</button>
      </form>
      <table className="data-table">
        <thead>
          <tr><th>Sym</th><th>Last</th><th>Status</th><th>Updated</th><th></th></tr>
        </thead>
        <tbody>
          {watchlist.map((sym) => {
            const tick = pocket?.ticks[sym];
            const live = pocket?.liveSymbols.includes(sym) && Boolean(tick);
            return (
              <tr key={sym} onClick={() => setActiveSymbol(sym)}>
                <td className="font-bold">{sym}</td>
                <td><Flash value={tick?.price}>{fmt(tick?.price)}</Flash></td>
                <td className={live ? "up" : "dim"}>{live ? "CONNECTED" : pocket ? "WAITING" : "UPDATING"}</td>
                <td className="dim">{tick ? new Date(tick.time * 1000).toLocaleTimeString() : "—"}</td>
                <td>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      removeFromWatchlist(sym);
                    }}
                    className="dim hover:text-[var(--down)]"
                  >
                    ✕
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
