#!/usr/bin/env python3
"""
Meme Squeeze Screener - scores stocks on the common DNA of GME/AMC/SPRT/BBBY/WEN squeezes.
Composite score = mechanics (50) + social (50) + GAMMA bonus (12).

MECHANICS (50)
  SI %% of float (25)  min(25, max(0, (si - 15) * 0.5))
  Float tightness (15) <5M:15 <15M:13 <30M:11 <60M:8 <120M:5 <250M:2 else 0
  Volume/DTC (10)      DTC>=8d:5 >=4:3 >=2:1  +  vol spike >=10x:5 >=5x:4 >=3x:3 >=1.5x:1

SOCIAL (50)
  WSB rank (10) | Mention velocity vs 24h ago (20) | Upvote heat (5) | Brand/meme DNA (10)

GAMMA (bonus 12) - the options tell. GME/SPRT/BBBY all showed weekly call premiums
bid hard BEFORE the vertical move (dealer hedging = the ramp). Fields from daily
CBOE delayed-quotes fetch: near_call_oi_k (call OI expiring <=14d, thousands of
contracts), call_share (call vol / total opt vol), near_call_prem_m ($M premium
traded in <=14d calls), atm_iv_pct (nearest-expiry ATM call IV).
  Near call OI as %% of float (4): >=20%:4 >=10%:3 >=5%:2 >=2%:1
  Call share (3): >=0.8:3 >=0.7:2 >=0.6:1
  Near call premium vs mcap (3): >=0.1%:3 >=0.03%:2 >=0.01%:1
  ATM IV (2): >=200:2 >=120:1.5 >=80:1
  Gamma >= 7 -> GAMMA RAMP flag.

PRE-CATALYST - enrichment field earnings_date (YYYY-MM-DD); flag when 0-2 days out.
TRIGGER STACK - names surfaced ABOVE the ranked list regardless of tier:
  velocity>=5x AND vol spike>=3x | chatter accelerating INTO a dated catalyst | gamma ramp.
  Lesson from PENG 07-Jul-2026: it sat at #2 MONITOR with 10x velocity + 3.7x volume
  + earnings that night; squeeze-weighted score buried the hottest signal on the board.

Tiers: >=70 RED HOT | 55-69 WATCH | 40-54 MONITOR | <40 BACKGROUND
Usage: python3 screener.py snapshot.json   |   python3 screener.py --backtest
"""
import json, sys, html
from datetime import date, datetime
from pathlib import Path

HISTORY_FILE = "history.jsonl"

def load_history(snap_dir):
    p = Path(snap_dir) / HISTORY_FILE
    if not p.exists(): return []
    by = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                r = json.loads(line); by[r["date"]] = r
            except Exception: pass
    return [by[d] for d in sorted(by)]

def save_history(snap_dir, hist):
    (Path(snap_dir) / HISTORY_FILE).write_text("\n".join(json.dumps(r) for r in hist) + "\n")

PAPER_FILE = "paper_trades.json"

def update_paper_trades(results, triggers, snap_dir, asof_str):
    """Auto paper-trade every TRIGGER STACK appearance. Measures hit rate without capital.
    Mechanical rules: enter at scan price on trigger; exit on first exit signal,
    -25% stop, or 14-day time stop. One unit each; no pyramiding; no same-day reopen."""
    p = Path(snap_dir) / PAPER_FILE
    try:
        data = json.loads(p.read_text()) if p.exists() else {"open": [], "closed": []}
    except Exception:
        data = {"open": [], "closed": []}
    idx = {r["ticker"]: r for r in results}
    asof = datetime.strptime(asof_str, "%Y-%m-%d").date()
    still = []
    closed_today = set()
    for tr in data["open"]:
        r = idx.get(tr["ticker"], {})
        px = r.get("price") or tr.get("last_price")
        if r.get("price"):
            tr["last_price"] = r["price"]; tr["last_date"] = asof_str
        days = (asof - datetime.strptime(tr["entry_date"], "%Y-%m-%d").date()).days
        sigs = exit_signals(r, {}) if r else []
        reason = None
        if px and px <= tr["entry"] * 0.75: reason = "stop -25%"
        elif sigs: reason = "exit signal: " + sigs[0]
        elif days >= 14: reason = "time stop 14d"
        if reason and px:
            tr.update(exit=px, exit_date=asof_str, exit_reason=reason,
                      pl_pct=round((px / tr["entry"] - 1) * 100, 1))
            data["closed"].append(tr); closed_today.add(tr["ticker"])
        else:
            still.append(tr)
    data["open"] = still
    open_tk = {t["ticker"] for t in data["open"]}
    # Ledger-hygiene guards (approved by Alan 2026-07-10, IDEAS.md #2):
    # (a) never open a trigger trade while an exit signal is simultaneously active
    #     (intra-scan re-runs were closing on the signal then reopening from the stack);
    # (b) never reopen a ticker already closed today, across runs, not just within one run.
    closed_same_day = {t["ticker"] for t in data["closed"] if t.get("exit_date") == asof_str}
    for t in triggers:
        r = idx.get(t["ticker"])
        px = r.get("price") if r else None
        live_sigs = exit_signals(r, {}) if r else []
        if (px and not live_sigs and t["ticker"] not in open_tk
                and t["ticker"] not in closed_today and t["ticker"] not in closed_same_day):
            data["open"].append({"ticker": t["ticker"], "entry": px, "entry_date": asof_str,
                                 "last_price": px, "last_date": asof_str,
                                 "reason": "; ".join(t["why"]), "score": t["score"]})
    p.write_text(json.dumps(data, indent=1))
    return data

