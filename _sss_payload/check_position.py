#!/usr/bin/env python3
"""
Quick position check — no full universe scan.

Usage:
  python check_position.py              # all rows in data/positions.csv
  python check_position.py GRPN         # one ticker
  python check_position.py GRPN WEN     # several
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT))

from scoring import exit_signals  # noqa: E402


def load_positions() -> Dict[str, Dict[str, Any]]:
    path = DATA / "positions.csv"
    out: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return out
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            t = (row.get("ticker") or "").strip().upper()
            if not t:
                continue
            def num(k):
                v = row.get(k)
                if v is None or str(v).strip() == "":
                    return None
                try:
                    return float(v)
                except ValueError:
                    return None
            out[t] = {
                "ticker": t,
                "entry": num("entry"),
                "shares": num("shares"),
                "stop": num("stop"),
                "flag_price": num("flag_price"),
            }
    return out


def live_quote(ticker: str) -> Dict[str, Any]:
    import yfinance as yf

    t = yf.Ticker(ticker)
    info: Dict[str, Any] = {"ticker": ticker}
    try:
        fi = t.fast_info
        last = getattr(fi, "last_price", None) or getattr(fi, "lastPrice", None)
        prev = getattr(fi, "previous_close", None) or getattr(fi, "previousClose", None)
        vol = getattr(fi, "last_volume", None) or getattr(fi, "lastVolume", None)
        avg = getattr(fi, "three_month_average_volume", None) or getattr(
            fi, "threeMonthAverageVolume", None
        )
        if last:
            info["price"] = float(last)
        if last and prev:
            info["chg_pct"] = round((float(last) / float(prev) - 1) * 100, 2)
        if vol:
            info["vol_m"] = round(float(vol) / 1e6, 3)
        if avg:
            info["avg_vol_m"] = round(float(avg) / 1e6, 3)
            if vol and avg:
                info["vol_spike"] = round(float(vol) / float(avg), 2)
    except Exception as e:
        info["error"] = str(e)

    if not info.get("price"):
        try:
            hist = t.history(period="5d")
            if hist is not None and not hist.empty:
                info["price"] = float(hist["Close"].iloc[-1])
                if len(hist) >= 2:
                    info["chg_pct"] = round(
                        (float(hist["Close"].iloc[-1]) / float(hist["Close"].iloc[-2]) - 1)
                        * 100,
                        2,
                    )
                if "Volume" in hist.columns:
                    info["vol_m"] = round(float(hist["Volume"].iloc[-1]) / 1e6, 3)
                    avg = float(hist["Volume"].mean())
                    if avg:
                        info["avg_vol_m"] = round(avg / 1e6, 3)
                        info["vol_spike"] = round(float(hist["Volume"].iloc[-1]) / avg, 2)
        except Exception as e:
            info["error"] = str(e)

    # light SI / float if Yahoo has it
    try:
        full = t.info or {}
        si = full.get("shortPercentOfFloat")
        if si is not None:
            si = float(si)
            info["si_pct"] = round(si * 100 if si <= 1.5 else si, 2)
        fl = full.get("floatShares")
        if fl:
            info["float_m"] = round(float(fl) / 1e6, 2)
        name = full.get("shortName") or full.get("longName")
        if name:
            info["name"] = name
    except Exception:
        pass

    # last scan snapshot extras (X, borrow, flags) if available
    snap_path = DATA / "last_snapshot.json"
    if snap_path.exists():
        import json

        try:
            snap = json.loads(snap_path.read_text(encoding="utf-8"))
            for r in snap.get("results") or []:
                if r.get("ticker") == ticker:
                    for k in (
                        "x_buzz",
                        "x_trend",
                        "x_mentions",
                        "borrow_fee_pct",
                        "shares_avail_k",
                        "borrow_fee_prev_pct",
                        "shares_avail_prev_k",
                        "score",
                        "tier",
                        "fuel",
                        "ign",
                        "flags",
                        "dtc",
                    ):
                        if r.get(k) is not None and k not in info:
                            info[k] = r[k]
                    # Do NOT import call_share from last scan — Yahoo chains often
                    # false-fire "puts takeover" when near-dated OI is empty (GRPN).
                    info["last_scan_asof"] = snap.get("as_of")
                    break
        except Exception:
            pass

    return info


def report(ticker: str, pos: Optional[Dict[str, Any]]) -> None:
    print()
    print("=" * 56)
    print(f"  QUICK CHECK  ·  {ticker}")
    print("=" * 56)
    q = live_quote(ticker)
    if q.get("error") and not q.get("price"):
        print(f"  Could not fetch quote: {q['error']}")
        return

    px = q.get("price")
    name = q.get("name") or ""
    chg = q.get("chg_pct")
    print(f"  {name}")
    print(f"  Price   ${px:.2f}" if px else "  Price   n/a", end="")
    if chg is not None:
        print(f"  ({chg:+.2f}% vs prior close)")
    else:
        print()

    if q.get("vol_spike") is not None:
        print(f"  Volume  {q.get('vol_m')}M  ·  {q['vol_spike']}x avg")
    if q.get("si_pct") is not None:
        print(f"  SI%     {q['si_pct']}" + (f"  ·  float {q['float_m']}M" if q.get("float_m") else ""))
    if q.get("score") is not None:
        print(
            f"  Last scan score {q['score']} ({q.get('tier')})  "
            f"fuel {q.get('fuel')}  ign {q.get('ign')}  as_of {q.get('last_scan_asof')}"
        )
    if q.get("x_buzz") is not None:
        print(
            f"  X (last scan)  buzz {q['x_buzz']}  trend {q.get('x_trend')}  "
            f"mentions {q.get('x_mentions')}"
        )
    if q.get("borrow_fee_pct") is not None:
        print(
            f"  Borrow (last scan)  fee {q['borrow_fee_pct']}%  "
            f"avail {q.get('shares_avail_k')}K"
        )

    if not pos:
        print("\n  (no row in positions.csv - exit P/L not computed)")
        print("  Add: ticker,entry,shares,stop,flag_price")
        print()
        return

    entry = pos.get("entry")
    stop = pos.get("stop")
    shares = pos.get("shares")
    flag = pos.get("flag_price")

    print("\n  -- POSITION --")
    if entry is not None:
        print(f"  Entry   ${entry:.4f}" if entry != int(entry) else f"  Entry   ${entry:.2f}")
    if shares is not None:
        print(f"  Shares  {shares:g}")
    if stop is not None:
        print(f"  Stop    ${stop:.2f}")
    if flag is not None:
        print(f"  Flag    ${flag:.2f}")

    if entry and px:
        pl = (px / entry - 1) * 100
        usd = (px - entry) * shares if shares else None
        print(f"  P/L     {pl:+.1f}%", end="")
        if usd is not None:
            print(f"  (${usd:+,.0f})")
        else:
            print()
        mult = px / entry
        print(f"  Multiple {mult:.2f}x entry  (scale-out doctrine at ~3-4x)")
    if stop and px:
        print(f"  Stop distance  {(px / stop - 1) * 100:.1f}% above stop")
    if flag and px:
        print(f"  vs flag price  {(px / flag - 1) * 100:+.1f}%")

    # merge live quote into row for exit rules
    row = {**q}
    sigs = exit_signals(row, pos)
    if len(sigs) >= 2 or any("DILUTION" in s for s in sigs):
        level = "RED"
    elif sigs:
        level = "AMBER"
    else:
        level = "GREEN"

    colors = {"GREEN": "hold / no exit flags", "AMBER": "caution - 1 exit flag", "RED": "exit pressure"}
    print(f"\n  EXIT PRESSURE: {level}  ({colors[level]})")
    if sigs:
        for s in sigs:
            print(f"    ! {s}")
    else:
        print("    [ok] no exit signals from live quote + last-scan context")
        print("    (dilution / full gamma still need a full scan or Claude EDGAR pass)")

    print()
    print("  Tip: full scan for X refresh, gamma, dilution, consensus.")
    print("=" * 56)
    print()


def main() -> int:
    positions = load_positions()
    args = [a.upper() for a in sys.argv[1:] if not a.startswith("-")]

    if args:
        tickers = args
    elif positions:
        tickers = list(positions.keys())
    else:
        print("No positions in data/positions.csv and no ticker given.")
        print("Usage: python check_position.py GRPN")
        return 1

    for t in tickers:
        report(t, positions.get(t))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
