# Chart indicator rules

## SL / SH real-time

`SL/SH · 4 candle` is an indicator. When a candle closes, it is compared with the four immediately preceding closed candles:

- `SH`: the new candle high is greater than or equal to every prior high.
- `SL`: the new candle low is less than or equal to every prior low.

The marker is created on that candle at close and persisted per symbol/timeframe. It is never recalculated, moved, or removed by later candles.
