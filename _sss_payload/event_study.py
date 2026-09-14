#!/usr/bin/env python3
"""
Event-study trigger miner for the Short Squeeze Screener.

Pulls real OHLCV history around known squeeze ignition dates and quiet high-SI
controls, then measures which *price/volume microstructure* features light up
BEFORE the move — candidates the live screener may not be watching yet.

This does NOT change scoring thresholds. It writes evidence:
  data/event_study_results.json
  event_study_report.html
  console summary

Usage:
  python event_study.py
  python event_study.py --lookback 15 --horizon 10
  python event_study.py --cases data/event_cases.json

Honest limits:
  - n is small (extend data/event_cases.json)
  - Social/options/borrow history is incomplete free → static fields only where listed
  - Price/volume features are the discovery edge here (Yahoo history is deep)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_CASES = DATA / "event_cases.json"
OUT_JSON = DATA / "event_study_results.json"
OUT_HTML = ROOT / "event_study_report.html"

sys.path.insert(0, str(ROOT))
from scoring import score_ticker  # noqa: E402


# ── Feature definitions (binary triggers evaluated each day) ─────────────────

@dataclass
class Feature:
    key: str
    label: str
    description: str
    # fn(df_through_today_inclusive) -> bool  (last row = evaluation day)
    test: Callable[[pd.DataFrame], bool]
    # optional continuous value for mean tables
    value: Optional[Callable[[pd.DataFrame], Optional[float]]] = None


def _last(df: pd.DataFrame) -> pd.Series:
    return df.iloc[-1]


def _rvol(df: pd.DataFrame, win: int = 20) -> float:
    if len(df) < win + 1:
        return float("nan")
    vol = df["Volume"].astype(float)
    avg = vol.iloc[-(win + 1) : -1].mean()
    if avg <= 0:
        return float("nan")
    return float(vol.iloc[-1] / avg)


def _atr_pct(df: pd.DataFrame, win: int = 14) -> float:
    if len(df) < win + 1:
        return float("nan")
    h, l, c = df["High"].astype(float), df["Low"].astype(float), df["Close"].astype(float)
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.iloc[-win:].mean()
    px = c.iloc[-1]
    return float(atr / px * 100) if px else float("nan")


def _bb_width(df: pd.DataFrame, win: int = 20) -> float:
    if len(df) < win:
        return float("nan")
    c = df["Close"].astype(float).iloc[-win:]
    mid = c.mean()
    sd = c.std()
    if mid <= 0 or sd != sd:
        return float("nan")
    return float((2 * sd) / mid * 100)


FEATURES: List[Feature] = [
    Feature(
        "rvol_ge_2",
        "RVOL ≥ 2x",
        "Today volume ≥ 2× 20-day average — demand showing up before (or with) price",
        lambda df: (_rvol(df) or 0) >= 2.0,
        lambda df: _rvol(df),
    ),
    Feature(
        "rvol_ge_3",
        "RVOL ≥ 3x",
        "Hard volume spike (≥3×) — live screener already weights this; check lead time",
        lambda df: (_rvol(df) or 0) >= 3.0,
        lambda df: _rvol(df),
    ),
    Feature(
        "rvol_build_3of5",
        "Volume building (3/5 days RVOL≥1.5)",
        "Quiet accumulation: elevated volume on ≥3 of last 5 sessions before the vertical",
        lambda df: _rvol_build(df, days=5, thr=1.5, need=3),
        None,
    ),
    Feature(
        "gap_up_3",
        "Gap up ≥ 3%",
        "Open ≥ 3% above prior close — overnight demand / catalyst tape",
        lambda df: len(df) >= 2
        and float(df["Open"].iloc[-1]) >= float(df["Close"].iloc[-2]) * 1.03,
        lambda df: (
            (float(df["Open"].iloc[-1]) / float(df["Close"].iloc[-2]) - 1) * 100
            if len(df) >= 2 and float(df["Close"].iloc[-2])
            else None
        ),
    ),
    Feature(
        "close_strong",
        "Close in top 20% of day range",
        "Buyers defended the highs into the close (not a wick-and-fail day)",
        lambda df: (
            (float(_last(df)["Close"]) - float(_last(df)["Low"]))
            / max(1e-9, float(_last(df)["High"]) - float(_last(df)["Low"]))
        )
        >= 0.80
        if float(_last(df)["High"]) > float(_last(df)["Low"])
        else False,
        lambda df: (
            (float(_last(df)["Close"]) - float(_last(df)["Low"]))
            / max(1e-9, float(_last(df)["High"]) - float(_last(df)["Low"]))
        ),
    ),
    Feature(
        "ret_3d_ge_8",
        "3-day return ≥ +8%",
        "Price already accelerating into ignition (late entry risk — still a tell)",
        lambda df: len(df) >= 4
        and float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-4]) - 1 >= 0.08,
        lambda df: (
            (float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-4]) - 1) * 100
            if len(df) >= 4
            else None
        ),
    ),
    Feature(
        "ret_5d_flat_vol_up",
        "5d price quiet (±5%) + RVOL≥2",
        "Coiled spring: volume without big price move yet — classic pre-squeeze tape",
        lambda df: len(df) >= 6
        and abs(float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-6]) - 1) <= 0.05
        and (_rvol(df) or 0) >= 2.0,
        None,
    ),
    Feature(
        "higher_lows_3",
        "3 higher lows (last 4 sessions)",
        "Constructive base: each session low above the prior — accumulation structure",
        lambda df: len(df) >= 4
        and all(
            float(df["Low"].iloc[-i]) > float(df["Low"].iloc[-i - 1]) for i in range(1, 4)
        ),
        None,
    ),
    Feature(
        "above_20ma_fresh",
        "Reclaim 20-day MA (was below 3d ago)",
        "Trend flip: price reclaims mean after trading below it",
        lambda df: len(df) >= 23
        and float(df["Close"].iloc[-1]) > float(df["Close"].iloc[-20:].mean())
        and float(df["Close"].iloc[-4]) < float(df["Close"].iloc[-23:-3].mean()),
        None,
    ),
    Feature(
        "atr_expanding",
        "ATR% expanding (≥1.3× its 10d avg)",
        "Volatility regime change — options/gamma often follows",
        lambda df: (
            len(df) >= 30
            and (_atr_pct(df) or 0) >= 1.3 * (
                sum(_atr_pct(df.iloc[: len(df) - i]) or 0 for i in range(1, 11)) / 10.0
            )
        ),
        lambda df: _atr_pct(df),
    ),
    Feature(
        "bb_squeeze",
        "Bollinger width tight (bottom quartile of 60d)",
        "Volatility squeeze (compression) often precedes expansion moves",
        lambda df: (
            len(df) >= 60
            and (_bb_width(df) or 999)
            <= sorted(
                [_bb_width(df.iloc[: len(df) - i]) or 999 for i in range(0, 60)]
            )[15]
        ),
        lambda df: _bb_width(df),
    ),
    Feature(
        "up_vol_dominance",
        "Up-day volume ≥ 1.8× down-day vol (10d)",
        "Demand-side volume: green days carry more shares than red days",
        lambda df: _up_vol_ratio(df) >= 1.8 if not math.isnan(_up_vol_ratio(df)) else False,
        lambda df: _up_vol_ratio(df),
    ),
    Feature(
        "dollar_vol_spike",
        "Dollar volume ≥ 2.5× 20d avg",
        "Real money showing up (price × volume), not just penny-stock share churn",
        lambda df: _dvol_r(df) >= 2.5 if not math.isnan(_dvol_r(df)) else False,
        lambda df: _dvol_r(df),
    ),
    Feature(
        "near_20d_high",
        "Within 3% of 20-day high",
        "Breaking or pressing range highs — breakout setup",
        lambda df: len(df) >= 20
        and float(df["Close"].iloc[-1]) >= float(df["High"].iloc[-20:].max()) * 0.97,
        lambda df: (
            (float(df["Close"].iloc[-1]) / float(df["High"].iloc[-20:].max()) - 1) * 100
            if len(df) >= 20
            else None
        ),
    ),
    Feature(
        "drawdown_recovery",
        "Was ≥15% off 60d high, now recovering (≤8% off)",
        "Failed breakdown / short-cover setup from a washed-out base",
        lambda df: len(df) >= 60
        and float(df["Close"].iloc[-10]) <= float(df["High"].iloc[-60:].max()) * 0.85
        and float(df["Close"].iloc[-1]) >= float(df["High"].iloc[-60:].max()) * 0.92,
        None,
    ),
]


def _rvol_build(df: pd.DataFrame, days: int = 5, thr: float = 1.5, need: int = 3) -> bool:
    if len(df) < 20 + days:
        return False
    hits = 0
    for offset in range(days):
        j = len(df) - 1 - offset
        sub = df.iloc[: j + 1]
        rv = _rvol(sub)
        if rv == rv and rv >= thr:
            hits += 1
    return hits >= need


def _up_vol_ratio(df: pd.DataFrame, win: int = 10) -> float:
    if len(df) < win + 1:
        return float("nan")
    chunk = df.iloc[-win:]
    up = chunk.loc[chunk["Close"] >= chunk["Open"], "Volume"].sum()
    down = chunk.loc[chunk["Close"] < chunk["Open"], "Volume"].sum()
    if down <= 0:
        return 99.0 if up > 0 else float("nan")
    return float(up / down)


def _dvol_r(df: pd.DataFrame, win: int = 20) -> float:
    if len(df) < win + 1:
        return float("nan")
    dvol = df["Close"].astype(float) * df["Volume"].astype(float)
    avg = dvol.iloc[-(win + 1) : -1].mean()
    if avg <= 0:
        return float("nan")
    return float(dvol.iloc[-1] / avg)


# ── Data pull ────────────────────────────────────────────────────────────────

def fetch_history(ticker: str, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
    import yfinance as yf

    try:
        df = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
    except Exception as e:
        print(f"  [warn] {ticker}: {e}")
        return None
    if df is None or df.empty:
        return None
    # yfinance sometimes returns MultiIndex columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.rename(columns=str.title)
    need = {"Open", "High", "Low", "Close", "Volume"}
    if not need.issubset(set(df.columns)):
        return None
    df = df.dropna(subset=["Close", "Volume"])
    return df


def nearest_index(df: pd.DataFrame, target: datetime) -> Optional[int]:
    if df is None or df.empty:
        return None
    # normalize tz-naive
    idx = df.index
    try:
        dates = pd.to_datetime(idx).tz_localize(None)
    except Exception:
        dates = pd.to_datetime(idx)
    target = pd.Timestamp(target).tz_localize(None) if hasattr(pd.Timestamp(target), "tz_localize") else pd.Timestamp(target)
    # find first session on or after target, else last on or before
    on_or_after = [i for i, d in enumerate(dates) if d.date() >= target.date()]
    if on_or_after:
        return on_or_after[0]
    on_or_before = [i for i, d in enumerate(dates) if d.date() <= target.date()]
    return on_or_before[-1] if on_or_before else None


# ── Core study ───────────────────────────────────────────────────────────────

def evaluate_case(
    case: Dict[str, Any],
    lookback: int,
    horizon: int,
) -> Dict[str, Any]:
    t = case["ticker"]
    ign = datetime.strptime(case["ignition_date"][:10], "%Y-%m-%d")
    start = ign - timedelta(days=lookback + 90)  # warm-up for MAs
    end = ign + timedelta(days=horizon + 15)
    print(f"  {t:6} {case['class']:8} ignition {case['ignition_date']} ...")
    df = fetch_history(t, start, end)
    if df is None or len(df) < 40:
        return {"ticker": t, "error": "insufficient history", "class": case["class"]}

    i0 = nearest_index(df, ign)
    if i0 is None or i0 < 30:
        return {"ticker": t, "error": "ignition not in history", "class": case["class"]}

    # realized forward peak from ignition close
    ign_close = float(df["Close"].iloc[i0])
    fwd = df.iloc[i0 : min(len(df), i0 + horizon + 1)]
    peak = float(fwd["High"].max()) if len(fwd) else ign_close
    realized_peak_pct = (peak / ign_close - 1) * 100 if ign_close else 0.0

    # features at each offset days before ignition (0 = ignition day)
    # offset k means using data through session i0 - k
    lead: Dict[str, Optional[int]] = {}  # first day feature True in [lookback..1] (or 0)
    on_ignition: Dict[str, bool] = {}
    values_at: Dict[str, Dict[str, Optional[float]]] = {}  # feature -> {T-3, T-1, T0}

    for feat in FEATURES:
        first = None
        for k in range(lookback, -1, -1):  # far → near → ignition
            j = i0 - k
            if j < 30:
                continue
            sub = df.iloc[: j + 1]
            try:
                hit = bool(feat.test(sub))
            except Exception:
                hit = False
            if hit and first is None and k > 0:
                first = k  # days BEFORE ignition
            if k == 0:
                on_ignition[feat.key] = hit
            if k in (3, 1, 0) and feat.value:
                try:
                    values_at.setdefault(feat.key, {})[f"T-{k}" if k else "T0"] = feat.value(sub)
                except Exception:
                    values_at.setdefault(feat.key, {})[f"T-{k}" if k else "T0"] = None
        lead[feat.key] = first

    # any-of pre-ignition (T-lookback..T-1)
    pre_any = {f.key: lead[f.key] is not None for f in FEATURES}

    # static score if provided
    static_score = None
    static_triggers = []
    if case.get("static"):
        s = {"ticker": t, **case["static"]}
        try:
            r = score_ticker(s, ign.date())
            static_score = r.get("score")
            static_triggers = r.get("trigger_why") or []
        except Exception:
            pass

    return {
        "ticker": t,
        "name": case.get("name"),
        "class": case["class"],
        "ignition_date": case["ignition_date"],
        "notes": case.get("notes"),
        "label_peak_gain_pct": case.get("peak_gain_pct"),
        "realized_peak_pct": round(realized_peak_pct, 1),
        "ignition_close": round(ign_close, 4),
        "lead_days": lead,  # days before ignition first fired (None = never pre)
        "on_ignition": on_ignition,
        "pre_any": pre_any,
        "values_at": values_at,
        "static_score": static_score,
        "static_triggers": static_triggers,
    }


def aggregate(results: List[Dict[str, Any]], lookback: int) -> Dict[str, Any]:
    ok = [r for r in results if "error" not in r]
    sq = [r for r in ok if r["class"] == "squeeze"]
    ct = [r for r in ok if r["class"] == "control"]

    rows = []
    for feat in FEATURES:
        k = feat.key
        sq_hit = [r for r in sq if r["pre_any"].get(k)]
        ct_hit = [r for r in ct if r["pre_any"].get(k)]
        sq_rate = len(sq_hit) / len(sq) if sq else 0
        ct_rate = len(ct_hit) / len(ct) if ct else 0
        lift = sq_rate - ct_rate
        leads = [r["lead_days"][k] for r in sq_hit if r["lead_days"].get(k) is not None]
        med_lead = sorted(leads)[len(leads) // 2] if leads else None
        # mean continuous value at T-3 if available
        def mean_val(group, slot="T-3"):
            vals = []
            for r in group:
                v = (r.get("values_at") or {}).get(k, {}).get(slot)
                if v is not None and v == v and abs(v) < 1e6:
                    vals.append(float(v))
            return round(sum(vals) / len(vals), 3) if vals else None

        rows.append(
            {
                "key": k,
                "label": feat.label,
                "description": feat.description,
                "squeeze_hit_rate": round(sq_rate, 3),
                "control_hit_rate": round(ct_rate, 3),
                "lift": round(lift, 3),
                "squeeze_n": len(sq_hit),
                "control_n": len(ct_hit),
                "median_lead_days": med_lead,
                "mean_val_T-3_squeeze": mean_val(sq),
                "mean_val_T-3_control": mean_val(ct),
                "recommendation": _recommend(sq_rate, ct_rate, med_lead, len(sq)),
            }
        )
    rows.sort(key=lambda x: (-x["lift"], -x["squeeze_hit_rate"]))

    # co-occurrence: best pairs of features among squeezes
    pair_scores = []
    keys = [f.key for f in FEATURES]
    for a_i, a in enumerate(keys):
        for b in keys[a_i + 1 :]:
            sq_both = sum(1 for r in sq if r["pre_any"].get(a) and r["pre_any"].get(b))
            ct_both = sum(1 for r in ct if r["pre_any"].get(a) and r["pre_any"].get(b))
            if sq_both == 0:
                continue
            pair_scores.append(
                {
                    "a": a,
                    "b": b,
                    "squeeze_rate": round(sq_both / len(sq), 3) if sq else 0,
                    "control_rate": round(ct_both / len(ct), 3) if ct else 0,
                    "lift": round(sq_both / len(sq) - (ct_both / len(ct) if ct else 0), 3),
                }
            )
    pair_scores.sort(key=lambda x: -x["lift"])

    return {
        "n_squeeze": len(sq),
        "n_control": len(ct),
        "lookback": lookback,
        "features": rows,
        "top_pairs": pair_scores[:15],
        "cases": ok,
        "errors": [r for r in results if "error" in r],
    }


def _recommend(sq_rate: float, ct_rate: float, med_lead: Optional[int], n_sq: int) -> str:
    lift = sq_rate - ct_rate
    if n_sq < 3:
        return "too few squeezes — collect more cases"
    if lift >= 0.45 and sq_rate >= 0.5 and (med_lead or 0) >= 1:
        return "STRONG CANDIDATE — consider live MONITOR flag (pre-ignition lead)"
    if lift >= 0.30 and sq_rate >= 0.4:
        return "PROMISING — paper-watch as soft flag; need more cases"
    if lift >= 0.15:
        return "WEAK EDGE — keep in research; don't score-weight yet"
    if sq_rate >= 0.6 and ct_rate >= 0.5:
        return "COMMON TAPE — fires on squeezes AND duds; poor discriminator"
    return "NO EDGE on this sample"


def write_html(agg: Dict[str, Any], out: Path) -> None:
    feats = agg["features"]
    rows = []
    for f in feats:
        color = (
            "#2ed573"
            if "STRONG" in f["recommendation"]
            else "#ffa502"
            if "PROMISING" in f["recommendation"]
            else "#747d8c"
        )
        rows.append(
            f"<tr>"
            f"<td><b>{_e(f['label'])}</b><div class='desc'>{_e(f['description'])}</div></td>"
            f"<td class='num'>{f['squeeze_hit_rate']*100:.0f}% ({f['squeeze_n']}/{agg['n_squeeze']})</td>"
            f"<td class='num'>{f['control_hit_rate']*100:.0f}% ({f['control_n']}/{agg['n_control']})</td>"
            f"<td class='num'><b style='color:{color}'>{f['lift']*100:+.0f}pp</b></td>"
            f"<td class='num'>{f['median_lead_days'] if f['median_lead_days'] is not None else '—'}</td>"
            f"<td>{_e(f['recommendation'])}</td>"
            f"</tr>"
        )
    case_rows = []
    for c in agg["cases"]:
        fired = [k for k, v in (c.get("pre_any") or {}).items() if v]
        top = ", ".join(fired[:6]) if fired else "-"
        case_rows.append(
            f"<tr><td><b>{_e(c['ticker'])}</b></td><td>{_e(c['class'])}</td>"
            f"<td>{_e(c['ignition_date'])}</td>"
            f"<td class='num'>{c.get('realized_peak_pct')}</td>"
            f"<td class='num'>{c.get('static_score') if c.get('static_score') is not None else '—'}</td>"
            f"<td>{_e(top)}</td></tr>"
        )
    pairs = "".join(
        f"<li><code>{_e(p['a'])}</code> + <code>{_e(p['b'])}</code> "
        f"— squeeze {p['squeeze_rate']*100:.0f}% / control {p['control_rate']*100:.0f}% "
        f"(lift {p['lift']*100:+.0f}pp)</li>"
        for p in (agg.get("top_pairs") or [])[:8]
    )
    strong = [f for f in feats if "STRONG" in f["recommendation"] or "PROMISING" in f["recommendation"]]
    takeaways = "".join(f"<li><b>{_e(f['label'])}</b> — {_e(f['recommendation'])} "
                        f"(lift {f['lift']*100:+.0f}pp, median lead {f['median_lead_days']}d)</li>"
                        for f in strong) or "<li>No strong candidates on this sample — add cases.</li>"

    doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Event Study — Trigger Mine</title>
<style>
 body{{background:#0f1117;color:#dfe4ea;font-family:Segoe UI,system-ui,sans-serif;margin:0;padding:24px}}
 h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:15px;color:#ffa502;margin:22px 0 10px}}
 .sub{{color:#747d8c;font-size:13px;margin-bottom:16px}}
 table{{border-collapse:collapse;width:100%;font-size:12.5px;margin-bottom:8px}}
 th{{text-align:left;color:#a4b0be;padding:8px;border-bottom:2px solid #2f3542}}
 td{{padding:8px;border-bottom:1px solid #1e2430;vertical-align:top}}
 .num{{font-variant-numeric:tabular-nums;white-space:nowrap}}
 .desc{{color:#747d8c;font-size:11px;margin-top:3px}}
 .box{{background:#12161f;border:1px solid #2f3542;border-radius:8px;padding:14px 16px;margin:14px 0}}
 code{{background:#1e2430;padding:1px 5px;border-radius:3px;font-size:11px}}
 li{{margin:6px 0}}
</style></head><body>
<h1>🔬 Event-study trigger mine</h1>
<div class="sub">{agg['n_squeeze']} squeezes · {agg['n_control']} controls ·
pre-ignition window T−{agg['lookback']}..T−1 · price/volume features from Yahoo history</div>

<div class="box">
<h2 style="margin-top:0">Takeaways (candidates for new live flags)</h2>
<ul>{takeaways}</ul>
<p style="color:#747d8c;font-size:12px;margin:8px 0 0">
Do <b>not</b> reweight the live score from this alone (small-n). Use STRONG/PROMISING as
<b>MONITOR soft flags</b> or paper-watch rules, then confirm on the live ledger.
</p>
</div>

<h2>Feature discrimination (sorted by lift = squeeze rate − control rate)</h2>
<table>
<tr><th>Feature</th><th>Squeeze hit</th><th>Control hit</th><th>Lift</th><th>Median lead</th><th>Verdict</th></tr>
{''.join(rows)}
</table>

<h2>Best feature pairs (both true sometime pre-ignition)</h2>
<ul>{pairs or '<li>None</li>'}</ul>

<h2>Per-case</h2>
<table>
<tr><th>Ticker</th><th>Class</th><th>Ignition</th><th>Realized peak%</th><th>Static score</th><th>Pre features (sample)</th></tr>
{''.join(case_rows)}
</table>

<div class="box" style="color:#747d8c;font-size:12px">
<b>How to read lift:</b> +50pp means the feature fired before the move on 50 percentage points
more squeezes than controls. <b>Median lead</b> = typical days before ignition it first turned on
(higher = more useful as an early warning).<br><br>
Social/gamma/borrow history is incomplete free — those stay in the static case scores.
This study mines <b>tape structure</b> the live screener largely ignores today.
</div>
</body></html>"""
    out.write_text(doc, encoding="utf-8")


