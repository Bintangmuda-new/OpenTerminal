import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np

from op1m_research import audit_csv, audit_ticks, summarize, wilson


class Op1mResearchTests(unittest.TestCase):
    def test_scores_draws_separately_from_wins(self):
        result = summarize(np.array([1, -1, 1]), np.array([1, 1, 0]))
        self.assertEqual((result["n"], result["wins"], result["losses"], result["draws"]), (3, 1, 1, 1))
        self.assertEqual((result["call"], result["put"]), (2, 1))
        self.assertGreater(result["ci"][0], 0)

    def test_wilson_is_bounded_and_full_sample_is_not_certainty(self):
        lo, hi = wilson(10, 10)
        self.assertLess(lo, 100)
        self.assertEqual(hi, 100)

    def test_audits_npz_and_flags_bad_ohlc_without_repairing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ticks").mkdir()
            (root / "csv").mkdir()
            np.savez(root / "ticks" / "EURUSD.npz",
                     t=np.array([1.0, 1.0, 0.0]), p=np.array([1.1, np.nan, 1.2]))
            with (root / "csv" / "sample.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["timestamp", "open", "high", "low", "close"])
                writer.writerow([1, 10, 9, 11, 10])

            ticks = audit_ticks(root / "ticks")
            csv_data = audit_csv(root / "csv")
            self.assertEqual((ticks["files"], ticks["ticks"], ticks["invalid"],
                              ticks["duplicate_times"], ticks["unsorted"]), (1, 3, 1, 1, 1))
            self.assertEqual((csv_data["rows"], csv_data["invalid_ohlc"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