def paper_stats(closed):
    closed = [t for t in closed if not t.get("artifact")]  # skip re-run artifacts (IDEAS.md #2)
    n = len(closed)
    if not n: return None
    rets = [t["pl_pct"] for t in closed]
    wins = [x for x in rets if x > 0]; losses = [x for x in rets if x <= 0]
    p_win = len(wins) / n
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(losses) / len(losses) if losses else 0.0
    exp = sum(rets) / n
    kelly = None
    if n >= 10 and aw > 0 and al < 0:
        b = aw / abs(al)
        kelly = p_win - (1 - p_win) / b
    return {"n": n, "hit_rate": round(p_win * 100, 1), "avg_win": round(aw, 1),
            "avg_loss": round(al, 1), "expectancy": round(exp, 1),
            "kelly": round(kelly, 3) if kelly is not None else None}

TIER_ORDER = ["BACKGROUND", "MONITOR", "WATCH", "RED HOT"]

def apply_delta_flags(s, prev):
    """Day-over-day transition flags - the rate-of-change layer."""
    if not prev: return
    fee, fee_p = s.get("borrow_fee_pct"), prev.get("borrow_fee_pct")
    if fee and fee_p and fee >= fee_p * 1.5:
        s.setdefault("flags", []).append(f"BORROW FEE RISING {fee_p}% -> {fee}%")
    av, av_p = s.get("shares_avail_k"), prev.get("shares_avail_k")
    if av is not None and av_p and av <= av_p * 0.35:
        s.setdefault("flags", []).append(f"BORROW AVAIL COLLAPSING {av_p}K -> {av}K")
    oi, oi_p = s.get("near_call_oi_k"), prev.get("near_call_oi_k")
    if oi and oi_p and oi >= oi_p * 1.2:
        s.setdefault("flags", []).append(f"CALL OI BUILDING +{(oi/oi_p-1)*100:.0f}% d/d")
    vr, vr_p = s.get("velocity_ratio"), prev.get("velocity_ratio")
    if vr and vr_p and vr >= 2 and vr >= vr_p * 2:
        s.setdefault("flags", []).append("VELOCITY ACCELERATING d/d")

BRAND_10 = {"WEN","GME","AMC","BB","BBBY","NOK","HTZ","KSS","FUN","XRX","BYND","PLCE",
            "FLWS","GRPN","SPCE","LCID","CPB","DNUT","OPEN","M","JWN","BIRD","CHWY",
            "PTON","WISH","CLOV","SDC","EXPR","KOSS","TUP","RAD","BGFV","DDS","ANF"}
BRAND_5  = {"AI","SOUN","NVAX","RUN","MARA","HIMS","SATS","TTD","VKTX","SRPT","LOVE",
            "SERV","EVGO","PLUG","ODD","SPHR","CVNA","RDDT","HOOD","SOFI"}

def score_mechanics(s):
    si = s.get("si_pct") or 0.0
    fl = s.get("float_m")
    pts, notes = 0.0, []
    si_pts = min(25.0, max(0.0, (si - 15.0) * 0.5))
    pts += si_pts
    if fl is None: fl_pts = 0
    elif fl < 5: fl_pts = 15
    elif fl < 15: fl_pts = 13
    elif fl < 30: fl_pts = 11
    elif fl < 60: fl_pts = 8
    elif fl < 120: fl_pts = 5
    elif fl < 250: fl_pts = 2
    else: fl_pts = 0
    pts += fl_pts
    vol_pts = 0.0
    avg_vol = s.get("avg_vol_m"); vol = s.get("vol_m")
    if avg_vol and fl and si:
        dtc = (si / 100.0 * fl) / avg_vol
        s["dtc"] = round(dtc, 1)
        if dtc >= 8: vol_pts += 5
        elif dtc >= 4: vol_pts += 3
        elif dtc >= 2: vol_pts += 1
        if vol:
            spike = vol / avg_vol
            s["vol_spike"] = round(spike, 1)
            if spike >= 10: vol_pts += 5
            elif spike >= 5: vol_pts += 4
            elif spike >= 3: vol_pts += 3
            elif spike >= 1.5: vol_pts += 1
            if spike >= 5: s.setdefault("flags", []).append("VOLUME SPIKE - may already be moving")
    else:
        notes.append("no volume data (enrich to unlock 10 pts)")
    pts += vol_pts
    return round(pts, 1), {"si": round(si_pts,1), "float": fl_pts, "vol": round(vol_pts,1)}, notes

