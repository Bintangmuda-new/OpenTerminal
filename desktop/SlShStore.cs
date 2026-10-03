using Microsoft.Data.Sqlite;
using System;
using System.Collections.Generic;
using System.IO;

namespace OpenTerminal.Desktop;

internal sealed class SlShStore
{
    private readonly string connectionString;

    public SlShStore(string dataDirectory)
    {
        Directory.CreateDirectory(dataDirectory);
        connectionString = new SqliteConnectionStringBuilder { DataSource = Path.Combine(dataDirectory, "terminal.db") }.ToString();
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = """
            CREATE TABLE IF NOT EXISTS slsh_marks (
              symbol TEXT NOT NULL,
              minutes INTEGER NOT NULL,
              candle_time REAL NOT NULL,
              price REAL NOT NULL,
              kind TEXT NOT NULL CHECK (kind IN ('SL', 'SH')),
              locked_at TEXT NOT NULL DEFAULT (datetime('now')),
              PRIMARY KEY (symbol, minutes, candle_time)
            );
            """;
        command.ExecuteNonQuery();
    }

    public IEnumerable<SlShRow> Load()
    {
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = "SELECT symbol, minutes, candle_time, price, kind FROM slsh_marks";
        using var reader = command.ExecuteReader();
        while (reader.Read())
            yield return new SlShRow(reader.GetString(0), reader.GetInt32(1), reader.GetDouble(2), reader.GetDouble(3), reader.GetString(4));
    }

    public void InsertIfAbsent(SlShRow row)
    {
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = "INSERT OR IGNORE INTO slsh_marks (symbol, minutes, candle_time, price, kind) VALUES ($symbol, $minutes, $time, $price, $kind)";
        command.Parameters.AddWithValue("$symbol", row.Symbol);
        command.Parameters.AddWithValue("$minutes", row.Minutes);
        command.Parameters.AddWithValue("$time", row.Time);
        command.Parameters.AddWithValue("$price", row.Price);
        command.Parameters.AddWithValue("$kind", row.Kind);
        command.ExecuteNonQuery();
    }

    private SqliteConnection Open()
    {
        var connection = new SqliteConnection(connectionString);
        connection.Open();
        return connection;
    }
}

internal sealed record SlShRow(string Symbol, int Minutes, double Time, double Price, string Kind);
