"""
Short-squeeze scoring engine.

DNA calibrated on GME / SPRT / BBBY / AMC / WEN:
  Mechanics 50  +  Social 50  +  Gamma bonus 12

Fuel  = SI + float (slow powder keg)
Ignition = volume + WSB velocity + gamma + catalyst (what moves this week)

Tiers:  >=70 RED HOT | 55-69 WATCH | 40-54 MONITOR | <40 BACKGROUND
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

# Brand / meme DNA — retail narrative fuel (not a buy signal alone)
BRAND_10 = {
    "WEN", "GME", "AMC", "BB", "BBBY", "NOK", "HTZ", "KSS", "FUN", "XRX", "BYND",
    "PLCE", "FLWS", "GRPN", "SPCE", "LCID", "CPB", "DNUT", "OPEN", "M", "JWN",
    "BIRD", "CHWY", "PTON", "WISH", "CLOV", "SDC", "EXPR", "KOSS", "TUP", "RAD",
    "BGFV", "DDS", "ANF",
}
BRAND_5 = {
    "AI", "SOUN", "NVAX", "RUN", "MARA", "HIMS", "SATS", "TTD", "VKTX", "SRPT",
    "LOVE", "SERV", "EVGO", "PLUG", "ODD", "SPHR", "CVNA", "RDDT", "HOOD", "SOFI",
}

TIER_ORDER = ["BACKGROUND", "MONITOR", "WATCH", "RED HOT"]
EXIT_DOCTRINE = "SPRT rule: sell half at 3-4x, trail the rest. Exit matters more than entry."


def tier(score: float) -> str:
    if score >= 70:
        return "RED HOT"
    if score >= 55:
        return "WATCH"
    if score >= 40:
        return "MONITOR"
    return "BACKGROUND"


def score_mechanics(s: Dict[str, Any]) -> Tuple[float, Dict[str, float], List[str]]:
    """SI% of float (25) · float tightness (15) · DTC + volume spike (10)."""
    si = s.get("si_pct") or 0.0
    fl = s.get("float_m")
    pts = 0.0
    notes: List[str] = []

    si_pts = min(25.0, max(0.0, (si - 15.0) * 0.5))
    pts += si_pts

    if fl is None:
        fl_pts = 0.0
    elif fl < 5:
        fl_pts = 15.0
    elif fl < 15:
        fl_pts = 13.0
    elif fl < 30:
        fl_pts = 11.0
    elif fl < 60:
        fl_pts = 8.0
    elif fl < 120:
        fl_pts = 5.0
    elif fl < 250:
        fl_pts = 2.0
    else:
        fl_pts = 0.0
    pts += fl_pts

    vol_pts = 0.0
    avg_vol = s.get("avg_vol_m")
    vol = s.get("vol_m")
    if avg_vol and fl and si:
        dtc = (si / 100.0 * fl) / avg_vol
        s["dtc"] = round(dtc, 1)
        if dtc >= 8:
            vol_pts += 5
        elif dtc >= 4:
            vol_pts += 3
        elif dtc >= 2:
            vol_pts += 1
        if vol:
            spike = vol / avg_vol
            s["vol_spike"] = round(spike, 1)
            if spike >= 10:
                vol_pts += 5
            elif spike >= 5:
                vol_pts += 4
            elif spike >= 3:
                vol_pts += 3
            elif spike >= 1.5:
                vol_pts += 1
            if spike >= 5:
                s.setdefault("flags", []).append("VOLUME SPIKE  -  may already be moving")
    else:
        notes.append("incomplete volume/float (vol pillar capped)")
    pts += vol_pts

    return round(pts, 1), {"si": round(si_pts, 1), "float": fl_pts, "vol": round(vol_pts, 1)}, notes


def score_social(s: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
    """WSB rank (10) · mention velocity (20) · upvote heat (5) · brand DNA (10)."""
    t = s["ticker"]
    rank = s.get("wsb_rank")
    m = s.get("mentions") or 0
    m24 = s.get("mentions_24h")
    r24 = s.get("rank_24h")
    up = s.get("upvotes") or 0
    pts = 0.0

    if rank is None:
        rank_pts = 0.0
    elif rank <= 5:
        rank_pts = 10.0
    elif rank <= 10:
        rank_pts = 8.0
    elif rank <= 25:
        rank_pts = 6.0
    elif rank <= 50:
        rank_pts = 4.0
    elif rank <= 100:
        rank_pts = 3.0
    elif rank <= 200:
        rank_pts = 2.0
    else:
        rank_pts = 1.0
    pts += rank_pts

    vel_pts = 0.0
    if m:
        ratio = m / max(1, (m24 if m24 is not None else m))
        s["velocity_ratio"] = round(ratio, 2)
        if ratio >= 8:
            vel_pts = 20.0
        elif ratio >= 5:
            vel_pts = 16.0
        elif ratio >= 3:
            vel_pts = 12.0
        elif ratio >= 2:
            vel_pts = 8.0
        elif ratio >= 1.5:
            vel_pts = 5.0
        elif ratio >= 1:
            vel_pts = 2.0
        if r24 and rank and r24 - rank >= 100 and m >= 5:
            vel_pts = max(vel_pts, 14.0)
        if m < 3:
            vel_pts = min(vel_pts, 6.0)
        if vel_pts >= 12:
            s.setdefault("flags", []).append("WSB CHATTER ACCELERATING")
    pts += vel_pts

    heat = up / m if m else 0
    up_pts = 5.0 if heat >= 20 else 3.0 if heat >= 10 else 2.0 if heat >= 5 else 1.0 if heat >= 1 else 0.0
    pts += up_pts

    # StockTwits / X early social — soft boost into brand bucket (cap social at ~50)
    if s.get("stocktwits_trending") and "ST TRENDING (pre-WSB)" not in (s.get("flags") or []):
        s.setdefault("flags", []).append("ST TRENDING (pre-WSB)")
    brand_pts = 10.0 if t in BRAND_10 else 5.0 if t in BRAND_5 else 0.0
    # X buzz soft points (flags mostly applied in x_sentiment.apply_x_fields)
    xb = s.get("x_buzz") or 0
    x_soft = 0.0
    if xb >= 75:
        x_soft = 4.0
    elif xb >= 60:
        x_soft = 3.0
    elif xb >= 45:
        x_soft = 2.0
    if s.get("x_trend") == "rising" and xb >= 40:
        x_soft = min(5.0, x_soft + 1.0)
    if s.get("x_accel") and s["x_accel"] >= 1.5 and xb >= 30:
        x_soft = min(5.0, x_soft + 1.0)
    if x_soft and brand_pts < 10:
        brand_pts = min(10.0, brand_pts + min(x_soft, 4.0))
    if s.get("stocktwits_trending") and brand_pts < 10:
        brand_pts = min(10.0, brand_pts + 2.0)
    pts += brand_pts

    return round(pts, 1), {
        "rank": rank_pts,
        "velocity": round(vel_pts, 1),
        "upvotes": up_pts,
        "brand": brand_pts,
    }


def score_gamma(s: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
    """
    Options/gamma tell — weekly calls bid BEFORE the vertical (GME/SPRT DNA).
    Near-dated call OI vs float · call volume share · premium vs mcap · ATM IV.
    Gamma >= 7 -> GAMMA RAMP flag.
    """
    oi = s.get("near_call_oi_k")
    share = s.get("call_share")
    prem = s.get("near_call_prem_m")
    iv = s.get("atm_iv_pct")
    fl = s.get("float_m")
    mcap = s.get("mcap_b")
    pts = 0.0
    br = {"oi_float": 0.0, "call_share": 0.0, "premium": 0.0, "iv": 0.0}

    if oi and fl:
        # oi_k * 100 shares / (float_m * 1e6) as %
        oi_pct = oi * 10.0 / fl
        s["call_oi_pct_float"] = round(oi_pct, 1)
        p = 4.0 if oi_pct >= 20 else 3.0 if oi_pct >= 10 else 2.0 if oi_pct >= 5 else 1.0 if oi_pct >= 2 else 0.0
        br["oi_float"] = p
        pts += p
    if share is not None:
        p = 3.0 if share >= 0.8 else 2.0 if share >= 0.7 else 1.0 if share >= 0.6 else 0.0
        br["call_share"] = p
        pts += p
    if prem is not None and mcap:
        ratio = prem / (mcap * 1000.0) * 100.0  # % of mcap
        s["prem_pct_mcap"] = round(ratio, 3)
        p = 3.0 if ratio >= 0.1 else 2.0 if ratio >= 0.03 else 1.0 if ratio >= 0.01 else 0.0
        br["premium"] = p
        pts += p
    if iv:
        p = 2.0 if iv >= 200 else 1.5 if iv >= 120 else 1.0 if iv >= 80 else 0.0
        br["iv"] = p
        pts += p

    if pts >= 7:
        s.setdefault("flags", []).append("GAMMA RAMP  -  weekly calls bid")
    return round(pts, 1), br


def check_catalyst(s: Dict[str, Any], asof: date) -> None:
    ed = s.get("earnings_date") or s.get("catalyst_date")
    if not ed:
        return
    try:
        d = datetime.strptime(str(ed)[:10], "%Y-%m-%d").date()
    except ValueError:
        return
    dd = (d - asof).days
    if 0 <= dd <= 2:
        s["pre_catalyst"] = True
        label = s.get("catalyst_type") or "earnings"
        s.setdefault("flags", []).append(f"PRE-CATALYST: {label} {ed} ({dd}d out)")


def apply_delta_flags(s: Dict[str, Any], prev: Optional[Dict[str, Any]]) -> None:
    """Day-over-day transitions — the money layer. Transitions beat standings."""
    if not prev:
        return
    fee, fee_p = s.get("borrow_fee_pct"), prev.get("borrow_fee_pct")
    if fee and fee_p and fee >= fee_p * 1.5:
        s.setdefault("flags", []).append(f"BORROW FEE RISING {fee_p}% -> {fee}%")
    av, av_p = s.get("shares_avail_k"), prev.get("shares_avail_k")
    if av is not None and av_p and av <= av_p * 0.35:
        s.setdefault("flags", []).append(f"BORROW AVAIL COLLAPSING {av_p}K -> {av}K")
    oi, oi_p = s.get("near_call_oi_k"), prev.get("near_call_oi_k")
    if oi and oi_p and oi >= oi_p * 1.2:
        s.setdefault("flags", []).append(f"CALL OI BUILDING +{(oi / oi_p - 1) * 100:.0f}% d/d")
    vr, vr_p = s.get("velocity_ratio"), prev.get("velocity_ratio")
    if vr and vr_p and vr >= 2 and vr >= vr_p * 2:
        s.setdefault("flags", []).append("VELOCITY ACCELERATING d/d")


def _has_squeeze_fuel(r: Dict[str, Any]) -> bool:
    """Gate triggers: mega-caps with chatter ≠ short squeeze DNA."""
    si = r.get("si_pct") or 0
    fl = r.get("float_m")
    fuel = r.get("fuel")
    if fuel is not None and fuel >= 12:
        return True
    if si >= 15:
        return True
    if fl is not None and fl < 80 and si >= 10:
        return True
    fee = r.get("borrow_fee_pct") or 0
    if fee >= 5 and si >= 12:
        return True
    return False


def trigger_reasons(r: Dict[str, Any]) -> List[str]:
    """Ignition *alerts* — research now, NOT buy recommendations.

    Surfaces ABOVE the ranked list regardless of tier. Requires squeeze fuel
    (SI/float) so mega-cap earnings chatter never lands here.

    Wording is deliberate: LOOK / HEAT — never 'buy' or 'entry order'.
    """
    reasons = []
    if not _has_squeeze_fuel(r):
        return reasons
    vel = r.get("soc_breakdown", {}).get("velocity", 0)
    if vel >= 16 and r.get("vol_spike", 0) >= 3:
        reasons.append("LOOK: velocity >=5x + volume >=3x")
    if vel >= 12 and r.get("pre_catalyst"):
        reasons.append("LOOK: chatter accelerating into dated catalyst")
    if r.get("gamma", 0) >= 7:
        reasons.append("LOOK: gamma ramp (weekly calls bid)")
    # Hard-to-borrow + volume without needing WSB top (structural squeeze start)
    fee = r.get("borrow_fee_pct") or 0
    if fee >= 20 and r.get("vol_spike", 0) >= 3 and (r.get("si_pct") or 0) >= 20:
        reasons.append("LOOK: hard-to-borrow (>=20% fee) + volume spike + high SI")
    # X / FinTwit heat (social edge leg — alone is weak without fuel score)
    xb = r.get("x_buzz") or 0
    if xb >= 70 and r.get("x_trend") == "rising":
        reasons.append(f"HEAT: X buzz {xb:.0f} + rising (FinTwit  -  research, not a buy)")
    accel = r.get("x_accel")
    if accel is not None and accel >= 1.5 and xb >= 50:
        reasons.append(f"HEAT: X accelerating {accel:.1f}x vs 3d (buzz {xb:.0f})")
    # Tape microstructure from event-study (soft -> alert only as pair setups)
    if r.get("tape_coil_breakout"):
        reasons.append("LOOK: tape coil+breakout (BB squeeze + near 20d high)")
    if r.get("tape_structure") and (r.get("tape_rvol") or 0) >= 1.5:
        reasons.append("LOOK: tape structure (reclaim 20-MA + higher lows) + volume")
    return reasons


def entry_markers(r: Dict[str, Any]) -> List[str]:
    """Human-readable setup markers (watchlist quality, not auto-buy)."""
    m = []
    si = r.get("si_pct") or 0
    fl = r.get("float_m")
    dtc = r.get("dtc")
    if si >= 30:
        m.append(f"Heavy SI {si:.0f}% of float")
    elif si >= 20:
        m.append(f"Elevated SI {si:.0f}%")
    if fl is not None and fl < 30:
        m.append(f"Tight float {fl}M")
    if dtc and dtc >= 4:
        m.append(f"Days-to-cover {dtc}d (crowded short book)")
    if r.get("vol_spike", 0) >= 3:
        m.append(f"Volume {r['vol_spike']}x avg")
    if r.get("soc_breakdown", {}).get("velocity", 0) >= 12:
        m.append("WSB chatter accelerating")
    if r.get("gamma", 0) >= 7:
        m.append("Gamma ramp building")
    if r.get("pre_catalyst"):
        m.append("Dated catalyst <=2 days")
    if r.get("borrow_fee_pct") and r["borrow_fee_pct"] >= 5:
        m.append(f"Borrow fee {r['borrow_fee_pct']}%")
    if r.get("x_buzz") is not None and r["x_buzz"] >= 45:
        tr = r.get("x_trend") or "?"
        m.append(f"X buzz {r['x_buzz']:.0f} ({tr})")
    if r.get("x_accel") and r["x_accel"] >= 1.5:
        m.append(f"X accel {r['x_accel']:.1f}x")
    if r.get("tape_coil_breakout"):
        m.append("Tape coil+breakout setup")
    if r.get("tape_reclaim_20ma"):
        m.append("Reclaiming 20-day MA")
    if r.get("tape_bb_squeeze"):
        m.append("BB volatility compression")
    if r.get("tape_near_20high"):
        m.append("Pressing 20-day high")
    return m


def exit_signals(r: Dict[str, Any], pos: Optional[Dict[str, Any]] = None) -> List[str]:
    """Exit / de-risk markers. Dilution is force-RED."""
    pos = pos or {}
    sigs: List[str] = []
    df = r.get("dilution_filing")
    if df:
        sigs.append(
            f"EXIT: DILUTION FILING {df.get('form')} filed {df.get('date')}  -  sell first, ask later"
        )
    m = r.get("mentions")
    m24 = r.get("mentions_24h")
    if m and m24 is not None:
        ratio = m / max(1, m24)
        if ratio < 1.0:
            sigs.append(f"EXIT: social fading (velocity {ratio:.1f}x  -  crowd leaving)")
    if r.get("vol_spike", 0) >= 5 and (r.get("chg_pct") or 0) >= 25:
        sigs.append(
            f"EXIT: parabolic on {r['vol_spike']}x volume  -  distribution window (squeezes die in 1-3 sessions)"
        )
    fee = r.get("borrow_fee_pct")
    fee_p = r.get("borrow_fee_prev_pct")
    if fee and fee_p and fee <= fee_p * 0.7:
        sigs.append(f"EXIT: borrow fee normalizing ({fee_p}% -> {fee}%)")
    av = r.get("shares_avail_k")
    av_p = r.get("shares_avail_prev_k")
    if av and av_p and av >= av_p * 5:
        sigs.append(f"EXIT: borrow availability rebounding ({av_p}K -> {av}K)")
    if r.get("call_share") is not None and r["call_share"] <= 0.45:
        sigs.append(f"EXIT: options flow flipped to puts (call share {r['call_share']:.2f})")
    entry = pos.get("entry")
    px = r.get("price")
    if entry and px and px / entry >= 3:
        sigs.append(f"EXIT: at {px / entry:.1f}x entry  -  {EXIT_DOCTRINE}")
    # X trend falling corroborates social fade
    if r.get("x_trend") == "falling" and m and m24 is not None and m / max(1, m24) < 1.2:
        if not any("social fading" in x for x in sigs):
            sigs.append("EXIT: X sentiment rolling over while social flat/fading")
    xb = r.get("x_buzz") or 0
    if xb >= 70 and r.get("x_trend") == "falling" and (r.get("x_mentions") or 0) >= 50:
        sigs.append("EXIT: X exhaustion signature (high buzz + falling trend)")
    if r.get("x_accel") is not None and r["x_accel"] <= 0.65 and xb >= 50:
        sigs.append(f"EXIT: X buzz collapsing ({r['x_accel']:.1f}x vs 3d)")
    return sigs


def score_ticker(
    s: Dict[str, Any],
    asof: date,
    prior: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    s = dict(s)
    s.setdefault("flags", [])
    if s.get("mentions") and s.get("mentions_24h") is not None:
        s["velocity_ratio"] = round(s["mentions"] / max(1, s["mentions_24h"]), 2)
    apply_delta_flags(s, prior)
    check_catalyst(s, asof)
    mech, mech_br, notes = score_mechanics(s)
    soc, soc_br = score_social(s)
    gam, gam_br = score_gamma(s)
    total = round(mech + soc + gam, 1)
    fuel = round(mech_br["si"] + mech_br["float"], 1)
    ign = round(
        mech_br["vol"] + soc_br["velocity"] + gam + (5 if s.get("pre_catalyst") else 0),
        1,
    )
    cur_tier = tier(total)
    if prior and prior.get("tier") in TIER_ORDER and cur_tier in TIER_ORDER:
        if TIER_ORDER.index(cur_tier) - TIER_ORDER.index(prior["tier"]) >= 2:
            s.setdefault("flags", []).append(f"TIER JUMP {prior['tier']} -> {cur_tier}")

    result = {
        **s,
        "mech": mech,
        "soc": soc,
        "gamma": gam,
        "score": total,
        "fuel": fuel,
        "ign": ign,
        "tier": cur_tier,
        "mech_breakdown": mech_br,
        "soc_breakdown": soc_br,
        "gamma_breakdown": gam_br,
        "notes": notes,
    }
    result["entry_markers"] = entry_markers(result)
    result["exit_signals"] = exit_signals(result, {})
    result["trigger_why"] = trigger_reasons(result)
    return result


def rank_universe(
    rows: List[Dict[str, Any]],
    asof: Optional[date] = None,
    prior_by_ticker: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    asof = asof or date.today()
    prior_by_ticker = prior_by_ticker or {}
    results = [score_ticker(r, asof, prior_by_ticker.get(r["ticker"])) for r in rows]
    results.sort(key=lambda r: -r["score"])
    triggers = [
        {
            "ticker": r["ticker"],
            "why": r["trigger_why"],
            "score": r["score"],
            "tier": r["tier"],
            "price": r.get("price"),
        }
        for r in results
        if r["trigger_why"]
    ]
    return results, triggers


# ── Backtest anchors (sanity-check the engine still separates DNA from noise) ──
BACKTEST_CASES = [
    {
        "ticker": "GME",
        "name": "GameStop Jan-2021",
        "si_pct": 140,
        "float_m": 47,
        "avg_vol_m": 25,
        "vol_m": 150,
        "wsb_rank": 1,
        "mentions": 500,
        "mentions_24h": 90,
        "rank_24h": 3,
        "upvotes": 20000,
        "near_call_oi_k": 300,
        "call_share": 0.85,
        "near_call_prem_m": 80,
        "atm_iv_pct": 400,
        "mcap_b": 1.5,
    },
    {
        "ticker": "SPRT",
        "name": "Support.com Aug-2021",
        "si_pct": 60,
        "float_m": 10,
        "avg_vol_m": 0.7,
        "vol_m": 5,
        "wsb_rank": 9,
        "mentions": 120,
        "mentions_24h": 25,
        "rank_24h": 60,
        "upvotes": 1500,
        "near_call_oi_k": 40,
        "call_share": 0.8,
        "near_call_prem_m": 3,
        "atm_iv_pct": 250,
        "mcap_b": 0.2,
    },
    {
        "ticker": "BBBY",
        "name": "Bed Bath Aug-2022",
        "si_pct": 52,
        "float_m": 77,
        "avg_vol_m": 15,
        "vol_m": 120,
        "wsb_rank": 2,
        "mentions": 400,
        "mentions_24h": 100,
        "rank_24h": 8,
        "upvotes": 9000,
    },
    {
        "ticker": "WEN",
        "name": "Wendy's 24-Jun-2026 (day 1)",
        "si_pct": 32,
        "float_m": 160,
        "avg_vol_m": 13,
        "vol_m": 202,
        "wsb_rank": 1,
        "mentions": 200,
        "mentions_24h": 15,
        "rank_24h": 120,
        "upvotes": 8000,
    },
    {
        "ticker": "AMC",
        "name": "AMC peak-era profile",
        "si_pct": 20,
        "float_m": 450,
        "avg_vol_m": 80,
        "vol_m": 400,
        "wsb_rank": 2,
        "mentions": 350,
        "mentions_24h": 80,
        "rank_24h": 5,
        "upvotes": 12000,
        "near_call_oi_k": 200,
        "call_share": 0.75,
        "near_call_prem_m": 40,
        "atm_iv_pct": 180,
        "mcap_b": 8,
    },
    {
        "ticker": "XYZ",
        "name": "control: boring 22% SI biotech, no chatter",
        "si_pct": 22,
        "float_m": 150,
        "avg_vol_m": 2,
        "vol_m": 2,
        "wsb_rank": None,
        "mentions": 0,
    },
]


def run_backtest() -> None:
    print(f"{'ticker':6} {'case':44} {'mech':>5} {'soc':>5} {'gam':>4} {'TOTAL':>6}  tier / triggers")
    for case in BACKTEST_CASES:
        r = score_ticker(dict(case), date(2021, 1, 25))
        trig = r["trigger_why"]
        print(
            f"{r['ticker']:6} {case['name']:44} {r['mech']:5} {r['soc']:5} {r['gamma']:4} {r['score']:6}  {r['tier']}"
            + (f"  << {'; '.join(trig)}" if trig else "")
        )
