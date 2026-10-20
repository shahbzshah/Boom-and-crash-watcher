"""VIDYA (Variable Index Dynamic Average) for the Crash 1000 alert bot.

Standard VIDYA (Tushar Chande) adapts its smoothing to trend strength:

    sc      = 2 / (period + 1)                 # EMA-style smoothing constant
    CMO_t   = (sum(up) - sum(down))
              / (sum(up) + sum(down))          # Chande Momentum Oscillator,
                                              # over the last `period` changes
    alpha_t = sc * abs(CMO_t)                  # dynamic smoothing factor
    VIDYA_t = alpha_t * close_t + (1 - alpha_t) * VIDYA_{t-1}

Strong trend  -> |CMO| ~ 1 -> alpha ~ sc (tracks like an EMA).
Flat market   -> |CMO| ~ 0 -> alpha ~ 0 (line barely moves).

`volumatic_vidya` is a volume-modulated variant — a reconstruction of the
"Volumatic VIDYA" idea (volume-adaptive VIDYA), because the compiled
`.ex5` strategy Megah sent cannot be inspected:

    rel_t   = n_t / mean(n over last `period` candles), clamped to [0.25, 4]
    alpha   = min(alpha * rel_t, 1.0)

i.e. candles with unusually many ticks (Deriv has no real volume, so tick
counts are the proxy) let the line react faster; quiet candles slow it down.
If volume data is missing or flat, it reduces exactly to standard VIDYA —
so both variants are always safe to reference from the signal rule.

Seeding: VIDYA_0 = close_0. Before `period` changes exist, CMO is computed
over however many changes are available, so the series is defined from the
first candle and matches the textbook formula once warmed up.
"""


def _cmo_alpha(close_prev_window, period):
    """alpha for one bar given the trailing window of closes (incl. current)."""
    changes = [close_prev_window[i] - close_prev_window[i - 1]
               for i in range(1, len(close_prev_window))]
    window = changes[-period:] if period > 0 else changes
    up = sum(c for c in window if c > 0)
    dn = sum(-c for c in window if c < 0)
    denom = up + dn
    cmo = (up - dn) / denom if denom > 0 else 0.0
    sc = 2.0 / (period + 1) if period > 0 else 1.0
    return sc * abs(cmo)


def vidya(closes, period=9):
    """Standard VIDYA over a list of closes. Returns list[float]."""
    out = []
    v = None
    for i, c in enumerate(closes):
        if v is None:
            v = float(c)  # seed on the first close
        else:
            a = _cmo_alpha(closes[: i + 1], period)
            v = a * c + (1.0 - a) * v
        out.append(v)
    return out


def volumatic_vidya(closes, volumes=None, period=9):
    """Volume-modulated VIDYA ("Volumatic" reconstruction).

    `volumes` is a per-candle tick-count list aligned with `closes`
    (use None entries / omit the list when unavailable — it then equals
    standard VIDYA). Returns list[float].
    """
    plain = vidya(closes, period)
    if not closes:
        return plain
    out = []
    for i, v in enumerate(plain):
        rel = 1.0
        if volumes:
            window = [n for n in volumes[max(0, i - period + 1): i + 1]
                      if n is not None and n > 0]
            if window:
                mean_n = sum(window) / len(window)
                cur = volumes[i]
                if cur is not None and cur > 0 and mean_n > 0:
                    rel = max(0.25, min(4.0, cur / mean_n))
        if i == 0:
            out.append(v)
        else:
            a = _cmo_alpha(closes[: i + 1], period)
            a_vol = min(a * rel, 1.0)
            out.append(a_vol * closes[i] + (1.0 - a_vol) * out[-1])
    return out