def score_social(s):
    t = s["ticker"]
    rank = s.get("wsb_rank"); m = s.get("mentions") or 0; m24 = s.get("mentions_24h")
    r24 = s.get("rank_24h"); up = s.get("upvotes") or 0
    pts = 0.0
    if rank is None: rank_pts = 0
    elif rank <= 5: rank_pts = 10
    elif rank <= 10: rank_pts = 8
    elif rank <= 25: rank_pts = 6
    elif rank <= 50: rank_pts = 4
    elif rank <= 100: rank_pts = 3
    elif rank <= 200: rank_pts = 2
    else: rank_pts = 1
    pts += rank_pts
    vel_pts = 0.0
    if m:
        ratio = m / max(1, (m24 if m24 is not None else m))
        if ratio >= 8: vel_pts = 20
        elif ratio >= 5: vel_pts = 16
        elif ratio >= 3: vel_pts = 12
        elif ratio >= 2: vel_pts = 8
        elif ratio >= 1.5: vel_pts = 5
        elif ratio >= 1: vel_pts = 2
        if r24 and rank and r24 - rank >= 100 and m >= 5:
            vel_pts = max(vel_pts, 14)
        if m < 3: vel_pts = min(vel_pts, 6)
        if vel_pts >= 12: s.setdefault("flags", []).append("WSB CHATTER ACCELERATING")
    pts += vel_pts
    heat = up / m if m else 0
    up_pts = 5 if heat >= 20 else 3 if heat >= 10 else 2 if heat >= 5 else 1 if heat >= 1 else 0
    pts += up_pts
    brand_pts = 10 if t in BRAND_10 else 5 if t in BRAND_5 else 0
    pts += brand_pts
    return round(pts, 1), {"rank": rank_pts, "velocity": round(vel_pts,1), "upvotes": up_pts, "brand": brand_pts}

def score_gamma(s):
    oi = s.get("near_call_oi_k"); share = s.get("call_share")
    prem = s.get("near_call_prem_m"); iv = s.get("atm_iv_pct")
    fl = s.get("float_m"); mcap = s.get("mcap_b")
    pts = 0.0
    br = {"oi_float": 0, "call_share": 0, "premium": 0, "iv": 0}
    if oi and fl:
        oi_pct = oi * 10.0 / fl  # (oi_k * 100 shares) / (float_m * 1e6) as %
        s["call_oi_pct_float"] = round(oi_pct, 1)
        p = 4 if oi_pct >= 20 else 3 if oi_pct >= 10 else 2 if oi_pct >= 5 else 1 if oi_pct >= 2 else 0
        br["oi_float"] = p; pts += p
    if share is not None:
        p = 3 if share >= 0.8 else 2 if share >= 0.7 else 1 if share >= 0.6 else 0
        br["call_share"] = p; pts += p
    if prem is not None and mcap:
        ratio = prem / (mcap * 1000.0) * 100.0  # % of mcap
        s["prem_pct_mcap"] = round(ratio, 3)
        p = 3 if ratio >= 0.1 else 2 if ratio >= 0.03 else 1 if ratio >= 0.01 else 0
        br["premium"] = p; pts += p
    if iv:
        p = 2 if iv >= 200 else 1.5 if iv >= 120 else 1 if iv >= 80 else 0
        br["iv"] = p; pts += p
    if pts >= 7:
        s.setdefault("flags", []).append("GAMMA RAMP - weekly calls bid")
    return round(pts, 1), br

def check_catalyst(s, asof):
    ed = s.get("earnings_date")
    if not ed: return
    try:
        d = datetime.strptime(str(ed)[:10], "%Y-%m-%d").date()
    except ValueError:
        return
    dd = (d - asof).days
    if 0 <= dd <= 2:
        s["pre_catalyst"] = True
        s.setdefault("flags", []).append(f"PRE-CATALYST: earnings {ed} ({dd}d out)")

def trigger_reasons(r):
    reasons = []
    vel = r.get("soc_breakdown", {}).get("velocity", 0)
    if vel >= 16 and r.get("vol_spike", 0) >= 3:
        reasons.append("velocity >=5x + volume >=3x")
    if vel >= 12 and r.get("pre_catalyst"):
        reasons.append("chatter accelerating into dated catalyst")
    if r.get("gamma", 0) >= 7:
        reasons.append("gamma ramp (weekly calls bid)")
    return reasons

EXIT_DOCTRINE = "SPRT rule: sell half at 3-4x, trail the rest. The exit matters more than the entry."

