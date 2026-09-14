"""
Live data fetchers for the short-squeeze screener.

Sources (all free, no paid key required for core run):
  - yfinance: price, volume, float, short % of float, options chain (gamma proxy)
  - apewisdom.io: WallStreetBets mention rank / velocity
  - stocktwits trending: early social breadth
  - data/short_overrides.csv: manual SI / borrow / float overrides (Finviz, Fintel, etc.)
  - data/watchlist.csv: core tickers always screened
  - highshortinterest.com: >20% SI universe (best-effort scrape)
"""
from __future__ import annotations

import csv
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
}


def _safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        if isinstance(x, str):
            x = x.replace("%", "").replace(",", "").strip()
            if x in ("", "-", "N/A", "None"):
                return None
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except (TypeError, ValueError):
        return None


def _parse_share_count_m(text: str) -> Optional[float]:
    """Parse '19.89M', '800K', '1.2B' into millions of shares."""
    if not text:
        return None
    t = text.strip().replace(",", "").upper()
    m = re.match(r"^([\d.]+)\s*([KMB])?$", t)
    if not m:
        return _safe_float(t)
    n = float(m.group(1))
    unit = m.group(2) or "M"
    if unit == "K":
        return round(n / 1000.0, 3)
    if unit == "B":
        return round(n * 1000.0, 2)
    return round(n, 2)  # already millions


# ── Local files ──────────────────────────────────────────────────────────────

def load_watchlist() -> List[str]:
    path = DATA / "watchlist.csv"
    if not path.exists():
        return ["GME", "AMC", "WEN", "GRPN", "FLWS", "BYND", "KOSS", "BB", "OPEN", "DNUT"]
    tickers = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f) if _has_header(path) else csv.reader(f):
            if isinstance(row, dict):
                t = (row.get("ticker") or row.get("symbol") or "").strip().upper()
            else:
                t = (row[0] if row else "").strip().upper()
            if t and not t.startswith("#") and t != "TICKER":
                tickers.append(t)
    return tickers or ["GME", "AMC", "WEN"]


def _has_header(path: Path) -> bool:
    first = path.read_text(encoding="utf-8").splitlines()[:1]
    if not first:
        return False
    return "ticker" in first[0].lower() or "symbol" in first[0].lower()


def load_short_overrides() -> Dict[str, Dict[str, Any]]:
    """Manual SI / borrow / float from Finviz etc. Columns optional."""
    path = DATA / "short_overrides.csv"
    out: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return out
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = (row.get("ticker") or "").strip().upper()
            if not t:
                continue
            entry: Dict[str, Any] = {"ticker": t}
            if row.get("si_pct") not in (None, ""):
                entry["si_pct"] = _safe_float(row["si_pct"])
            if row.get("float_m") not in (None, ""):
                entry["float_m"] = _safe_float(row["float_m"])
            if row.get("days_to_cover") not in (None, ""):
                entry["dtc_override"] = _safe_float(row["days_to_cover"])
            if row.get("borrow_fee_pct") not in (None, ""):
                entry["borrow_fee_pct"] = _safe_float(row["borrow_fee_pct"])
            if row.get("shares_avail_k") not in (None, ""):
                entry["shares_avail_k"] = _safe_float(row["shares_avail_k"])
            if row.get("name"):
                entry["name"] = row["name"]
            out[t] = entry
    return out


def load_positions() -> List[Dict[str, Any]]:
    path = DATA / "positions.csv"
    if not path.exists():
        return []
    positions = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            t = (row.get("ticker") or "").strip().upper()
            if not t:
                continue
            positions.append(
                {
                    "ticker": t,
                    "entry": _safe_float(row.get("entry")),
                    "shares": _safe_float(row.get("shares")),
                    "stop": _safe_float(row.get("stop")),
                    "flag_price": _safe_float(row.get("flag_price")),
                }
            )
    return positions


# ── Social: WSB + StockTwits ─────────────────────────────────────────────────

