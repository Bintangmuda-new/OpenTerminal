import sqlite3
import pandas as pd
from pathlib import Path
import numpy as np

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"

def aggregate_candles(df, factor):
    """Aggregate M1 candles into higher timeframes"""
    df_agg = df.groupby(np.arange(len(df)) // factor).agg({
        'time': 'first',
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last'
    })
    return df_agg

def get_trend(df):
    if len(df) < 5: return "NETRAL"
    last_close = df['close'].iloc[-1]
    mid = df['close'].mean()
    if last_close > mid * 1.0001: return "UP"
    if last_close < mid * 0.9999: return "DOWN"
    return "RANGE"

def run_backtest():
    conn = sqlite3.connect(DB)
    df = pd.read_sql("SELECT * FROM candles_m1 WHERE symbol='#AAPL_otc' ORDER BY time ASC", conn)
    
    wins = 0
    losses = 0
    signals = 0
    
    # Simple strict SMC simulation
    for i in range(100, len(df)-1):
        window = df.iloc[i-100:i+1].copy()
        current_candle = window.iloc[-1]
        next_candle = df.iloc[i+1]
        
        # M15 and H1 Bias
        m15 = aggregate_candles(window, 15)
        h1 = aggregate_candles(window, 60)
        
        bias_h1 = get_trend(h1)
        bias_m15 = get_trend(m15)
        
        is_bullish_bias = bias_h1 == "UP" and bias_m15 == "UP"
        is_bearish_bias = bias_h1 == "DOWN" and bias_m15 == "DOWN"
        
        # Discount / Premium Zone
        recent = m15.iloc[-6:]
        high = recent['high'].max()
        low = recent['low'].min()
        span = high - low
        if span == 0: continue
        
        pos = (current_candle['close'] - low) / span
        in_discount = pos <= 0.3
        in_premium = pos >= 0.7
        
        # Signal Generation (Extremely Strict)
        signal = None
        if is_bullish_bias and in_discount:
            signal = "CALL"
        elif is_bearish_bias and in_premium:
            signal = "PUT"
            
        if signal:
            signals += 1
            if signal == "CALL":
                if next_candle['close'] > next_candle['open']: wins += 1
                else: losses += 1
            elif signal == "PUT":
                if next_candle['close'] < next_candle['open']: wins += 1
                else: losses += 1

    total = wins + losses
    winrate = (wins/total*100) if total > 0 else 0
    print(f"--- SMC BACKTEST RESULTS ---")
    print(f"Total Signals: {signals}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    print(f"Winrate: {winrate:.2f}%")

run_backtest()