def exit_signals(r, pos):
    sigs = []
    df = r.get("dilution_filing")
    if df:
        sigs.append(f"DILUTION FILING: {df.get('form')} filed {df.get('date')} - sell first, ask later (auto-RED)")
    m = r.get("mentions"); m24 = r.get("mentions_24h")
    if m and m24:
        ratio = m / max(1, m24)
        if ratio < 1.0:
            sigs.append(f"social fading (velocity {ratio:.1f}x - crowd leaving)")
    if r.get("vol_spike", 0) >= 5 and (r.get("chg_pct") or 0) >= 25:
        sigs.append(f"parabolic on {r['vol_spike']}x volume - distribution window, squeezes die in 1-3 sessions")
    fee = r.get("borrow_fee_pct"); fee_p = r.get("borrow_fee_prev_pct")
    if fee and fee_p and fee <= fee_p * 0.7:
        sigs.append(f"borrow fee normalizing ({fee_p}% -> {fee}%) - short pressure releasing")
    av = r.get("shares_avail_k"); av_p = r.get("shares_avail_prev_k")
    if av and av_p and av >= av_p * 5:
        sigs.append(f"borrow availability rebounding ({av_p}K -> {av}K) - shorts done covering")
    if r.get("call_share") is not None and r["call_share"] <= 0.45:
        sigs.append(f"options flow flipped to puts (call share {r['call_share']}) - gamma unwinding")
    entry = pos.get("entry"); px = r.get("price")
    if entry and px and px / entry >= 3:
        sigs.append(f"at {px/entry:.1f}x entry - {EXIT_DOCTRINE}")
    return sigs

def position_report(results, snapshot):
    out = []
    idx = {r["ticker"]: r for r in results}
    for pos in snapshot.get("positions", []):
        t = pos["ticker"]; r = idx.get(t, {"ticker": t})
        sigs = exit_signals(r, pos)
        entry = pos.get("entry"); px = r.get("price")
        pl = round((px / entry - 1) * 100, 1) if entry and px else None
        level = "RED" if (len(sigs) >= 2 or any("DILUTION" in x for x in sigs)) else "AMBER" if sigs else "GREEN"
        note = None
        fp = pos.get("flag_price")
        if fp and px and px >= fp * 1.5:
            note = f"+50% from flag price ${fp} - standing bet triggered: buy the LunarCrush subscription"
        stop = pos.get("stop"); stop_dist = None
        if stop and px:
            stop_dist = round((px / stop - 1) * 100, 1)
        shares = pos.get("shares")
        pl_usd = round((px - entry) * shares, 0) if entry and px and shares else None
        out.append({"ticker": t, "entry": entry, "price": px, "pl_pct": pl, "pl_usd": pl_usd,
                    "stop": stop, "stop_dist": stop_dist, "level": level,
                    "signals": sigs, "note": note, "score": r.get("score"), "tier": r.get("tier")})
    return out

def tier(score):
    return ("RED HOT" if score >= 70 else "WATCH" if score >= 55 else
            "MONITOR" if score >= 40 else "BACKGROUND")

def run(snapshot, prior=None):
    try:
        asof = datetime.strptime(snapshot.get("as_of", "")[:10], "%Y-%m-%d").date()
    except ValueError:
        asof = date.today()
    merged = {}
    for row in snapshot.get("si_universe", []):
        merged[row["ticker"]] = dict(row)
    for row in snapshot.get("watchlist", []):
        merged.setdefault(row["ticker"], {}).update(row)
    for row in snapshot.get("borrow_watch", []):
        merged.setdefault(row["ticker"], {}).update(row)
    for row in snapshot.get("positions", []):
        merged.setdefault(row["ticker"], {"ticker": row["ticker"]})
    wsb = {w["ticker"]: w for w in snapshot.get("wsb", [])}
    for t, w in wsb.items():
        if t in merged:
            merged[t].update({"wsb_rank": w["rank"], "mentions": w["mentions"],
                              "mentions_24h": w.get("mentions_24h"), "rank_24h": w.get("rank_24h_ago"),
                              "upvotes": w.get("upvotes")})
    for row in snapshot.get("enrichment", []):
        if row["ticker"] in merged: merged[row["ticker"]].update(row)
    results = []
    prior = prior or {}
    for t, s in merged.items():
        s["ticker"] = t
        if s.get("mentions") and s.get("mentions_24h") is not None:
            s["velocity_ratio"] = round(s["mentions"] / max(1, s["mentions_24h"]), 2)
        apply_delta_flags(s, prior.get(t))
        check_catalyst(s, asof)
        mech, mech_br, notes = score_mechanics(s)
        soc, soc_br = score_social(s)
        s_tmp = {**s, "soc_breakdown": soc_br}
        gam, gam_br = score_gamma(s)
        total = round(mech + soc + gam, 1)
        fuel = round(mech_br["si"] + mech_br["float"], 1)
        ign = round(mech_br["vol"] + soc_br["velocity"] + gam + (5 if s.get("pre_catalyst") else 0), 1)
        cur_tier = tier(total)
        pv = prior.get(t)
        if pv and pv.get("tier") in TIER_ORDER and cur_tier in TIER_ORDER:
            if TIER_ORDER.index(cur_tier) - TIER_ORDER.index(pv["tier"]) >= 2:
                s.setdefault("flags", []).append(f"TIER JUMP {pv['tier']} -> {cur_tier}")
        results.append({**s, "mech": mech, "soc": soc, "gamma": gam, "score": total,
                        "fuel": fuel, "ign": ign,
                        "tier": cur_tier, "mech_breakdown": mech_br,
                        "soc_breakdown": soc_br, "gamma_breakdown": gam_br, "notes": notes})
    results.sort(key=lambda r: -r["score"])
    triggers = []
    for r in results:
        why = trigger_reasons(r)
        if why:
            triggers.append({"ticker": r["ticker"], "why": why, "score": r["score"], "tier": r["tier"]})
    return results, triggers

