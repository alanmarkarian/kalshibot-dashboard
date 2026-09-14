"""Map Grok raw universe rows into Claude Meme Screener snapshot shape.

Does not copy Grok scores/tiers. Does not write OneDrive Claude files.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
LAST_SNAPSHOT = DATA / "last_snapshot.json"
CLAUDE_MEME_SNAP = DATA / "claude_meme_snapshot.json"
CLAUDE_ENGINE_DIR = DATA / "claude_engine"
CLAUDE_ENGINE_SNAP = CLAUDE_ENGINE_DIR / "snapshot.json"

ENRICH_KEYS = (
    "ticker",
    "price",
    "chg_pct",
    "vol_m",
    "avg_vol_m",
    "mcap_b",
    "near_call_oi_k",
    "call_share",
    "near_call_prem_m",
    "atm_iv_pct",
    "borrow_fee_pct",
    "shares_avail_k",
    "borrow_fee_prev_pct",
    "shares_avail_prev_k",
    "earnings_date",
    "x_buzz",
    "x_trend",
    "x_mentions",
    "x_sentiment",
    "x_bullish_pct",
    "dilution_filing",
)

SI_KEYS = ("ticker", "name", "si_pct", "float_m")


def _as_rows(src: Union[Sequence[Dict[str, Any]], Dict[str, Any]]) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if isinstance(src, dict):
        if isinstance(src.get("results"), list):
            return list(src["results"]), src
        return [], src
    return list(src or []), {}


def _si_row(r: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ticker": r.get("ticker")}
    out["name"] = r.get("name")
    out["si_pct"] = r.get("si_pct")
    out["float_m"] = r.get("float_m")
    return out


def _wsb_row(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rank = r.get("wsb_rank")
    if rank is None:
        return None
    return {
        "ticker": r.get("ticker"),
        "rank": rank,
        "mentions": r.get("mentions") or 0,
        "upvotes": r.get("upvotes") or 0,
        "rank_24h_ago": r.get("rank_24h_ago", r.get("rank_24h")),
        "mentions_24h": r.get("mentions_24h"),
    }


def _enrich_row(r: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in ENRICH_KEYS:
        if k in r and r[k] is not None:
            out[k] = r[k]
        elif k == "ticker":
            out[k] = r.get("ticker")
    return out


def grok_rows_to_claude_snapshot(
    src: Union[Sequence[Dict[str, Any]], Dict[str, Any]],
    *,
    as_of: Optional[str] = None,
    watchlist: Optional[Iterable[str]] = None,
    positions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build Claude-shaped snapshot from RAW Grok fields (not Grok scores)."""
    rows, wrapper = _as_rows(src)
    meta = wrapper.get("meta") if isinstance(wrapper.get("meta"), dict) else {}
    asof = as_of or wrapper.get("as_of") or meta.get("as_of") or date.today().isoformat()
    wl_tickers = list(watchlist) if watchlist is not None else list(meta.get("watchlist") or [])
    pos = positions if positions is not None else list(wrapper.get("positions") or [])

    by_tk = {str(r.get("ticker") or "").upper(): r for r in rows if r.get("ticker")}
    si_universe = [_si_row(r) for r in rows if r.get("ticker")]
    watchlist_rows = []
    for t in wl_tickers:
        tu = str(t).upper()
        r = by_tk.get(tu, {"ticker": tu})
        watchlist_rows.append(_si_row(r) if r.get("ticker") else {"ticker": tu, "name": None, "si_pct": None, "float_m": None})

    wsb = []
    for r in rows:
        w = _wsb_row(r)
        if w:
            wsb.append(w)
    wsb.sort(key=lambda x: (x.get("rank") is None, x.get("rank") or 10**9))

    stocktwits = []
    for r in rows:
        if r.get("stocktwits_trending"):
            stocktwits.append(r["ticker"])

    enrichment = [_enrich_row(r) for r in rows if r.get("ticker")]

    return {
        "as_of": asof,
        "si_universe": si_universe,
        "watchlist": watchlist_rows,
        "stocktwits": stocktwits,
        "wsb": wsb,
        "enrichment": enrichment,
        "positions": pos or [],
        "borrow_watch": [],
    }


def write_claude_snapshot(snapshot: Dict[str, Any]) -> tuple[Path, Path]:
    DATA.mkdir(parents=True, exist_ok=True)
    CLAUDE_ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(snapshot, indent=1, default=str)
    CLAUDE_MEME_SNAP.write_text(text + "\n", encoding="utf-8")
    CLAUDE_ENGINE_SNAP.write_text(text + "\n", encoding="utf-8")
    return CLAUDE_MEME_SNAP, CLAUDE_ENGINE_SNAP


def snapshot_from_last_grok(path: Optional[Path] = None, *, persist: bool = True) -> Dict[str, Any]:
    p = Path(path or LAST_SNAPSHOT)
    grok = json.loads(p.read_text(encoding="utf-8"))
    snap = grok_rows_to_claude_snapshot(grok)
    if persist:
        write_claude_snapshot(snap)
    return snap


if __name__ == "__main__":
    persist = "--no-write" not in sys.argv
    args = [a for a in sys.argv[1:] if a != "--no-write"]
    src = Path(args[0]) if args else LAST_SNAPSHOT
    snap = snapshot_from_last_grok(src, persist=persist)
    n_si = len(snap.get("si_universe") or [])
    n_wsb = len(snap.get("wsb") or [])
    n_en = len(snap.get("enrichment") or [])
    print(f"as_of={snap.get('as_of')} si={n_si} watchlist={len(snap.get('watchlist') or [])} wsb={n_wsb} stocktwits={len(snap.get('stocktwits') or [])} enrichment={n_en} borrow_watch={len(snap.get('borrow_watch') or [])}")
    if persist:
        print(f"wrote {CLAUDE_MEME_SNAP}")
        print(f"wrote {CLAUDE_ENGINE_SNAP}")
