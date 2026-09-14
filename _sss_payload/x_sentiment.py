"""
X / FinTwit sentiment leg for the Short Squeeze Screener.

Primary source: Adanos X stock sentiment API (same free key as Claude screener,
used sparingly: 1 batched /v1/compare call per scan, ≤10 tickers).

Differentiation vs Claude's usage:
  - Auto-runs every scan (no manual Chrome step)
  - Buzz acceleration from trend_history (today vs prior 3-day avg)
  - Relative buzz rank within the high-SI batch (who's hottest in-universe)
  - Exhaustion signature: high buzz + falling trend (top-chatter pattern)
  - Optional extra call only when --x-extra (default off to protect free quota)

Config resolution (first hit wins):
  1. env ADANOS_API_KEY
  2. data/adanos_config.json
  3. Claude Meme Screener adanos_config.json (shared key)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CLAUDE_CFG = Path(r"C:\Users\alanm\OneDrive\Documents\Claude Meme Screener\adanos_config.json")

UA = {"User-Agent": "ShortSqueezeScreener/1.0 (local; research)"}


def _load_config() -> Dict[str, Any]:
    key = os.environ.get("ADANOS_API_KEY", "").strip()
    base = "https://api.adanos.org/x/stocks"
    for path in (DATA / "adanos_config.json", CLAUDE_CFG):
        if not path.exists():
            continue
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not key:
            key = (cfg.get("api_key") or "").strip()
        base = (cfg.get("base_url") or base).rstrip("/")
        if key:
            return {"api_key": key, "base_url": base, "source": str(path)}
    if key:
        return {"api_key": key, "base_url": base, "source": "env"}
    return {}


def pick_x_candidates(
    rows: Sequence[Dict[str, Any]],
    positions: Optional[Sequence[Dict[str, Any]]] = None,
    limit: int = 10,
) -> List[str]:
    """
    Choose ≤10 tickers for the single Adanos call.
    Priority: positions → high fuel SI → WSB-active high SI → watchlist heat.
    """
    positions = positions or []
    pos_set = {p["ticker"] for p in positions if p.get("ticker")}
    scored: List[Tuple[float, str]] = []
    for r in rows:
        t = r.get("ticker")
        if not t:
            continue
        si = r.get("si_pct") or 0
        fl = r.get("float_m")
        # crude pre-score fuel
        fuelish = max(0.0, (si - 15) * 0.5)
        if fl is not None:
            if fl < 15:
                fuelish += 13
            elif fl < 30:
                fuelish += 11
            elif fl < 60:
                fuelish += 8
        pri = 0.0
        if t in pos_set:
            pri += 1000
        if r.get("wsb_rank") and r["wsb_rank"] <= 50:
            pri += 50
        if r.get("stocktwits_trending"):
            pri += 20
        if r.get("vol_spike") and r["vol_spike"] >= 2:
            pri += 15
        pri += fuelish
        pri += si * 0.1
        scored.append((pri, t))
    scored.sort(key=lambda x: -x[0])
    out: List[str] = []
    seen = set()
    for _, t in scored:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= limit:
            break
    return out


def _accel_from_history(history: Optional[List[float]]) -> Optional[float]:
    """
    Acceleration = last point / mean of prior 3 points.
    >1.5 → clear ramp; <0.7 → rolling over.
    """
    if not history or len(history) < 2:
        return None
    hist = [float(x) for x in history if x is not None]
    if len(hist) < 2:
        return None
    last = hist[-1]
    prior = hist[-4:-1] if len(hist) >= 4 else hist[:-1]
    prior = [x for x in prior if x > 0]
    if not prior:
        return None if last == 0 else 99.0
    base = sum(prior) / len(prior)
    if base <= 0:
        return None
    return round(last / base, 2)


def fetch_adanos_compare(tickers: Sequence[str]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """
    One /v1/compare call. Returns {TICKER: fields}, meta (quota, errors).
    """
    meta: Dict[str, Any] = {"ok": False, "n": 0, "quota_remaining": None, "error": None}
    cfg = _load_config()
    if not cfg.get("api_key"):
        meta["error"] = "no Adanos API key (set ADANOS_API_KEY or data/adanos_config.json)"
        print(f"  [X] {meta['error']}")
        return {}, meta
    tickers = [t.upper() for t in tickers if t][:10]
    if not tickers:
        meta["error"] = "no tickers"
        return {}, meta

    url = f"{cfg['base_url']}/v1/compare"
    try:
        r = requests.get(
            url,
            params={"tickers": ",".join(tickers)},
            headers={**UA, "X-API-Key": cfg["api_key"]},
            timeout=30,
        )
        meta["quota_remaining"] = (
            r.headers.get("X-RateLimit-Remaining-Monthly")
            or r.headers.get("x-ratelimit-remaining-monthly")
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        meta["error"] = str(e)
        print(f"  [X] Adanos error: {e}")
        return {}, meta

    stocks = payload.get("stocks") or payload.get("results") or []
    out: Dict[str, Dict[str, Any]] = {}
    for s in stocks:
        t = (s.get("ticker") or "").upper()
        if not t:
            continue
        hist = s.get("trend_history") or []
        accel = _accel_from_history(hist)
        buzz = s.get("buzz_score")
        try:
            buzz = float(buzz) if buzz is not None else None
        except (TypeError, ValueError):
            buzz = None
        sent = s.get("sentiment_score")
        try:
            sent = float(sent) if sent is not None else None
        except (TypeError, ValueError):
            sent = None
        out[t] = {
            "x_buzz": round(buzz, 1) if buzz is not None else None,
            "x_trend": s.get("trend"),  # rising / falling / stable / null
            "x_mentions": s.get("mentions") or s.get("unique_tweets") or 0,
            "x_sentiment": sent,
            "x_bullish_pct": s.get("bullish_pct"),
            "x_bearish_pct": s.get("bearish_pct"),
            "x_accel": accel,
            "x_trend_history": hist,
            "x_source": "adanos",
        }
    # relative rank within this batch (1 = hottest)
    ranked = sorted(
        [(v.get("x_buzz") or 0, t) for t, v in out.items()],
        key=lambda x: -x[0],
    )
    for i, (_, t) in enumerate(ranked, 1):
        out[t]["x_batch_rank"] = i

    meta["ok"] = True
    meta["n"] = len(out)
    meta["tickers"] = list(out.keys())
    meta["config_source"] = cfg.get("source")
    print(
        f"  [X] Adanos: {len(out)} tickers"
        + (f" · quota remaining {meta['quota_remaining']}" if meta["quota_remaining"] else "")
    )
    return out, meta


def apply_x_fields(row: Dict[str, Any], x: Dict[str, Any]) -> Dict[str, Any]:
    """Merge X metrics onto a ticker row and attach pre-score flags."""
    row = dict(row)
    row.update({k: v for k, v in x.items() if v is not None or k.startswith("x_")})
    flags = list(row.get("flags") or [])

    buzz = row.get("x_buzz") or 0
    trend = (row.get("x_trend") or "").lower() if row.get("x_trend") else ""
    accel = row.get("x_accel")
    wsb = row.get("wsb_rank")
    mentions = row.get("x_mentions") or 0

    if buzz >= 60 and (wsb is None or wsb > 50):
        flags.append("X TRENDING (pre-Reddit)")
    elif buzz >= 60:
        flags.append(f"X BUZZ {buzz:.0f}")

    if trend == "rising" and buzz >= 40:
        flags.append("X TREND RISING")
    if accel is not None and accel >= 1.5 and buzz >= 30:
        flags.append(f"X ACCELERATING {accel:.1f}x vs 3d")
    if accel is not None and accel <= 0.7 and buzz >= 55:
        flags.append("X ROLLING OVER (buzz fading)")
    # Exhaustion: still loud but trend falling — classic top signature
    if buzz >= 70 and trend == "falling" and mentions >= 50:
        flags.append("X EXHAUSTION SIGNATURE (high buzz + falling)")

    if row.get("x_batch_rank") == 1 and buzz >= 40:
        flags.append("X #1 IN BATCH (hottest FinTwit in scan set)")

    # de-dupe flags preserving order
    seen = set()
    uniq = []
    for f in flags:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    row["flags"] = uniq
    return row


def enrich_rows_with_x(
    rows: List[Dict[str, Any]],
    positions: Optional[List[Dict[str, Any]]] = None,
    limit: int = 10,
    enabled: bool = True,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Pick candidates, fetch Adanos once, merge back into rows."""
    meta: Dict[str, Any] = {"ok": False, "skipped": not enabled}
    if not enabled:
        print("  [X] skipped (--no-x)")
        return rows, meta

    tickers = pick_x_candidates(rows, positions, limit=limit)
    print(f"  [X] candidates: {', '.join(tickers)}")
    by_x, meta = fetch_adanos_compare(tickers)
    if not by_x:
        return rows, meta

    out = []
    for r in rows:
        t = r.get("ticker")
        if t in by_x:
            out.append(apply_x_fields(r, by_x[t]))
        else:
            out.append(r)
    meta["candidates"] = tickers
    return out, meta