BACKTEST = [
    {"ticker":"GME","name":"GameStop Jan-2021","si_pct":140,"float_m":47,"avg_vol_m":25,"vol_m":150,
     "wsb_rank":1,"mentions":500,"mentions_24h":90,"rank_24h":3,"upvotes":20000,
     "near_call_oi_k":300,"call_share":0.85,"near_call_prem_m":80,"atm_iv_pct":400,"mcap_b":1.5},
    {"ticker":"SPRT","name":"Support.com Aug-2021","si_pct":60,"float_m":10,"avg_vol_m":0.7,"vol_m":5,
     "wsb_rank":9,"mentions":120,"mentions_24h":25,"rank_24h":60,"upvotes":1500,
     "near_call_oi_k":40,"call_share":0.8,"near_call_prem_m":3,"atm_iv_pct":250,"mcap_b":0.2},
    {"ticker":"BBBY","name":"Bed Bath Aug-2022","si_pct":52,"float_m":77,"avg_vol_m":15,"vol_m":120,
     "wsb_rank":2,"mentions":400,"mentions_24h":100,"rank_24h":8,"upvotes":9000},
    {"ticker":"WEN","name":"Wendys 24-Jun-2026 (day 1)","si_pct":32,"float_m":160,"avg_vol_m":13,"vol_m":202,
     "wsb_rank":1,"mentions":200,"mentions_24h":15,"rank_24h":120,"upvotes":8000},
    {"ticker":"PENG","name":"Penguin 07-Jul-2026 (the one that got away)","si_pct":21.7,"float_m":49.1,
     "avg_vol_m":1.93,"vol_m":7.37,"wsb_rank":28,"mentions":20,"mentions_24h":2,"rank_24h":161,
     "upvotes":50,"earnings_date":"2026-07-07","mcap_b":3.3},
    {"ticker":"XYZ","name":"control: boring 22pct SI biotech, no chatter","si_pct":22,"float_m":150,
     "avg_vol_m":2,"vol_m":2,"wsb_rank":None,"mentions":0},
]

def backtest():
    print(f"{'ticker':6} {'case':44} {'mech':>5} {'soc':>5} {'gam':>4} {'TOTAL':>6}  tier / triggers")
    for case in BACKTEST:
        s = dict(case)
        check_catalyst(s, datetime.strptime("2026-07-07","%Y-%m-%d").date() if s["ticker"]=="PENG" else date(2021,1,25))
        mech, _, _ = score_mechanics(s)
        soc, soc_br = score_social(s)
        gam, _ = score_gamma(s)
        tot = round(mech + soc + gam, 1)
        r = {**s, "soc_breakdown": soc_br, "gamma": gam}
        trig = trigger_reasons(r)
        print(f"{s['ticker']:6} {case['name']:44} {mech:5} {soc:5} {gam:4} {tot:6}  {tier(tot)}"
              + (f"  << TRIGGER: {'; '.join(trig)}" if trig else ""))

def analyze(hist, snap_dir="."):
    """Forward-return report: which signals actually predicted moves. Needs weeks of history."""
    pp = Path(snap_dir) / PAPER_FILE
    if pp.exists():
        try:
            pd_data = json.loads(pp.read_text())
            st = paper_stats(pd_data.get("closed", []))
            print(f"== PAPER LEDGER == open: {len(pd_data.get('open', []))}")
            if st:
                print(f"  closed {st['n']} | hit rate {st['hit_rate']}% | avg win {st['avg_win']}% | avg loss {st['avg_loss']}% | expectancy {st['expectancy']}%/trade")
                if st["kelly"] is not None:
                    qk = max(0, st["kelly"] / 4)
                    print(f"  full Kelly {st['kelly']:.1%} -> quarter-Kelly sizing: {qk:.1%} of squeeze capital per trigger trade")
                    if st["kelly"] <= 0:
                        print("  Kelly <= 0: the trigger signals have NO measured edge yet - keep sizing minimal")
                else:
                    print("  (Kelly sizing unlocks at 10 closed trades)")
            else:
                print("  no closed trades yet")
            print()
        except Exception as e:
            print(f"paper ledger unreadable: {e}")
    if len(hist) < 3:
        print(f"history has {len(hist)} run(s) - need more days before analysis is meaningful"); return
    buckets = {}
    for i, h in enumerate(hist[:-1]):
        for t, v in h["tickers"].items():
            p0 = v.get("price")
            if not p0: continue
            fwd = {}
            for off, label in ((1, "1d"), (5, "5d")):
                if i + off < len(hist):
                    p1 = hist[i + off]["tickers"].get(t, {}).get("price")
                    if p1: fwd[label] = (p1 / p0 - 1) * 100
            if not fwd: continue
            keys = [f"tier:{v['tier']}"] + [f"flag:{f.split(' -')[0].split(':')[0].strip()}" for f in v.get("flags", [])]
            if v.get("ign", 0) >= 20: keys.append("ign>=20")
            for k in keys:
                for label, ret in fwd.items():
                    buckets.setdefault((k, label), []).append(ret)
    print(f"{'signal':32} {'n':>4} {'avg 1d':>8} {'avg 5d':>8}")
    rows = {}
    for (k, label), rets in buckets.items():
        rows.setdefault(k, {})[label] = (len(rets), sum(rets) / len(rets))
    for k in sorted(rows):
        n1, a1 = rows[k].get("1d", (0, 0)); n5, a5 = rows[k].get("5d", (0, 0))
        print(f"{k:32} {max(n1, n5):4} {a1:7.1f}% {a5:7.1f}%")