def fetch_wsb(pages: int = 2) -> Dict[str, Dict[str, Any]]:
    """ApeWisdom WallStreetBets filter. Returns ticker → social metrics."""
    out: Dict[str, Dict[str, Any]] = {}
    for page in range(1, pages + 1):
        url = f"https://apewisdom.io/api/v1.0/filter/wallstreetbets/page/{page}"
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            results = r.json().get("results") or []
        except Exception as e:
            print(f"  [warn] ApeWisdom page {page}: {e}")
            break
        for item in results:
            t = (item.get("ticker") or "").upper()
            if not t or t.startswith("CRYPTO"):
                continue
            out[t] = {
                "ticker": t,
                "wsb_rank": item.get("rank"),
                "mentions": item.get("mentions"),
                "mentions_24h": item.get("mentions_24h_ago"),
                "rank_24h": item.get("rank_24h_ago"),
                "upvotes": item.get("upvotes"),
                "name": item.get("name") or t,
            }
        time.sleep(0.3)
    print(f"  WSB: {len(out)} tickers from ApeWisdom")
    return out


def fetch_stocktwits_trending() -> Set[str]:
    url = "https://api.stocktwits.com/api/2/trending/symbols.json"
    try:
        r = requests.get(url, headers=UA, timeout=15)
        r.raise_for_status()
        symbols = r.json().get("symbols") or []
        out = {(s.get("symbol") or "").upper() for s in symbols if s.get("symbol")}
        print(f"  StockTwits trending: {len(out)} symbols")
        return out
    except Exception as e:
        print(f"  [warn] StockTwits: {e}")
        return set()


# ── High short interest universe (best-effort) ───────────────────────────────

def fetch_high_short_interest() -> List[Dict[str, Any]]:
    """
    Scrape highshortinterest.com for SI > ~20% universe.
    Falls back to empty list if blocked/changed layout.
    """
    rows: List[Dict[str, Any]] = []
    for path in ("/all/", "/all/2/", "/"):
        url = f"https://www.highshortinterest.com{path}"
        try:
            r = requests.get(url, headers=UA, timeout=25)
            r.raise_for_status()
            html = r.text
        except Exception as e:
            print(f"  [warn] highshortinterest {path}: {e}")
            continue

        found = 0
        # Common table: Company | Ticker | Exchange | SI% | Float | ...
        # Match ticker cells then nearby percent + float-ish numbers
        table_rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.I | re.S)
        for tr in table_rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.I | re.S)
            if len(cells) < 4:
                continue
            texts = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            # find ticker-like token (1-5 caps)
            ticker = None
            for tx in texts[:4]:
                if re.fullmatch(r"[A-Z]{1,5}", tx):
                    ticker = tx
                    break
            if not ticker:
                # link text
                m = re.search(r">([A-Z]{1,5})</a>", tr, re.I)
                if m:
                    ticker = m.group(1).upper()
            if not ticker:
                continue
            # Layout: Ticker | Company | Exchange | ShortInt | Float | Outstd | Industry
            si = None
            fl = None
            name = ticker
            if len(texts) >= 5:
                name = texts[1] if len(texts[1]) > 1 else ticker
                si = _safe_float(texts[3])  # "58.91%" handled by _safe_float
                fl = _parse_share_count_m(texts[4])  # "19.89M" / "800K"
            if si is None:
                for tx in texts:
                    pct = re.search(r"([\d.]+)\s*%", tx)
                    if pct:
                        si = _safe_float(pct.group(1))
                        break
            if ticker and si and si >= 15:
                rows.append(
                    {
                        "ticker": ticker.upper(),
                        "si_pct": si,
                        "float_m": fl,
                        "name": name,
                    }
                )
                found += 1

        if found == 0:
            # Fallback regex pairs
            simple = re.findall(
                r">([A-Z]{1,5})</a>[\s\S]{0,200}?([\d.]+)\s*%[\s\S]{0,120}?([\d,.]+)\s*M?",
                html,
                re.I,
            )
            for t, si_s, fl_s in simple:
                si = _safe_float(si_s)
                fl = _safe_float(fl_s.replace(",", ""))
                if si and si >= 15:
                    if fl and fl > 5000:
                        fl = fl / 1e6
                    rows.append({"ticker": t.upper(), "si_pct": si, "float_m": fl, "name": t.upper()})
                    found += 1
        print(f"  highshortinterest{path}: {found} names")
        time.sleep(0.4)
        if found > 10:
            break  # homepage often enough

    # de-dupe by ticker, keep highest SI
    by: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        t = row["ticker"]
        if t not in by or (row.get("si_pct") or 0) > (by[t].get("si_pct") or 0):
            by[t] = row
    print(f"  SI universe: {len(by)} unique tickers")
    return list(by.values())