def _e(x: Any) -> str:
    import html as H

    return H.escape(str(x if x is not None else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="Event-study trigger miner")
    ap.add_argument("--cases", default=str(DEFAULT_CASES))
    ap.add_argument("--lookback", type=int, default=12, help="Trading-day lookback before ignition")
    ap.add_argument("--horizon", type=int, default=10, help="Forward days for realized peak")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))["cases"]
    print("=" * 72)
    print(f"EVENT STUDY  ·  {len(cases)} cases  ·  lookback {args.lookback}d")
    print("=" * 72)

    results = [evaluate_case(c, args.lookback, args.horizon) for c in cases]
    agg = aggregate(results, args.lookback)

    OUT_JSON.write_text(json.dumps(agg, indent=2, default=str), encoding="utf-8")
    write_html(agg, OUT_HTML)

    print("\n-- FEATURE LIFT (squeeze hit - control hit) -----------------")
    print(f"  {'feature':36} {'sq%':>6} {'ct%':>6} {'lift':>7} {'lead':>5}  verdict")
    for f in agg["features"]:
        lead = str(f["median_lead_days"]) if f["median_lead_days"] is not None else "-"
        print(
            f"  {f['label'][:36]:36} {f['squeeze_hit_rate']*100:5.0f}% {f['control_hit_rate']*100:5.0f}% "
            f"{f['lift']*100:+6.0f}pp {lead:>5}  {f['recommendation']}"
        )

    print("\n-- TOP PAIRS ------------------------------------------------")
    for p in (agg.get("top_pairs") or [])[:6]:
        print(
            f"  {p['a']} + {p['b']}:  "
            f"sq {p['squeeze_rate']*100:.0f}% / ct {p['control_rate']*100:.0f}%  lift {p['lift']*100:+.0f}pp"
        )

    strong = [f for f in agg["features"] if "STRONG" in f["recommendation"] or "PROMISING" in f["recommendation"]]
    print("\n-- CANDIDATES TO CONSIDER AS NEW TRIGGERS -------------------")
    if not strong:
        print("  (none cleared the bar on this sample - add more cases or loosen thresholds)")
    for f in strong:
        print(f"  * {f['label']}")
        print(f"      {f['description']}")
        print(f"      -> {f['recommendation']}")

    if agg.get("errors"):
        print("\n-- DATA ERRORS ----------------------------------------------")
        for e in agg["errors"]:
            print(f"  {e['ticker']}: {e['error']}")

    print(f"\n  JSON -> {OUT_JSON}")
    print(f"  HTML -> {OUT_HTML}")
    print("  Reminder: small-n evidence only - don't reweight live scores from this alone.\n")

    if not args.no_browser:
        try:
            webbrowser.open(OUT_HTML.as_uri())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
