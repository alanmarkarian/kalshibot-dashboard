#!/usr/bin/env python3
"""Build squeeze.html full dual board from on-disk snapshots. No live scan."""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

def _resolve_root() -> Path:
    """Prefer MarkarianPC path when present; else repo root (Actions / box)."""
    win = Path(r"C:\Users\alanm\ShortSqueezeScreener")
    if win.exists():
        return win
    return Path(__file__).resolve().parent


def _resolve_claude(root: Path) -> Path:
    candidates = [
        Path(r"C:\Users\alanm\OneDrive\Documents\Claude Meme Screener\snapshot.json"),
        root / "data" / "claude_engine" / "snapshot.json",
        root / "data" / "claude_meme_snapshot.json",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


ROOT = _resolve_root()
CONS = ROOT / "data" / "consensus.json"
SNAP = ROOT / "data" / "last_snapshot.json"
BUYABLE_FULL = ROOT / "data" / "buyable_full.json"
POS = ROOT / "data" / "positions.csv"
CLAUDE = _resolve_claude(ROOT)
OUT = ROOT / "squeeze.html"

WATCH_TIERS = {"WATCH", "RED HOT"}

LEVEL_RANK = {
    "DUAL TRIGGER": 0,
    "DUAL WATCH": 1,
    "DUAL IGNITION": 2,
    "DUAL BOARD": 3,
    "GROK ONLY": 4,
    "CLAUDE ONLY": 5,
    "FUEL / HTB": 6,
}

LVL_CLASS = {
    "DUAL TRIGGER": "l-trig",
    "DUAL WATCH": "l-watch",
    "DUAL IGNITION": "l-ign",
    "DUAL BOARD": "l-board",
    "GROK ONLY": "l-grok",
    "CLAUDE ONLY": "l-claude",
    "FUEL / HTB": "l-fuel",
}

GROK_ASOF = "8/21 Fri 7:36 Premarket"  # fallback; table prefers last_snapshot as_of + mtime
CLAUDE_ASOF = "8/21 Fri AM"  # fallback; Claude prefers snapshot as_of (Fri AM; data: Thu 8/20 close)
# Buyable strip = last full-options Premarket (7:36) from buyable_full.json.
# Midday --quick rebuilds the table only; it must not overwrite that file.


def asof_label_from_iso(as_of, extra=""):
    """Honest short vintage: '8/21 Fri' plus optional session."""
    if not as_of:
        return None
    s = str(as_of).strip()
    try:
        d = datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        return None
    label = f"{d.month}/{d.day} {d.strftime('%a')}"
    extra = (extra or "").strip()
    return f"{label} {extra}" if extra else label


def grok_table_asof(snap, snap_path):
    """Table Grok vintage from last_snapshot.json, not a hardcoded date."""
    base = asof_label_from_iso(snap.get("as_of") or (snap.get("meta") or {}).get("as_of"))
    if not base:
        return None
    try:
        mt = datetime.fromtimestamp(snap_path.stat().st_mtime, ZoneInfo("America/New_York"))
    except OSError:
        return f"{base} Premarket"
    if mt.hour < 9 or (mt.hour == 9 and mt.minute < 30):
        return f"{base} {mt.hour}:{mt.minute:02d} Premarket"
    if mt.hour < 12:
        return f"{base} AM"
    if mt.hour < 16:
        return f"{base} Midday"
    return f"{base} PM"


def claude_table_asof(claude_obj, cons_obj):
    """Table Claude vintage from snapshot / consensus meta. Session is AM."""
    raw = (
        claude_obj.get("as_of")
        or (cons_obj.get("claude_meta") or {}).get("asof_date")
        or (cons_obj.get("claude_meta") or {}).get("as_of")
    )
    base = asof_label_from_iso(raw)
    if not base:
        return None
    return f"{base} AM"


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def load_buyable_persist():
    """Buyable strip: last full-options scan only. Midday --quick does not own this file."""
    if not BUYABLE_FULL.exists():
        return None
    try:
        data = json.loads(BUYABLE_FULL.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("source") != "full-options":
        return None
    return data


def persist_to_card(r):
    return {
        "ticker": str(r.get("ticker") or "").upper(),
        "level": r.get("level"),
        "grok": r.get("grok") if r.get("grok") is not None else r.get("grok_score"),
        "claude": r.get("claude") if r.get("claude") is not None else r.get("claude_score"),
        "grok_tier": r.get("grok_tier"),
        "claude_tier": r.get("claude_tier"),
        "grok_fuel": r.get("grok_fuel") if r.get("grok_fuel") is not None else r.get("fuel"),
        "grok_ign": r.get("grok_ign") if r.get("grok_ign") is not None else r.get("ign"),
        "claude_fuel": r.get("claude_fuel"),
        "claude_ign": r.get("claude_ign"),
        "si": r.get("si") if r.get("si") is not None else r.get("si_pct"),
        "price": r.get("price"),
        "why": r.get("why") or "",
    }


def fmt_score(v):
    if v is None or v == "":
        return "-"
    try:
        return f"{float(v):.1f}"
    except (TypeError, ValueError):
        return "-"


def fmt_si(v):
    if v is None or v == "":
        return "-"
    try:
        return f"{float(v):.1f}%"
    except (TypeError, ValueError):
        return "-"


def fmt_float(v):
    if v is None or v == "":
        return "-"
    try:
        x = float(v)
        if x >= 100:
            return f"{x:.0f}M"
        return f"{x:.1f}M"
    except (TypeError, ValueError):
        return "-"


def fmt_px(v):
    if v is None or v == "":
        return ""
    try:
        return f"${float(v):.2f}"
    except (TypeError, ValueError):
        return ""


def fmt_fi(v):
    if v is None or v == "":
        return "-"
    try:
        return f"{float(v):.1f}"
    except (TypeError, ValueError):
        return "-"


def has_squeeze_fuel(r):
    """Same gate as scoring.trigger_reasons / consensus._has_squeeze_fuel."""
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


def flag_ignition(r):
    """Ignition requires squeeze fuel. X/WSB flags alone are not enough."""
    if not has_squeeze_fuel(r):
        return False
    if (r.get("ign") or 0) >= 15 and (r.get("fuel") or 0) >= 12:
        return True
    if (r.get("fuel") or 0) < 12:
        return False
    flags = " ".join(r.get("flags") or [])
    ign_flags = (
        "WSB CHATTER ACCELERATING",
        "GAMMA RAMP",
        "VOLUME SPIKE",
        "X TRENDING",
        "X ACCELERATING",
        "X TREND RISING",
        "PRE-CATALYST",
        "TIER JUMP",
    )
    return any(k in flags for k in ign_flags)


def fuel_view(r, snap_row=None, cl_row=None):
    fuel = r.get("grok_fuel")
    if fuel is None:
        fuel = r.get("claude_fuel")
    if fuel is None and snap_row:
        fuel = snap_row.get("fuel")
    ign = r.get("grok_ign")
    if ign is None:
        ign = r.get("claude_ign")
    if ign is None and snap_row:
        ign = snap_row.get("ign")
    si = r.get("si_pct")
    if si in (None, "") and snap_row:
        si = snap_row.get("si_pct")
    if si in (None, "") and cl_row:
        si = cl_row.get("si_pct")
    fl = None
    if snap_row and snap_row.get("float_m") not in (None, ""):
        fl = snap_row.get("float_m")
    elif cl_row and cl_row.get("float_m") not in (None, ""):
        fl = cl_row.get("float_m")
    fee = None
    if snap_row and snap_row.get("borrow_fee_pct") not in (None, ""):
        fee = snap_row.get("borrow_fee_pct")
    elif cl_row and cl_row.get("borrow_fee_pct") not in (None, ""):
        fee = cl_row.get("borrow_fee_pct")
    flags = list(r.get("grok_flags") or []) + list(r.get("claude_flags") or [])
    if snap_row:
        flags = list(snap_row.get("flags") or []) + flags
    return {
        "si_pct": si or 0,
        "float_m": fl,
        "fuel": fuel,
        "ign": ign,
        "borrow_fee_pct": fee or 0,
        "flags": flags,
    }


def keep_onesided(r, snap_row=None, cl_row=None):
    if r.get("grok_trigger") or r.get("claude_trigger"):
        return True
    if r.get("grok_tier") in WATCH_TIERS or r.get("claude_tier") in WATCH_TIERS:
        return True
    return flag_ignition(fuel_view(r, snap_row, cl_row))


def shorten(flag, maxlen=72):
    s = str(flag).strip()
    if len(s) <= maxlen:
        return s
    return s[: maxlen - 1] + "…"


def flag_priority(flag):
    s = str(flag).lower()
    if "gamma ramp" in s:
        return 90
    # "no dilution" / "not dilution" are clean-file notes, not dilution alerts
    neg_dil = ("no dilution" in s) or ("not dilution" in s) or ("not a dilution" in s)
    if (not neg_dil) and ("dilution" in s or "3.02" in s):
        return 0
    if "stop breached" in s:
        return 1
    if "social fad" in s or "chatter fad" in s or "crowd leaving" in s:
        return 2
    if "fading" in s or "velocity 0." in s:
        return 2
    return 10


def pick_tape(flag_lists, gamma_blank, extra=None):
    flags = []
    for lst in flag_lists:
        for f in lst or []:
            if f and str(f).strip():
                flags.append(str(f).strip())
    if extra:
        for f in extra:
            if f and str(f).strip():
                flags.append(str(f).strip())
    seen = set()
    uniq = []
    for f in flags:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    if not uniq:
        return "-"
    scored = []
    for f in uniq:
        p = flag_priority(f)
        if gamma_blank and p == 90:
            continue
        scored.append((p, f))
    if not scored:
        return "-"
    scored.sort(key=lambda x: x[0])
    picked = []
    for p, f in scored[:2]:
        picked.append(shorten(f, 80 if p <= 2 else 42))
    return " · ".join(picked)


def live_why(tk, snap_row, cl_wsb, enr):
    """Why-line from THIS scan's WSB/X fields, not leftover stale flags."""
    bits = []
    if cl_wsb:
        rk = cl_wsb.get("rank")
        mn = cl_wsb.get("mentions")
        if rk is not None or mn is not None:
            bits.append(f"Claude WSB rank {rk if rk is not None else '-'} / {mn if mn is not None else '-'} mentions")
            r24 = cl_wsb.get("rank_24h_ago")
            m24 = cl_wsb.get("mentions_24h")
            if r24 is not None or m24 is not None:
                bits.append(f"(prior {r24 if r24 is not None else '-'} / {m24 if m24 is not None else '-'}m)")
    if snap_row and snap_row.get("wsb_rank") is not None:
        bits.append(
            f"Grok WSB #{snap_row.get('wsb_rank')} / {snap_row.get('mentions') if snap_row.get('mentions') is not None else '-'} mentions"
        )
    xb = (enr or {}).get("x_buzz")
    xt = (enr or {}).get("x_trend")
    if xb not in (None, ""):
        try:
            xb_s = f"{float(xb):.0f}"
        except (TypeError, ValueError):
            xb_s = str(xb)
        bits.append(f"X buzz {xb_s}" + (f" {xt}" if xt else ""))
    if snap_row and snap_row.get("x_buzz") not in (None, "") and xb in (None, ""):
        bits.append(f"Grok X buzz {snap_row.get('x_buzz')}")
    return " · ".join(bits)


def gamma_cell(snap_row):
    """Show a real Grok gamma only; zeroed thin-proof skip becomes '-'."""
    if not snap_row:
        return "-"
    g = snap_row.get("gamma")
    bd = snap_row.get("gamma_breakdown") or {}
    try:
        gf = float(g)
    except (TypeError, ValueError):
        return "-"
    parts = []
    for v in bd.values() if isinstance(bd, dict) else []:
        try:
            parts.append(float(v))
        except (TypeError, ValueError):
            pass
    if gf == 0.0 and (not parts or all(x == 0.0 for x in parts)):
        return "-"
    return f"{gf:.1f}"


def score_block(score, fuel, ign, asof, who):
    sc = fmt_score(score)
    if sc == "-":
        return f'<div class="n">-</div><div class="submeta">fuel - · ign -</div><div class="asof">-</div>'
    return (
        f'<div class="n">{html.escape(sc)}</div>'
        f'<div class="submeta">fuel {html.escape(fmt_fi(fuel))} · ign {html.escape(fmt_fi(ign))}</div>'
        f'<div class="asof">{html.escape(asof)}</div>'
    )


def main():
    cons = load_json(CONS)
    snap = load_json(SNAP)
    claude = load_json(CLAUDE)
    grok_asof = grok_table_asof(snap, SNAP) or GROK_ASOF
    claude_asof = claude_table_asof(claude, cons) or CLAUDE_ASOF

    grok_by = {r["ticker"].upper(): r for r in snap.get("results") or []}
    cl_si = {}
    for key in ("si_universe", "watchlist", "borrow_watch"):
        for r in claude.get(key) or []:
            tk = str(r.get("ticker") or "").upper()
            if tk and tk not in cl_si:
                cl_si[tk] = r
    cl_enr = {str(r.get("ticker") or "").upper(): r for r in claude.get("enrichment") or []}
    cl_wsb = {str(r.get("ticker") or "").upper(): r for r in claude.get("wsb") or []}

    rows = {}
    cons_all = set()
    for r in cons.get("agreements") or []:
        rows[r["ticker"].upper()] = dict(r)
        cons_all.add(r["ticker"].upper())
    dropped_onesided = []
    for bucket in ("only_grok", "only_claude"):
        for r in cons.get(bucket) or []:
            tk = r["ticker"].upper()
            cons_all.add(tk)
            if tk in rows:
                continue
            snap_row = grok_by.get(tk)
            cl_row = cl_si.get(tk)
            if keep_onesided(r, snap_row, cl_row):
                rows[tk] = dict(r)
            else:
                dropped_onesided.append(tk)

    # Fuel / HTB names in last_snapshot that consensus never included (e.g. SLS).
    extra = []
    for snap_row in snap.get("results") or []:
        tk = str(snap_row.get("ticker") or "").upper()
        if not tk or tk in rows or tk in cons_all:
            continue
        if not has_squeeze_fuel(snap_row):
            fee = snap_row.get("borrow_fee_pct") or 0
            if fee < 20:
                continue
        extra.append(tk)
        cl_row = cl_si.get(tk)
        enr = cl_enr.get(tk) or {}
        rows[tk] = {
            "ticker": tk,
            "level": "FUEL / HTB",
            "grok_score": snap_row.get("score"),
            "grok_tier": snap_row.get("tier"),
            "grok_fuel": snap_row.get("fuel"),
            "grok_ign": snap_row.get("ign"),
            "grok_flags": snap_row.get("flags") or [],
            "claude_score": None,
            "claude_tier": None,
            "claude_fuel": None,
            "claude_ign": None,
            "claude_flags": enr.get("flags") or [],
            "price": snap_row.get("price") or (cl_row or {}).get("price") or enr.get("price"),
            "si_pct": snap_row.get("si_pct") if snap_row.get("si_pct") not in (None, "") else (cl_row or {}).get("si_pct"),
        }

    board = []
    tape_n = float_n = gamma_n = si_n = 0
    for tk, r in rows.items():
        snap_row = grok_by.get(tk)
        cl_row = cl_si.get(tk)
        enr = cl_enr.get(tk) or {}

        float_v = None
        if snap_row and snap_row.get("float_m") not in (None, ""):
            float_v = snap_row["float_m"]
        elif cl_row and cl_row.get("float_m") not in (None, ""):
            float_v = cl_row["float_m"]

        gtxt = gamma_cell(snap_row)
        gamma_blank = gtxt == "-"

        extra_tape = []
        if snap_row:
            for sig in snap_row.get("exit_signals") or []:
                extra_tape.append(sig)
            fee = snap_row.get("borrow_fee_pct")
            if fee and float(fee) >= 20:
                extra_tape.append(f"HTB borrow {float(fee):.2f}%")
        tape = pick_tape(
            [
                (snap_row or {}).get("flags"),
                r.get("grok_flags"),
                r.get("claude_flags"),
                enr.get("flags"),
            ],
            gamma_blank,
            extra=extra_tape,
        )

        si = r.get("si_pct")
        if si in (None, "") and snap_row:
            si = snap_row.get("si_pct")
        if si in (None, "") and cl_row:
            si = cl_row.get("si_pct")

        grok = r.get("grok_score")
        claude_s = r.get("claude_score")
        grok_fuel = r.get("grok_fuel")
        grok_ign = r.get("grok_ign")
        if grok_fuel is None and snap_row:
            grok_fuel = snap_row.get("fuel")
        if grok_ign is None and snap_row:
            grok_ign = snap_row.get("ign")
        claude_fuel = r.get("claude_fuel")
        claude_ign = r.get("claude_ign")

        level = r.get("level") or "DUAL BOARD"
        mx = max(
            float(grok) if grok is not None else -1.0,
            float(claude_s) if claude_s is not None else -1.0,
        )
        rec = {
            "ticker": tk,
            "level": level,
            "grok": grok,
            "claude": claude_s,
            "grok_tier": r.get("grok_tier"),
            "claude_tier": r.get("claude_tier"),
            "grok_fuel": grok_fuel,
            "grok_ign": grok_ign,
            "claude_fuel": claude_fuel,
            "claude_ign": claude_ign,
            "si": si,
            "float": float_v,
            "tape": tape,
            "gamma": gtxt,
            "price": r.get("price"),
            "why": live_why(tk, snap_row, cl_wsb.get(tk), enr),
            "fee": (snap_row or {}).get("borrow_fee_pct"),
            "_rank": (LEVEL_RANK.get(level, 9), -mx, tk),
        }
        board.append(rec)
        if tape != "-":
            tape_n += 1
        if float_v not in (None, ""):
            float_n += 1
        if gtxt != "-":
            gamma_n += 1
        if si not in (None, ""):
            si_n += 1

    board.sort(key=lambda x: x["_rank"])

    n_dual = sum(1 for r in board if str(r["level"]).startswith("DUAL"))
    n_grok = sum(1 for r in board if r["level"] == "GROK ONLY")
    n_claude = sum(1 for r in board if r["level"] == "CLAUDE ONLY")
    n_trig = sum(1 for r in board if r["level"] == "DUAL TRIGGER")
    n_watch = sum(1 for r in board if r["level"] == "DUAL WATCH")
    n_ign = sum(1 for r in board if r["level"] == "DUAL IGNITION")
    n_fuel = sum(1 for r in board if r["level"] == "FUEL / HTB")

    persist = load_buyable_persist()
    if persist:
        buyable = [
            persist_to_card(r)
            for r in (persist.get("names") or [])
            if r.get("level") in ("DUAL TRIGGER", "DUAL WATCH")
        ]
        buyable_grok_asof = persist.get("as_of_label") or persist.get("as_of") or "full-options Premarket"
        buyable_source = "full-options"
        buyable_as_of = persist.get("as_of")
    else:
        buyable = [persist_to_card(r) for r in board if r["level"] in ("DUAL TRIGGER", "DUAL WATCH")]
        buyable_grok_asof = grok_asof
        buyable_source = "current-consensus"
        buyable_as_of = None
    n_buy_trig = sum(1 for r in buyable if r["level"] == "DUAL TRIGGER")
    n_buy_watch = sum(1 for r in buyable if r["level"] == "DUAL WATCH")

    pos_lines = POS.read_text(encoding="utf-8").strip().splitlines()
    live_pos = [ln for ln in pos_lines[1:] if ln.strip()] if len(pos_lines) > 1 else []
    book_flat = len(live_pos) == 0
    cl_pos = claude.get("positions") or []
    cl_pos_empty = len(cl_pos) == 0

    def esc(s):
        return html.escape("" if s is None else str(s))

    def buyable_cards():
        chunks = []
        if n_buy_trig == 0:
            chunks.append('<div class="card empty">Dual trigger &mdash; none</div>')
        if n_buy_watch == 0:
            chunks.append('<div class="card empty">Dual watch &mdash; none</div>')
        for r in buyable:
            if r["level"] not in ("DUAL TRIGGER", "DUAL WATCH"):
                continue
            px = fmt_px(r["price"])
            si = fmt_si(r["si"])
            meta = " · ".join(x for x in (px, f"SI {si}" if si != "-" else "") if x)
            why = esc(r["why"])
            tk = esc(r["ticker"])
            grok_html = score_block(r["grok"], r["grok_fuel"], r["grok_ign"], buyable_grok_asof if r["grok"] is not None else "-", "grok")
            cl_html = score_block(r["claude"], r["claude_fuel"], r["claude_ign"], claude_asof if r["claude"] is not None else "-", "claude")
            chunks.append(f"""
  <div class="card">
    <div class="name-row">
      <div class="tk"><a href="https://stockanalysis.com/stocks/{tk.lower()}/" target="_blank" rel="noopener">{tk}</a></div>
      <div class="px">{esc(meta)}</div>
    </div>
    <div class="lvl">{esc(r["level"])}</div>
    <div class="scores">
      <div class="score grok">
        <div class="who">Grok</div>
        {grok_html}
        <div class="tier">{esc(r["grok_tier"] or "-")}</div>
      </div>
      <div class="score claude">
        <div class="who">Claude</div>
        {cl_html}
        <div class="tier">{esc(r["claude_tier"] or "-")}</div>
      </div>
    </div>
    {f'<div class="why">{why}</div>' if why else ""}
  </div>""")
        return "\n".join(chunks)

    def table_rows():
        out = []
        for r in board:
            lc = LVL_CLASS.get(r["level"], "l-board")
            tk = esc(r["ticker"])
            g_asof = grok_asof if r["grok"] is not None else "-"
            c_asof = claude_asof if r["claude"] is not None else "-"
            out.append(
                "<tr>"
                f'<td class="tkc"><a href="https://stockanalysis.com/stocks/{tk.lower()}/" target="_blank" rel="noopener">{tk}</a></td>'
                f'<td><span class="pill {lc}">{esc(r["level"])}</span></td>'
                f'<td class="num grok">{esc(fmt_score(r["grok"]))}<div class="submeta">fuel {esc(fmt_fi(r["grok_fuel"]))} · ign {esc(fmt_fi(r["grok_ign"]))}</div><div class="asof">{esc(g_asof)}</div></td>'
                f'<td class="num claude">{esc(fmt_score(r["claude"]))}<div class="submeta">fuel {esc(fmt_fi(r["claude_fuel"]))} · ign {esc(fmt_fi(r["claude_ign"]))}</div><div class="asof">{esc(c_asof)}</div></td>'
                f'<td class="num">{esc(fmt_si(r["si"]))}</td>'
                f'<td class="num">{esc(fmt_float(r["float"]))}</td>'
                f'<td class="tape">{esc(r["tape"])}</td>'
                f'<td class="num">{esc(r["gamma"])}</td>'
                "</tr>"
            )
        return "\n".join(out)

    now = datetime.now(ZoneInfo("America/New_York"))
    built = now.strftime("%Y-%m-%d %I:%M %p ET").replace(" 0", " ")

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0f1117">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Squeeze brief</title>
<style>
  :root {{
    color-scheme: dark;
    --page:#0f1117; --card:#12161f; --ink:#dfe4ea; --muted:#8b93a7;
    --line:#2f3542; --warn:#fab219; --warn-bg:#2a2410; --warn-line:#5a4a12;
    --ok:#2ed573; --hot:#ff6b6b; --ign:#e056fd; --link:#70a1ff;
    --grok:#70a1ff; --claude:#ffa502; --fuel:#7bed9f;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; }}
  body {{
    background: var(--page); color: var(--ink);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    font-size: 17px; line-height: 1.45;
    padding: 18px 16px 48px;
    padding-left: max(16px, env(safe-area-inset-left));
    padding-right: max(16px, env(safe-area-inset-right));
  }}
  .wrap {{ max-width: 720px; margin: 0 auto; }}
  h1 {{ font-size: 22px; font-weight: 700; margin: 0 0 4px; }}
  h2 {{ font-size: 13px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
       color: var(--muted); margin: 22px 0 10px; }}
  .sub {{ color: var(--muted); font-size: 14px; margin: 0 0 14px; }}
  .sub a {{ color: var(--link); text-decoration: none; }}
  .banner {{
    background: var(--warn-bg); border: 1px solid var(--warn-line);
    color: #ffe7a3; border-radius: 12px; padding: 14px 16px; margin-bottom: 8px;
  }}
  .banner b {{ color: var(--warn); }}
  .banner p {{ margin: 0 0 8px; }}
  .banner p:last-child {{ margin: 0; }}
  .card {{
    background: var(--card); border: 1px solid var(--line);
    border-radius: 14px; padding: 16px; margin-bottom: 10px;
  }}
  .card.empty {{ color: var(--muted); }}
  .name-row {{ display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }}
  .tk {{ font-size: 28px; font-weight: 800; letter-spacing: .02em; }}
  .tk a {{ color: inherit; text-decoration: none; }}
  .px {{ font-variant-numeric: tabular-nums; color: var(--muted); font-size: 15px; }}
  .lvl {{ display: inline-block; margin-top: 8px; background: #2a1630; color: var(--ign);
         font-size: 12px; font-weight: 700; letter-spacing: .04em; padding: 6px 10px;
         border-radius: 999px; min-height: 32px; }}
  .scores {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 12px 0; }}
  .score {{
    background: #0c0e14; border: 1px solid var(--line); border-radius: 12px;
    padding: 12px; min-height: 72px;
  }}
  .score .who {{ font-size: 12px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }}
  .score.grok .who {{ color: var(--grok); }}
  .score.claude .who {{ color: var(--claude); }}
  .score .n {{ font-size: 26px; font-weight: 750; font-variant-numeric: tabular-nums; line-height: 1.1; margin-top: 2px; }}
  .score .tier {{ color: var(--muted); font-size: 13px; margin-top: 2px; }}
  .score .submeta, td .submeta {{ color: var(--muted); font-size: 11px; font-weight: 500; margin-top: 3px; white-space: nowrap; }}
  .score .asof, td .asof {{ color: #6b7386; font-size: 10px; margin-top: 1px; }}
  .why {{ font-size: 15px; color: #c8ceda; }}
  .flat {{ font-size: 16px; }}
  .flat b {{ color: var(--ok); }}
  .note {{ color: var(--muted); font-size: 14px; margin-top: 8px; }}
  .counts {{ color: var(--muted); font-size: 14px; margin: -4px 0 10px; }}
  .table-wrap {{
    overflow-x: auto; -webkit-overflow-scrolling: touch;
    border: 1px solid var(--line); border-radius: 14px; background: var(--card);
  }}
  table {{
    width: 100%; border-collapse: collapse; min-width: 680px;
    font-size: 14px;
  }}
  th, td {{
    padding: 10px 8px; text-align: left; border-bottom: 1px solid var(--line);
    vertical-align: top;
  }}
  th {{
    font-size: 11px; letter-spacing: .06em; text-transform: uppercase;
    color: var(--muted); font-weight: 700; position: sticky; top: 0;
    background: #161b26;
  }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.tkc {{ font-weight: 800; letter-spacing: .02em; }}
  td.tkc a {{ color: inherit; text-decoration: none; }}
  td.tape {{ color: #c8ceda; font-size: 13px; max-width: 200px; }}
  td.num.grok {{ color: var(--grok); font-weight: 650; }}
  td.num.claude {{ color: var(--claude); font-weight: 650; }}
  tr:last-child td {{ border-bottom: 0; }}
  .pill {{
    display: inline-block; font-size: 10px; font-weight: 800; letter-spacing: .04em;
    padding: 4px 7px; border-radius: 999px; white-space: nowrap;
  }}
  .l-trig {{ background: #3a1418; color: var(--hot); }}
  .l-watch {{ background: var(--warn-bg); color: var(--warn); }}
  .l-ign {{ background: #2a1630; color: var(--ign); }}
  .l-board {{ background: #1a2230; color: #a8b4c8; }}
  .l-grok {{ background: #152033; color: var(--grok); }}
  .l-claude {{ background: #2a1c0c; color: var(--claude); }}
  .l-fuel {{ background: #10261a; color: var(--fuel); }}
  footer {{ margin-top: 28px; color: var(--muted); font-size: 13px; line-height: 1.5; }}
  footer a {{ color: var(--link); text-decoration: none; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Squeeze brief</h1>
  <p class="sub">Full dual board + one-sided names &middot; two theories, two scores<br>
    <a href="index.html">Kalshi dashboard</a></p>

  <div class="banner">
    <p><b>Full {grok_asof} board.</b> Grok = {grok_asof} full-options scan (56 names; X on candidates; options on top 20; Cap 98&rarr;56). Claude = {claude_asof} (prices/SI mostly Thu 8/20 close; HSI DB unchanged Aug 12; WSB fresh 8/21; ST trending fetch failed; n=126). Scores stay separate. Missing cells are "-" not zeros.</p>
    <p>Buyable = last full-options Premarket (7:36); table = latest scan (this morning's full Premarket). Buyable cards are dual trigger / dual watch only. Dual ignition is research, not a buy card. Paper ledger is not the live book.</p>
  </div>

  <h2>Buyable</h2>
{buyable_cards()}

  <h2>Exit pressure</h2>
  <div class="card flat">
    <b>{"Book is flat." if book_flat else "Live book has rows — check positions.csv."}</b>
    <div class="note">Live file <code>positions.csv</code> is header-only. No fills. Paper ledgers are not the live book.</div>
  </div>

  <h2>Full board</h2>
  <p class="counts">{len(board)} names &middot; {n_dual} dual ({n_trig} trigger / {n_watch} watch / {n_ign} ignition + {n_dual - n_trig - n_watch - n_ign} board) &middot; {n_grok} Grok-only &middot; {n_claude} Claude-only &middot; {n_fuel} fuel/HTB dropped by the join. Scores stay separate. Duplicates in the consensus one-sided lists (SOUN / GRPN / ONDS) are shown once at dual level. One-sided ignition noise without squeeze fuel (BB, QXO-style X/WSB-only) is gated out.</p>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Level</th>
          <th class="num">Grok</th>
          <th class="num">Claude</th>
          <th class="num">SI</th>
          <th class="num">Float</th>
          <th>Tape</th>
          <th class="num">Gamma</th>
        </tr>
      </thead>
      <tbody>
{table_rows()}
      </tbody>
    </table>
  </div>

  <footer>
    One product, two theories &mdash; scores are not merged.<br>
    Under each score: fuel / ignition / that side's as-of. Gamma "-" = no Grok options/gamma on that name. Tape prefers dilution / stop-breach / social-fade over a gamma-ramp flag when Gamma is "-".<br>
    Built {built} &middot; rebuilt from current files (no new scan) &middot; Buyable = last full-options Premarket (7:36) &middot; table = latest scan (this morning's full Premarket) &middot; Grok {grok_asof} + Claude {claude_asof}
  </footer>
</div>
</body>
</html>
"""
    OUT.write_text(page, encoding="utf-8", newline="\n")
    stats = {
        "n": len(board),
        "dual": n_dual,
        "grok_only": n_grok,
        "claude_only": n_claude,
        "fuel_htb": n_fuel,
        "trig": n_trig,
        "watch": n_watch,
        "ign": n_ign,
        "si": si_n,
        "float": float_n,
        "tape": tape_n,
        "gamma": gamma_n,
        "tickers": [r["ticker"] for r in board],
        "buyable": [r["ticker"] for r in buyable],
        "buyable_source": buyable_source,
        "buyable_as_of": buyable_as_of,
        "buyable_grok_asof": buyable_grok_asof,
        "table_trig": n_trig,
        "table_watch": n_watch,
        "fuel_htb_names": [r["ticker"] for r in board if r["level"] == "FUEL / HTB"],
        "dropped_onesided": dropped_onesided,
        "book_flat": book_flat,
        "claude_positions_empty": cl_pos_empty,
        "bytes": OUT.stat().st_size,
        "onds_tape": next((r["tape"] for r in board if r["ticker"] == "ONDS"), None),
        "sls": next(({"level": r["level"], "si": r["si"], "tape": r["tape"], "grok": r["grok"], "fee": r["fee"]} for r in board if r["ticker"] == "SLS"), None),
        "htz_buyable": any(r["ticker"] == "HTZ" and r["level"] in ("DUAL TRIGGER", "DUAL WATCH") for r in buyable),
        "grpn_buyable": any(r["ticker"] == "GRPN" and r["level"] in ("DUAL TRIGGER", "DUAL WATCH") for r in buyable),
    }
    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()