# ── yfinance: quotes, float, SI, options gamma proxy ─────────────────────────

def _yf_info_fields(ticker: str) -> Dict[str, Any]:
    import yfinance as yf

    t = yf.Ticker(ticker)
    info: Dict[str, Any] = {}
    try:
        raw = t.fast_info
        # fast_info is object-like
        def g(name, default=None):
            try:
                return getattr(raw, name, default)
            except Exception:
                return default

        last = g("last_price") or g("lastPrice")
        prev = g("previous_close") or g("previousClose")
        vol = g("last_volume") or g("lastVolume")
        avg = g("three_month_average_volume") or g("threeMonthAverageVolume")
        mcap = g("market_cap") or g("marketCap")
        if last:
            info["price"] = float(last)
        if last and prev:
            info["chg_pct"] = round((float(last) / float(prev) - 1) * 100, 2)
        if vol:
            info["vol_m"] = round(float(vol) / 1e6, 3)
        if avg:
            info["avg_vol_m"] = round(float(avg) / 1e6, 3)
        if mcap:
            info["mcap_b"] = round(float(mcap) / 1e9, 3)
    except Exception:
        pass

    # Full info for short % / float / name / earnings
    try:
        full = t.info or {}
    except Exception:
        full = {}

    if full:
        if not info.get("price") and full.get("currentPrice"):
            info["price"] = _safe_float(full["currentPrice"])
        if not info.get("price") and full.get("regularMarketPrice"):
            info["price"] = _safe_float(full["regularMarketPrice"])
        if info.get("chg_pct") is None and full.get("regularMarketChangePercent") is not None:
            info["chg_pct"] = round(float(full["regularMarketChangePercent"]), 2)
        if not info.get("vol_m") and full.get("volume"):
            info["vol_m"] = round(float(full["volume"]) / 1e6, 3)
        if not info.get("avg_vol_m") and full.get("averageVolume"):
            info["avg_vol_m"] = round(float(full["averageVolume"]) / 1e6, 3)
        if not info.get("mcap_b") and full.get("marketCap"):
            info["mcap_b"] = round(float(full["marketCap"]) / 1e9, 3)

        name = full.get("shortName") or full.get("longName")
        if name:
            info["name"] = name

        # Short interest (Yahoo often delayed / incomplete)
        si = full.get("shortPercentOfFloat")
        if si is not None:
            # Yahoo sometimes returns fraction 0.25, sometimes 25
            si = float(si)
            info["si_pct"] = round(si * 100 if si <= 1.5 else si, 2)

        fl_shares = full.get("floatShares")
        if fl_shares:
            fl_m = float(fl_shares) / 1e6
            # Sanity: Yahoo occasionally returns garbage mega-floats (ETFs/ADRs)
            if 0.05 <= fl_m <= 5000:
                info["float_m"] = round(fl_m, 2)

        # Reject absurd SI / market-cap mismatches later in merge

        # Days to cover if Yahoo provides shortRatio
        sr = full.get("shortRatio")
        if sr is not None:
            info["dtc_yahoo"] = round(float(sr), 1)

        # Earnings date
        ed = full.get("earningsTimestamp") or full.get("earningsTimestampStart")
        if ed:
            try:
                info["earnings_date"] = datetime.utcfromtimestamp(int(ed)).strftime("%Y-%m-%d")
            except Exception:
                pass
        # sometimes list of dates
        dates = full.get("earningsDate")
        if dates and isinstance(dates, (list, tuple)) and dates:
            try:
                d0 = dates[0]
                if hasattr(d0, "strftime"):
                    info["earnings_date"] = d0.strftime("%Y-%m-%d")
                elif isinstance(d0, (int, float)):
                    info["earnings_date"] = datetime.utcfromtimestamp(int(d0)).strftime("%Y-%m-%d")
            except Exception:
                pass

    # History fallback for volume if missing
    if not info.get("avg_vol_m") or not info.get("vol_m"):
        try:
            hist = t.history(period="3mo")
            if hist is not None and not hist.empty:
                if not info.get("vol_m"):
                    info["vol_m"] = round(float(hist["Volume"].iloc[-1]) / 1e6, 3)
                if not info.get("avg_vol_m"):
                    info["avg_vol_m"] = round(float(hist["Volume"].mean()) / 1e6, 3)
                if not info.get("price"):
                    info["price"] = round(float(hist["Close"].iloc[-1]), 4)
                if info.get("chg_pct") is None and len(hist) >= 2:
                    info["chg_pct"] = round(
                        (float(hist["Close"].iloc[-1]) / float(hist["Close"].iloc[-2]) - 1) * 100, 2
                    )
        except Exception:
            pass

    return info


