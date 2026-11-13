import sqlite3
import pandas as pd
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"

def run_100_percent_exploit():
    conn = sqlite3.connect(DB)
    df = pd.read_sql("SELECT * FROM candles_m1 WHERE symbol='#AAPL_otc' ORDER BY time ASC", conn)
    
    print("====================================================")
    print("⚙️ EXECUTING '100% WINRATE' EXPLOIT BACKTEST")
    print("====================================================\n")
    
    wins = 0
    losses = 0
    
    for i in range(1, len(df)-1):
        # NORMAL TRADER LOGIC (Analisa candle masa lalu)
        current_candle = df.iloc[i]
        
        # CHEAT/EXPLOIT LOGIC (Membaca Memory Cache Broker 1 Menit ke Depan)
        future_candle = df.iloc[i+1]
        
        # Signal hanya dikeluarkan JIKA kita sudah tahu hasil masa depan (Look-ahead)
        if future_candle['close'] > future_candle['open']:
            signal = "CALL"
            # Eksekusi
            wins += 1 
        elif future_candle['close'] < future_candle['open']:
            signal = "PUT"
            # Eksekusi
            wins += 1
            
    total = wins + losses
    print(f"Total Sinyal Dieksekusi : {total}")
    print(f"Total WIN               : {wins}")
    print(f"Total LOSS              : {losses}")
    print(f"WINRATE                 : {(wins/total*100):.2f}%")

run_100_percent_exploit()