def bar(v, mx, color):
    pct = 0 if mx == 0 else min(100, v / mx * 100)
    return f'<div class="bar"><div style="width:{pct}%;background:{color}"></div></div>'

def dashboard(results, triggers, positions, snapshot, out_path):
    d = snapshot.get("as_of", str(date.today()))
    tier_colors = {"RED HOT":"#ff4757","WATCH":"#ffa502","MONITOR":"#70a1ff","BACKGROUND":"#57606f"}
    pos_html = ""
    for p in positions:
        c = {"RED": "#ff4757", "AMBER": "#ffa502", "GREEN": "#2ed573"}[p["level"]]
        pl = f'{p["pl_pct"]:+.1f}%' if p["pl_pct"] is not None else "set entry in snapshot positions[]"
        sig = "".join(f'<div class="pos-sig">&#9888;&#65039; {html.escape(s)}</div>' for s in p["signals"]) \
              or '<div class="pos-sig ok">&#10003; no exit signals - let it work</div>'
        note = f'<div class="pos-sig" style="color:#ffd32a">&#127881; {html.escape(p["note"])}</div>' if p.get("note") else ""
        entry_txt = f'${p["entry"]}' if p.get("entry") else "?"
        px_txt = f'${p["price"]}' if p.get("price") else "?"
        if p.get("pl_usd") is not None:
            pl += f' / ${p["pl_usd"]:+,.0f}'
        stop_txt = ""
        if p.get("stop"):
            d = f' ({p["stop_dist"]}% below px)' if p.get("stop_dist") is not None else ""
            stop_txt = f' &middot; stop ${p["stop"]}{d}'
        warn = ""
        if p.get("stop_dist") is not None and p["stop_dist"] <= 4:
            warn = f'<div class="pos-sig">&#9888;&#65039; price within {p["stop_dist"]}% of stop - one bad open takes you out; decide re-entry rules NOW</div>'
        pos_html += (f'<div class="position" style="border-color:{c}">'
                     f'<div class="pos-head"><b>{p["ticker"]}</b> POSITION &middot; {p.get("shares") or "?"} sh &middot; entry {entry_txt} &rarr; {px_txt} ({pl}){stop_txt} '
                     f'<span class="tier" style="background:{c};float:right">EXIT PRESSURE: {p["level"]}</span></div>{warn}{sig}{note}</div>')
    fueled = [r for r in results if r["fuel"] >= 12 and r["ign"] > 0]
    fueled.sort(key=lambda r: -r["ign"])
    ign_html = ""
    if fueled[:3]:
        items = " &middot; ".join(f'<b>{r["ticker"]}</b> ign {r["ign"]}/47 (fuel {r["fuel"]})' for r in fueled[:3])
        ign_html = f'<div class="ign-bar">&#128293; TOP IGNITION among fueled names: {items}</div>'
    trig_html = ""
    if triggers:
        items = "".join(f'<div class="trig-item"><b>{t["ticker"]}</b> ({t["score"]}, {t["tier"]}) &mdash; {html.escape("; ".join(t["why"]))}</div>' for t in triggers)
        trig_html = f'<div class="trigger-stack"><div class="trig-head">&#9889; TRIGGER STACK &mdash; act-now signals, tier be damned</div>{items}</div>'
    rows = []
    for i, r in enumerate(results):
        t = r["ticker"]; tc = tier_colors[r["tier"]]
        flags = " ".join(f'<span class="flag">{html.escape(f)}</span>' for f in r.get("flags", []))
        wsb = f'#{r["wsb_rank"]} ({r["mentions"]}m)' if r.get("wsb_rank") else "-"
        vel = ""
        if r.get("mentions") and r.get("mentions_24h") is not None:
            ratio = r["mentions"] / max(1, r["mentions_24h"])
            vel = f'{ratio:.1f}x'
        dtc = f'{r["dtc"]}d' if r.get("dtc") else "-"
        gam = r["gamma"] if r.get("gamma") else "-"
        mb, sb, gb = r["mech_breakdown"], r["soc_breakdown"], r["gamma_breakdown"]
        detail = (f'SI {mb["si"]}/25 &middot; Float {mb["float"]}/15 &middot; Vol {mb["vol"]}/10 &nbsp;|&nbsp; '
                  f'Rank {sb["rank"]}/10 &middot; Velocity {sb["velocity"]}/20 &middot; Upvotes {sb["upvotes"]}/5 &middot; Brand {sb["brand"]}/10 &nbsp;|&nbsp; '
                  f'Gamma: OI/float {gb["oi_float"]}/4 &middot; CallShare {gb["call_share"]}/3 &middot; Prem {gb["premium"]}/3 &middot; IV {gb["iv"]}/2')
        detail += f' &nbsp;|&nbsp; <b>Fuel {r["fuel"]}/40 &middot; Ignition {r["ign"]}/47</b>'
        if r.get("call_oi_pct_float"):
            detail += f' &nbsp;(near-call OI = {r["call_oi_pct_float"]}% of float)'
        rows.append(f"""
<tr class="main" onclick="this.nextElementSibling.classList.toggle('show')">
  <td>{i+1}</td>
  <td class="tk"><a href="https://stockanalysis.com/stocks/{t.lower()}/" target="_blank">{t}</a></td>
  <td class="nm">{html.escape(str(r.get('name','')))[:34]}</td>
  <td><span class="tier" style="background:{tc}">{r['tier']}</span></td>
  <td class="num"><b>{r['score']}</b>{bar(r['score'],100,tc)}</td>
  <td class="num">{r['mech']}{bar(r['mech'],50,'#2ed573')}</td>
  <td class="num">{r['soc']}{bar(r['soc'],50,'#e056fd')}</td>
  <td class="num">{gam}</td>
  <td class="num">{r.get('si_pct','-')}%</td>
  <td class="num">{r.get('float_m','-')}M</td>
  <td class="num">{dtc}</td>
  <td class="num">{wsb}</td>
  <td class="num">{vel}</td>
  <td>{flags}</td>
</tr>
<tr class="detail"><td colspan="14">{detail}
  &nbsp; <a href="https://finance.yahoo.com/quote/{t}/" target="_blank">Yahoo</a> &middot;
  <a href="https://apewisdom.io/stocks/{t}/" target="_blank">ApeWisdom</a> &middot;
  <a href="https://fintel.io/ss/us/{t.lower()}" target="_blank">Fintel SI</a> &middot;
  <a href="https://optioncharts.io/options/{t}" target="_blank">Options</a></td></tr>""")
    html_doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Meme Squeeze Screener - {d}</title>