def fetch_options_gamma(ticker: str, price: Optional[float] = None) -> Dict[str, Any]:
    """
    Gamma / options proxy from yfinance options chain.
    near_call_oi_k, call_share, near_call_prem_m, atm_iv_pct for ≤14d expiries.
    """
    import yfinance as yf

    out: Dict[str, Any] = {}
    try:
        t = yf.Ticker(ticker)
        expiries = list(t.options or [])
    except Exception:
        return out
    if not expiries:
        return out

    today = date.today()
    near = []
    for exp in expiries:
        try:
            d = datetime.strptime(exp, "%Y-%m-%d").date()
        except ValueError:
            continue
        if 0 <= (d - today).days <= 14:
            near.append(exp)
    if not near:
        # take nearest single expiry if nothing ≤14d
        near = expiries[:1]

    call_oi = 0
    put_oi = 0
    call_vol = 0
    put_vol = 0
    call_prem = 0.0  # $ premium = vol * last * 100
    atm_ivs: List[float] = []

    for exp in near[:3]:  # cap chains
        try:
            chain = t.option_chain(exp)
            calls = chain.calls
            puts = chain.puts
        except Exception:
            continue
        if calls is not None and not calls.empty:
            call_oi += int(calls["openInterest"].fillna(0).sum())
            call_vol += int(calls["volume"].fillna(0).sum())
            last = calls["lastPrice"].fillna(0)
            vol = calls["volume"].fillna(0)
            call_prem += float((last * vol * 100).sum())
            if price:
                # ATM-ish: strike closest to price
                try:
                    idx = (calls["strike"] - price).abs().idxmin()
                    iv = calls.loc[idx, "impliedVolatility"]
                    if iv == iv and iv > 0:
                        atm_ivs.append(float(iv) * 100)
                except Exception:
                    pass
        if puts is not None and not puts.empty:
            put_oi += int(puts["openInterest"].fillna(0).sum())
            put_vol += int(puts["volume"].fillna(0).sum())

    if call_oi or put_oi:
        out["near_call_oi_k"] = round(call_oi / 1000.0, 2)
    total_vol = call_vol + put_vol
    if total_vol > 0:
        out["call_share"] = round(call_vol / total_vol, 3)
    if call_prem > 0:
        out["near_call_prem_m"] = round(call_prem / 1e6, 3)
    if atm_ivs:
        out["atm_iv_pct"] = round(sum(atm_ivs) / len(atm_ivs), 1)
    return out


def enrich_ticker(
    ticker: str,
    base: Optional[Dict[str, Any]] = None,
    fetch_options: bool = True,
) -> Dict[str, Any]:
    row = dict(base or {})
    row["ticker"] = ticker.upper()
    try:
        info = _yf_info_fields(ticker)
        # don't overwrite better manual SI/float with weaker Yahoo zeros
        for k, v in info.items():
            if v is None:
                continue
            if k in ("si_pct", "float_m") and row.get(k) is not None:
                continue
            row[k] = v
        if fetch_options and row.get("price"):
            gam = fetch_options_gamma(ticker, row.get("price"))
            row.update(gam)
    except Exception as e:
        row.setdefault("notes", [])
        if isinstance(row.get("notes"), list):
            row["notes"].append(f"yf error: {e}")
        print(f"  [warn] {ticker}: {e}")
    return row


