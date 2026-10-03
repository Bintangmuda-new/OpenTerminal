"""Mirror of MainWindow.BiasScore. Guards the two properties that make the
STRUCTURE panel honest: the three numbers always sum to 100, and flipping every
vote must flip BUY and SELL exactly.
"""


def bias_score(votes):
    total = max(len(votes), 1)
    buy = sum(1 for v, _ in votes if v > 0) * 100 // total
    sell = sum(1 for v, _ in votes if v < 0) * 100 // total
    return buy, sell, 100 - buy - sell


def main():
    cases = [
        [(1, "a"), (1, "b"), (-1, "c"), (0, "d"), (0, "e"), (0, "f")],
        [(0, "a")] * 6,
        [(1, "a")] * 6,
        [(-1, "a")] * 6,
        [(1, "a"), (-1, "b"), (1, "c"), (-1, "d"), (0, "e"), (1, "f")],
    ]
    for votes in cases:
        buy, sell, flat = bias_score(votes)
        assert buy + sell + flat == 100, (votes, buy, sell, flat)
        assert 0 <= flat <= 100, (votes, flat)
        # Symmetry: negating every vote swaps BUY and SELL, nothing else.
        rbuy, rsell, rflat = bias_score([(-v, n) for v, n in votes])
        assert (rbuy, rsell, rflat) == (sell, buy, flat), (votes, rbuy, rsell)

    assert bias_score([(1, "a")] * 6)[0] == 100
    assert bias_score([(0, "a")] * 6)[2] == 100
    print(f"ok - {len(cases)} cases, sum-to-100 and symmetry hold")


if __name__ == "__main__":
    main()