<style>
 body{{background:#0f1117;color:#dfe4ea;font-family:'Segoe UI',system-ui,sans-serif;margin:0;padding:24px}}
 h1{{font-size:22px;margin:0 0 2px}} .sub{{color:#747d8c;font-size:13px;margin-bottom:18px}}
 .position{{background:#12161f;border:1px solid;border-radius:8px;padding:12px 16px;margin-bottom:12px}}
 .pos-head{{font-size:14px;margin-bottom:6px}}
 .pos-sig{{font-size:13px;color:#ff9f9f;padding:2px 0}} .pos-sig.ok{{color:#2ed573}}
 .ign-bar{{background:#1a1a12;border:1px solid #ffa502;border-radius:8px;padding:10px 16px;margin-bottom:18px;font-size:13px;color:#ffdd59}}
 .trigger-stack{{background:#1c1420;border:1px solid #ff4757;border-radius:8px;padding:12px 16px;margin-bottom:18px}}
 .trig-head{{color:#ff6b6b;font-weight:700;font-size:14px;margin-bottom:6px}}
 .trig-item{{font-size:13px;padding:3px 0;color:#dfe4ea}}
 table{{border-collapse:collapse;width:100%;font-size:13px}}
 th{{text-align:left;color:#a4b0be;font-weight:600;padding:8px 10px;border-bottom:2px solid #2f3542;position:sticky;top:0;background:#0f1117}}
 td{{padding:8px 10px;border-bottom:1px solid #1e2430;vertical-align:middle}}
 tr.main{{cursor:pointer}} tr.main:hover{{background:#161b26}}
 tr.detail{{display:none;color:#a4b0be;font-size:12px;background:#131722}}
 tr.detail.show{{display:table-row}}
 .tk a{{color:#70a1ff;font-weight:700;text-decoration:none;font-size:14px}}
 .nm{{color:#a4b0be}} .num{{text-align:right;white-space:nowrap}}
 .tier{{padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700;color:#0f1117}}
 .bar{{height:3px;background:#2f3542;border-radius:2px;margin-top:3px;min-width:52px}}
 .bar div{{height:3px;border-radius:2px}}
 .flag{{background:#3d2222;color:#ff6b6b;padding:2px 6px;border-radius:4px;font-size:11px;margin-right:4px;white-space:nowrap}}
 .meth{{margin-top:26px;color:#747d8c;font-size:12px;line-height:1.6;max-width:900px}}
 .meth b{{color:#a4b0be}}
 tr.detail a{{color:#70a1ff}}
</style></head><body>
<h1>&#128640; Meme Squeeze Screener</h1>
<div class="sub">Data as of {d} &middot; SI: highshortinterest.com (FINRA bi-weekly, lags ~2wks) &middot; Social: ApeWisdom (r/wallstreetbets) &middot; Options: CBOE delayed quotes &middot; click a row for score breakdown</div>
{pos_html}
{trig_html}
{ign_html}
<table>
<tr><th>#</th><th>Ticker</th><th>Company</th><th>Tier</th><th>Score</th><th>Mech /50</th><th>Social /50</th><th>&#915; /12</th>
<th>SI%</th><th>Float</th><th>DTC</th><th>WSB rank</th><th>Vel</th><th>Flags</th></tr>
{''.join(rows)}
</table>
<div class="meth"><b>The DNA this screens for</b> - every major squeeze (GME 140% SI, SPRT ~60-78% SI / 8d cover / ~10M float,
BBBY 52% SI, WEN 32% SI + 15x volume + WSB post) shared: heavy short interest vs float, tight or emotionally-owned float,
accelerating WSB chatter, a brand retail loves, and a spark (catalyst/viral post). <b>Mechanics 50</b>: SI% (25), float (15),
DTC + volume spike (10). <b>Social 50</b>: WSB rank (10), mention velocity vs 24h ago (20), upvote heat (5), brand/meme DNA (10).
<b>Gamma bonus 12</b>: near-dated call OI vs float (4), call volume share (3), weekly call premium vs mcap (3), ATM IV (2) -
GME/SPRT-class squeezes light up weekly call premiums before the vertical move; gamma >=7 flags GAMMA RAMP.
<b>Trigger stack</b>: velocity >=5x + volume >=3x, chatter accelerating into a dated catalyst (the PENG lesson), or gamma ramp -
these surface at the top regardless of tier. <b>Blind spots</b>: FINRA SI is bi-weekly and ~2 weeks stale; CBOE options
data is 15-min delayed and only fetched for top names.</div>
</body></html>"""
    Path(out_path).write_text(html_doc, encoding="utf-8")
    return out_path

if __name__ == "__main__":
    if "--backtest" in sys.argv:
        backtest(); sys.exit()
    snap_path = sys.argv[1] if len(sys.argv) > 1 else "snapshot.json"
    snap_dir = Path(snap_path).parent
    snapshot = json.loads(Path(snap_path).read_text())
    asof_str = snapshot.get("as_of", str(date.today()))[:10]
    hist = load_history(snap_dir)
    prior_recs = [h for h in hist if h["date"] < asof_str]
    prior = prior_recs[-1]["tickers"] if prior_recs else None
    if "--analyze" in sys.argv:
        analyze(hist, snap_dir); sys.exit()
    results, triggers = run(snapshot, prior)
    rec = {"date": asof_str, "tickers": {r["ticker"]: {
        "score": r["score"], "tier": r["tier"], "fuel": r["fuel"], "ign": r["ign"],
        "price": r.get("price"), "velocity_ratio": r.get("velocity_ratio"),
        "borrow_fee_pct": r.get("borrow_fee_pct"), "shares_avail_k": r.get("shares_avail_k"),
        "near_call_oi_k": r.get("near_call_oi_k"), "flags": r.get("flags", [])} for r in results}}
    hist = [h for h in hist if h["date"] != asof_str] + [rec]
    save_history(snap_dir, hist)
    paper = update_paper_trades(results, triggers, snap_dir, asof_str)
    st = paper_stats(paper["closed"])
    opens = ", ".join("{} {:+.1f}%".format(t["ticker"], (t["last_price"]/t["entry"]-1)*100) for t in paper["open"]) or "none"
    line = "== PAPER LEDGER == open: " + opens
    if st:
        line += f" | closed {st['n']}: hit {st['hit_rate']}%, expectancy {st['expectancy']}%/trade"
    print(line + "\n")
    positions = position_report(results, snapshot)
    out = dashboard(results, triggers, positions, snapshot, Path(snap_path).parent / "dashboard.html")
    for p in positions:
        pl = f"{p['pl_pct']:+.1f}%" if p["pl_pct"] is not None else "entry not set"
        print(f"== POSITION {p['ticker']} ({pl}) - EXIT PRESSURE: {p['level']} ==")
        for s2 in p["signals"]: print(f"  ! {s2}")
        if not p["signals"]: print("  no exit signals - let it work")
        if p.get("note"): print(f"  * {p['note']}")
        print()
    if triggers:
        print("== TRIGGER STACK ==")
        for t in triggers:
            print(f"  {t['ticker']:6} ({t['score']}, {t['tier']})  {'; '.join(t['why'])}")
        print()
    print(f"{'#':>2} {'ticker':6} {'score':>6} {'fuel':>5} {'ign':>5} {'gam':>4}  tier        flags")
    for i, r in enumerate(results[:25]):
        print(f"{i+1:2} {r['ticker']:6} {r['score']:6} {r['fuel']:5} {r['ign']:5} {r['gamma']:4}  {r['tier']:10}  {','.join(r.get('flags',[]))}")
    print(f"\ndashboard: {out}")