def build_universe(
    max_si_names: int = 40,
    max_options: int = 25,
    include_wsb_crossover: bool = True,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Assemble the screening universe and enrich with live data.
    Returns (rows, meta).
    """
    print("Fetching social feeds...")
    wsb = fetch_wsb(pages=2)
    st_trending = fetch_stocktwits_trending()

    print("Loading local overrides + watchlist...")
    overrides = load_short_overrides()
    watchlist = load_watchlist()
    positions = load_positions()

    print("Fetching high short-interest universe...")
    si_universe = fetch_high_short_interest()

    merged: Dict[str, Dict[str, Any]] = {}

    for row in si_universe:
        merged[row["ticker"]] = dict(row)

    for t in watchlist:
        merged.setdefault(t, {"ticker": t})

    for p in positions:
        merged.setdefault(p["ticker"], {"ticker": p["ticker"]})

    for t, o in overrides.items():
        merged.setdefault(t, {"ticker": t}).update({k: v for k, v in o.items() if v is not None})

    # WSB crossover: attach social to names already in universe/watchlist only.
    # Do NOT auto-add mega-caps just because they rank on WSB (AAPL/SPY noise).
    # Optional: add top-WSB names that already have SI override or known short DNA.
    if include_wsb_crossover:
        for t, w in wsb.items():
            if t in merged:
                continue
            # only pull pure WSB names if they look like squeeze candidates via overrides
            if t in overrides and (overrides[t].get("si_pct") or 0) >= 15:
                merged.setdefault(t, {"ticker": t})

    # Cap universe for speed: always keep watchlist + positions; top SI by si_pct
    keep: Set[str] = set(watchlist) | {p["ticker"] for p in positions}
    ranked_si = sorted(
        [r for r in merged.values() if r.get("si_pct")],
        key=lambda x: -(x.get("si_pct") or 0),
    )
    for r in ranked_si[:max_si_names]:
        keep.add(r["ticker"])
    # always keep override rows that have explicit borrow data (user is tracking them)
    for t, o in overrides.items():
        if o.get("borrow_fee_pct") is not None or o.get("shares_avail_k") is not None:
            keep.add(t)
    if len(merged) > len(keep):
        print(f"  Cap: {len(merged)} -> {len(keep)} tickers (max_si={max_si_names})")
        merged = {t: merged[t] for t in keep if t in merged}

    # Attach social
    for t, row in merged.items():
        if t in wsb:
            row.update(
                {
                    "wsb_rank": wsb[t].get("wsb_rank"),
                    "mentions": wsb[t].get("mentions"),
                    "mentions_24h": wsb[t].get("mentions_24h"),
                    "rank_24h": wsb[t].get("rank_24h"),
                    "upvotes": wsb[t].get("upvotes"),
                }
            )
            if not row.get("name") and wsb[t].get("name"):
                row["name"] = wsb[t]["name"]
        if t in st_trending:
            row["stocktwits_trending"] = True

    tickers = sorted(merged.keys())
    print(f"Enriching {len(tickers)} tickers via Yahoo (options on top {max_options})...")

    # Priority for options: positions, watchlist, high SI, high WSB rank
    def opt_priority(t: str) -> Tuple[int, float, int]:
        pos = 0 if t in {p["ticker"] for p in positions} else 1
        wl = 0 if t in watchlist else 1
        si = -(merged[t].get("si_pct") or 0)
        wr = merged[t].get("wsb_rank") or 999
        return (pos, wl, si, wr)

    ordered = sorted(tickers, key=opt_priority)
    options_set = set(ordered[:max_options])

    rows: List[Dict[str, Any]] = []
    for i, t in enumerate(ordered, 1):
        print(f"  [{i}/{len(ordered)}] {t}{' +opt' if t in options_set else ''}")
        row = enrich_ticker(t, merged[t], fetch_options=(t in options_set))
        # re-apply overrides after Yahoo (manual wins)
        if t in overrides:
            for k, v in overrides[t].items():
                if v is not None and k != "ticker":
                    row[k] = v
        rows.append(row)
        time.sleep(0.15)  # be polite to Yahoo

    meta = {
        "as_of": date.today().isoformat(),
        "n_tickers": len(rows),
        "n_wsb": len(wsb),
        "n_si_universe": len(si_universe),
        "positions": positions,
        "watchlist": watchlist,
    }
    return rows, meta
