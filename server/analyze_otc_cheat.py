import sqlite3
import pandas as pd
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"

def main():
    conn = sqlite3.connect(DB)
    # Get 1000 candles from a symbol
    df = pd.read_sql("SELECT * FROM candles_m1 WHERE symbol LIKE '%otc%' ORDER BY time ASC LIMIT 5000", conn)
    
    # Are there any deterministic patterns?
    # E.g. if close matches a certain decimal, next candle is green?
    df['up'] = (df['close'] > df['open']).astype(int)
    df['next_up'] = df['up'].shift(-1)
    
    # Looking for a 100% correlation pattern in tick/candle data
    print("Total rows:", len(df))
    print("Symbols:", df['symbol'].unique())
    
    # Check if there is any simple logic that yields 100% winrate.
    # E.g., if low == open, or high == open, or modulo math.
    
    df['close_last_digit'] = df['close'].apply(lambda x: str(x)[-1])
    
    for digit in df['close_last_digit'].unique():
        subset = df[df['close_last_digit'] == digit].dropna()
        if len(subset) > 10:
            winrate = subset['next_up'].mean()
            if winrate == 1.0 or winrate == 0.0:
                print(f"BINGO! Pattern found: Last digit {digit} -> next_up={winrate} (count={len(subset)})")
                
    print("Searching for patterns...")
    
main()
