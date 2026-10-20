"""Tests for vidya.py — hand-computed expectations, not just smoke."""
import vidya


def _close(a, b, tol=1e-9):
    return all(abs(x - y) < tol for x, y in zip(a, b)) and len(a) == len(b)


# period=3 -> sc = 2/4 = 0.5
UP = [10.0, 11.0, 12.0, 13.0]
UP_EXPECT = [10.0, 10.5, 11.25, 12.125]          # CMO=+1 every bar -> a=0.5
DOWN = [13.0, 12.0, 11.0, 10.0]
DOWN_EXPECT = [13.0, 12.5, 11.75, 10.875]        # CMO=-1 every bar -> a=0.5


def test_steady_uptrend():
    assert _close(vidya.vidya(UP, period=3), UP_EXPECT), vidya.vidya(UP, period=3)


def test_steady_downtrend():
    assert _close(vidya.vidya(DOWN, period=3), DOWN_EXPECT)


def test_flat_market_freezes_line():
    flat = [5.0] * 6
    got = vidya.vidya(flat, period=3)
    assert _close(got, [5.0] * 6), got  # CMO=0 -> alpha=0 -> line never moves


def test_trend_switch_resets_smoothing():
    # up then flat: alpha collapses once momentum dies
    s = [10.0, 11.0, 12.0, 12.0, 12.0, 12.0, 12.0]
    got = vidya.vidya(s, period=3)
    assert got[-1] < 12.0          # never fully catches a dead trend
    assert got[-1] > got[2]        # but crept closer than right after the move


def test_uniform_volume_equals_plain():
    vols = [10] * len(UP)
    assert _close(vidya.volumatic_vidya(UP, vols, period=3),
                  vidya.vidya(UP, period=3))


def test_no_volume_equals_plain():
    assert _close(vidya.volumatic_vidya(UP, None, period=3),
                  vidya.vidya(UP, period=3))
    assert _close(vidya.volumatic_vidya(UP, [None] * len(UP), period=3),
                  vidya.vidya(UP, period=3))


def test_fat_volume_bar_reacts_faster():
    # volume 100 vs mean 40 -> rel=2.5 -> a_vol = min(0.5*2.5,1)=1.0 -> snaps to close
    vols = [10, 10, 100, 10]
    got = vidya.volumatic_vidya(UP, vols, period=3)
    plain = vidya.vidya(UP, period=3)
    assert abs(got[2] - 12.0) < 1e-9, got
    assert got[2] > plain[2]


def test_thin_volume_bar_reacts_slower():
    vols = [10, 10, 1, 10]
    got = vidya.volumatic_vidya(UP, vols, period=3)
    plain = vidya.vidya(UP, period=3)
    assert got[2] < plain[2]


def test_edges():
    assert vidya.vidya([], period=3) == []
    assert vidya.vidya([7.5], period=3) == [7.5]
    assert vidya.volumatic_vidya([], None, period=3) == []


if __name__ == "__main__":
    for name, fn in sorted({k: v for k, v in globals().items()
                            if k.startswith("test_")}.items()):
        fn()
        print("PASS", name)
