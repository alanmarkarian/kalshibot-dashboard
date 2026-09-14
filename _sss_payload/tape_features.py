"""
Price/volume microstructure flags from the event-study miner.

These are SOFT flags only — they do not reweight the composite score until the
live paper ledger confirms edge. Computed from ~3 months of Yahoo OHLCV.

Promising features from event_study.py (2026-07-28 sample):
  - Reclaim 20-day MA
  - Bollinger width compression
  - Within 3% of 20-day high
  - 3 higher lows
  - Combo: compression + pressing highs (strongest pair lift)
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import pandas as pd


def _hist(ticker: str) -> Optional[pd.DataFrame]:
    import yfinance as yf

    try:
        df = yf.download(ticker, period="4mo", progress=False, auto_adjust=True)
    except Exception:
        return None
    if df is None or df.empty or len(df) < 40:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.rename(columns=str.title)
    return df.dropna(subset=["Close", "Volume"])


def _rvol(df: pd.DataFrame, win: int = 20) -> float:
    vol = df["Volume"].astype(float)
    if len(vol) < win + 1:
        return float("nan")
    avg = vol.iloc[-(win + 1) : -1].mean()
    return float(vol.iloc[-1] / avg) if avg > 0 else float("nan")


def compute_tape_flags(df: pd.DataFrame) -> Dict[str, Any]:
    c = df["Close"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    out: Dict[str, Any] = {}
    flags: List[str] = []

    # RVOL
    rv = _rvol(df)
    if rv == rv:
        out["tape_rvol"] = round(rv, 2)
        if rv >= 3:
            flags.append("TAPE RVOL >=3x")
        elif rv >= 2:
            flags.append("TAPE RVOL >=2x")

    # Reclaim 20-MA
    if len(df) >= 23:
        ma20 = c.iloc[-20:].mean()
        ma20_prior = c.iloc[-23:-3].mean()
        out["tape_vs_20ma"] = round((float(c.iloc[-1]) / float(ma20) - 1) * 100, 2)
        if float(c.iloc[-1]) > float(ma20) and float(c.iloc[-4]) < float(ma20_prior):
            flags.append("TAPE RECLAIM 20-MA")
            out["tape_reclaim_20ma"] = True

    # Near 20d high
    if len(df) >= 20:
        hi20 = float(h.iloc[-20:].max())
        dist = float(c.iloc[-1]) / hi20 - 1
        out["tape_vs_20high"] = round(dist * 100, 2)
        if dist >= -0.03:
            flags.append("TAPE NEAR 20D HIGH")
            out["tape_near_20high"] = True

    # Higher lows
    if len(df) >= 4 and all(float(l.iloc[-i]) > float(l.iloc[-i - 1]) for i in range(1, 4)):
        flags.append("TAPE HIGHER LOWS")
        out["tape_higher_lows"] = True

    # BB width compression (bottom quartile of last 60 widths)
    if len(df) >= 60:
        widths = []
        for i in range(0, 60):
            sl = c.iloc[len(c) - 20 - i : len(c) - i] if i > 0 else c.iloc[-20:]
            if len(sl) < 20:
                continue
            mid, sd = float(sl.mean()), float(sl.std())
            if mid > 0 and sd == sd:
                widths.append((2 * sd) / mid * 100)
        if widths:
            cur = widths[0]
            out["tape_bb_width"] = round(cur, 3)
            thr = sorted(widths)[max(0, len(widths) // 4 - 1)]
            if cur <= thr:
                flags.append("TAPE BB SQUEEZE (compression)")
                out["tape_bb_squeeze"] = True

    # 3d momentum
    if len(df) >= 4:
        r3 = float(c.iloc[-1]) / float(c.iloc[-4]) - 1
        out["tape_ret_3d"] = round(r3 * 100, 2)
        if r3 >= 0.08:
            flags.append("TAPE 3D +8%")
            out["tape_ret_3d_hot"] = True

    # Event-study best pair: compression + pressing highs
    if out.get("tape_bb_squeeze") and out.get("tape_near_20high"):
        flags.append("TAPE COIL+BREAKOUT SETUP (BB squeeze + 20d high)")
        out["tape_coil_breakout"] = True

    # Constructive structure combo
    if out.get("tape_reclaim_20ma") and out.get("tape_higher_lows"):
        flags.append("TAPE STRUCTURE (reclaim MA + higher lows)")
        out["tape_structure"] = True

    out["tape_flags"] = flags
    return out


def enrich_rows_with_tape(
    rows: List[Dict[str, Any]],
    max_names: int = 25,
    enabled: bool = True,
) -> List[Dict[str, Any]]:
    if not enabled:
        return rows
    # prioritize high SI / watchlist-ish
    ranked = sorted(rows, key=lambda r: -(r.get("si_pct") or 0))
    want = set()
    for r in ranked:
        if len(want) >= max_names:
            break
        if (r.get("si_pct") or 0) >= 15 or r.get("wsb_rank"):
            want.add(r["ticker"])
    # always include already-flagged / high fuel candidates by SI top
    print(f"  [tape] computing microstructure on {len(want)} names...")
    out = []
    for r in rows:
        t = r["ticker"]
        if t not in want:
            out.append(r)
            continue
        df = _hist(t)
        if df is None:
            out.append(r)
            time.sleep(0.05)
            continue
        tape = compute_tape_flags(df)
        merged = dict(r)
        merged.update({k: v for k, v in tape.items() if k != "tape_flags"})
        flags = list(merged.get("flags") or [])
        for f in tape.get("tape_flags") or []:
            if f not in flags:
                flags.append(f)
        merged["flags"] = flags
        out.append(merged)
        time.sleep(0.08)
    return out
