"""
Dual-screener consensus: Grok ShortSqueezeScreener vs Claude Meme Screener.

Loads Claude's snapshot.json, scores it with Claude's own screener.py (their logic),
compares to this run's results, and writes:
  data/consensus.json
  consensus.html

Agreement tiers (strongest → weakest):
  DUAL TRIGGER   — both trigger stacks name the ticker
  DUAL WATCH     — both tier WATCH or RED HOT
  DUAL IGNITION  — both fuel≥12 and ign≥15 (or either has ignition flags)
  DUAL BOARD     — both in top-15 by composite score
  ONE-SIDED      — only one system cares
"""
from __future__ import annotations

import html
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_CLAUDE = Path(r"C:\Users\alanm\OneDrive\Documents\Claude Meme Screener")

WATCH_TIERS = {"WATCH", "RED HOT"}
IGN_FLAGS = (
    "WSB CHATTER ACCELERATING",
    "GAMMA RAMP",
    "VOLUME SPIKE",
    "X TRENDING",
    "X ACCELERATING",
    "X TREND RISING",
    "PRE-CATALYST",
    "TIER JUMP",
)


def resolve_claude_dir(claude_dir: Optional[Path] = None) -> Path:
    if claude_dir is not None:
        return Path(claude_dir)
    engine = DATA / "claude_engine"
    if (engine / "snapshot.json").exists():
        return engine
    return DEFAULT_CLAUDE


def _claude_screener_path() -> Path:
    vendored = ROOT / "claude_screener.py"
    if vendored.exists():
        return vendored
    return DEFAULT_CLAUDE / "screener.py"


def _load_claude_screener(claude_dir: Optional[Path] = None):
    path = _claude_screener_path()
    if not path.exists() and claude_dir is not None:
        path = Path(claude_dir) / "screener.py"
    if not path.exists():
        raise FileNotFoundError(f"Claude screener not found: {path}")
    spec = importlib.util.spec_from_file_location("claude_meme_screener", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    # We only call run() / load_history; run() is pure and does not write.
    sys.modules["claude_meme_screener"] = mod
    spec.loader.exec_module(mod)
    return mod


def score_claude_snapshot(claude_dir: Optional[Path] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    claude_dir = resolve_claude_dir(claude_dir)
    snap_path = claude_dir / "snapshot.json"
    if not snap_path.exists():
        raise FileNotFoundError(f"No Claude snapshot at {snap_path}")
    snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
    mod = _load_claude_screener(claude_dir)

    asof_str = str(snapshot.get("as_of", date.today().isoformat()))[:10]
    prior = None
    try:
        hist = mod.load_history(claude_dir)
        prior_recs = [h for h in hist if h.get("date", "") < asof_str]
        prior = prior_recs[-1]["tickers"] if prior_recs else None
    except Exception:
        prior = None

    results, triggers = mod.run(snapshot, prior)
    meta = {
        "as_of": snapshot.get("as_of"),
        "asof_date": asof_str,
        "n": len(results),
        "path": str(snap_path),
    }
    return results, triggers, meta


def _has_squeeze_fuel(r: Dict[str, Any]) -> bool:
    """Same gate as scoring.trigger_reasons: chatter without SI/float is not ignition."""
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


def _flag_ignition(r: Dict[str, Any]) -> bool:
    # Ignition (and one-sided ignition noise) requires squeeze fuel,
    # matching scoring.trigger_reasons. X/WSB flags alone are not enough.
    if not _has_squeeze_fuel(r):
        return False
    if (r.get("ign") or 0) >= 15 and (r.get("fuel") or 0) >= 12:
        return True
    if (r.get("fuel") or 0) < 12:
        return False
    flags = " ".join(r.get("flags") or [])
    return any(k in flags for k in IGN_FLAGS)


def _index(results: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {r["ticker"]: r for r in results if r.get("ticker")}


def build_consensus(
    grok_results: List[Dict[str, Any]],
    grok_triggers: List[Dict[str, Any]],
    claude_results: List[Dict[str, Any]],
    claude_triggers: List[Dict[str, Any]],
    grok_meta: Optional[Dict[str, Any]] = None,
    claude_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    g_idx = _index(grok_results)
    c_idx = _index(claude_results)
    g_trig = {t["ticker"] for t in grok_triggers}
    c_trig = {t["ticker"] for t in claude_triggers}

    g_watch = {t for t, r in g_idx.items() if r.get("tier") in WATCH_TIERS}
    c_watch = {t for t, r in c_idx.items() if r.get("tier") in WATCH_TIERS}
    g_ign = {t for t, r in g_idx.items() if _flag_ignition(r)}
    c_ign = {t for t, r in c_idx.items() if _flag_ignition(r)}
    g_top = {r["ticker"] for r in grok_results[:15]}
    c_top = {r["ticker"] for r in claude_results[:15]}

    dual_trigger = sorted(g_trig & c_trig)
    dual_watch = sorted((g_watch & c_watch) - set(dual_trigger))
    dual_ignition = sorted((g_ign & c_ign) - set(dual_trigger) - set(dual_watch))
    dual_board = sorted((g_top & c_top) - set(dual_trigger) - set(dual_watch) - set(dual_ignition))

    only_grok = sorted((g_trig | g_watch | g_ign) - (c_trig | c_watch | c_ign))
    only_claude = sorted((c_trig | c_watch | c_ign) - (g_trig | g_watch | g_ign))

    def row(t: str, level: str) -> Dict[str, Any]:
        g, c = g_idx.get(t, {}), c_idx.get(t, {})
        return {
            "ticker": t,
            "level": level,
            "grok_score": g.get("score"),
            "grok_tier": g.get("tier"),
            "grok_fuel": g.get("fuel"),
            "grok_ign": g.get("ign"),
            "grok_flags": g.get("flags") or [],
            "grok_x_buzz": g.get("x_buzz"),
            "grok_trigger": t in g_trig,
            "claude_score": c.get("score"),
            "claude_tier": c.get("tier"),
            "claude_fuel": c.get("fuel"),
            "claude_ign": c.get("ign"),
            "claude_flags": c.get("flags") or [],
            "claude_x_buzz": c.get("x_buzz"),
            "claude_trigger": t in c_trig,
            "price": g.get("price") or c.get("price"),
            "si_pct": g.get("si_pct") or c.get("si_pct"),
        }

    agreements = (
        [row(t, "DUAL TRIGGER") for t in dual_trigger]
        + [row(t, "DUAL WATCH") for t in dual_watch]
        + [row(t, "DUAL IGNITION") for t in dual_ignition]
        + [row(t, "DUAL BOARD") for t in dual_board]
    )

    return {
        "as_of": date.today().isoformat(),
        "grok_meta": grok_meta or {},
        "claude_meta": claude_meta or {},
        "counts": {
            "dual_trigger": len(dual_trigger),
            "dual_watch": len(dual_watch),
            "dual_ignition": len(dual_ignition),
            "dual_board": len(dual_board),
            "only_grok": len(only_grok),
            "only_claude": len(only_claude),
        },
        "agreements": agreements,
        "only_grok": [row(t, "GROK ONLY") for t in only_grok if t in g_idx],
        "only_claude": [row(t, "CLAUDE ONLY") for t in only_claude if t in c_idx],
        "sets": {
            "dual_trigger": dual_trigger,
            "dual_watch": dual_watch,
            "dual_ignition": dual_ignition,
            "dual_board": dual_board,
            "only_grok": only_grok,
            "only_claude": only_claude,
        },
    }


def write_consensus_html(consensus: Dict[str, Any], out_path: Path) -> Path:
    c = consensus
    counts = c.get("counts") or {}
    level_color = {
        "DUAL TRIGGER": "#ff4757",
        "DUAL WATCH": "#ffa502",
        "DUAL IGNITION": "#e056fd",
        "DUAL BOARD": "#70a1ff",
        "GROK ONLY": "#57606f",
        "CLAUDE ONLY": "#57606f",
    }

    def table(rows: List[Dict], title: str) -> str:
        if not rows:
            return f'<div class="section"><h2>{html.escape(title)}</h2><p class="muted">None</p></div>'
        body = []
        for r in rows:
            lc = level_color.get(r["level"], "#57606f")
            gsc = r.get("grok_score")
            csc = r.get("claude_score")
            gflags = " ".join(
                f'<span class="flag">{html.escape(f)}</span>' for f in (r.get("grok_flags") or [])[:3]
            )
            cflags = " ".join(
                f'<span class="flag">{html.escape(f)}</span>' for f in (r.get("claude_flags") or [])[:3]
            )
            xb = ""
            if r.get("grok_x_buzz") is not None:
                xb = f'X {r["grok_x_buzz"]:.0f}'
            body.append(
                f"<tr>"
                f'<td><b><a href="https://stockanalysis.com/stocks/{r["ticker"].lower()}/" target="_blank">'
                f'{html.escape(r["ticker"])}</a></b></td>'
                f'<td><span class="tier" style="background:{lc}">{html.escape(r["level"])}</span></td>'
                f'<td class="num">{r.get("si_pct") if r.get("si_pct") is not None else "—"}</td>'
                f'<td class="num">{f"${r["price"]:.2f}" if r.get("price") else "—"}</td>'
                f'<td class="num">{gsc if gsc is not None else "—"}'
                f' <span class="sub">{html.escape(str(r.get("grok_tier") or ""))}</span></td>'
                f'<td class="num">{csc if csc is not None else "—"}'
                f' <span class="sub">{html.escape(str(r.get("claude_tier") or ""))}</span></td>'
                f'<td class="num">{xb or "—"}</td>'
                f"<td>{gflags}</td><td>{cflags}</td>"
                f"</tr>"
            )
        return (
            f'<div class="section"><h2>{html.escape(title)}</h2>'
            f"<table><thead><tr>"
            f"<th>Ticker</th><th>Level</th><th>SI%</th><th>Px</th>"
            f"<th>Grok</th><th>Claude</th><th>X</th><th>Grok flags</th><th>Claude flags</th>"
            f"</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
        )

    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dual Screener Consensus — {html.escape(str(c.get("as_of")))}</title>
<style>
 body{{background:#0f1117;color:#dfe4ea;font-family:'Segoe UI',system-ui,sans-serif;margin:0;padding:24px}}
 h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:15px;margin:0 0 10px;color:#ffa502}}
 .sub{{color:#747d8c;font-size:13px;margin-bottom:16px}}
 .cards{{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px}}
 .card{{background:#12161f;border:1px solid #2f3542;border-radius:8px;padding:12px 16px;min-width:110px}}
 .card b{{display:block;font-size:22px;color:#fff}}
 .card span{{font-size:11px;color:#a4b0be}}
 .section{{margin-bottom:22px}}
 table{{border-collapse:collapse;width:100%;font-size:12.5px}}
 th{{text-align:left;color:#a4b0be;padding:8px;border-bottom:2px solid #2f3542}}
 td{{padding:8px;border-bottom:1px solid #1e2430;vertical-align:middle}}
 .num{{font-variant-numeric:tabular-nums}}
 .sub{{color:#747d8c;font-size:11px}}
 .tier{{display:inline-block;color:#fff;font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px}}
 .flag{{display:inline-block;background:#2f1b1b;color:#ff9f9f;font-size:10px;padding:2px 6px;border-radius:3px;margin:1px}}
 .muted{{color:#747d8c}} a{{color:#70a1ff;text-decoration:none}}
 .hero{{background:#1c1420;border:1px solid #ff4757;border-radius:8px;padding:14px 16px;margin-bottom:18px}}
</style></head><body>
<h1>🎯 Dual Screener Consensus</h1>
<div class="sub">Grok ShortSqueezeScreener × Claude Meme Screener · {html.escape(str(c.get("as_of")))}
<br>Claude snapshot: {html.escape(str((c.get("claude_meta") or {}).get("as_of") or "?"))}
 · Grok: {html.escape(str((c.get("grok_meta") or {}).get("as_of") or "?"))}</div>

<div class="hero">
  <b>Dream signal = DUAL TRIGGER</b> (both ignition-alert stacks fire) — strongest dual-system
  attention for a <i>purchase consideration</i>, still not an auto-buy.&nbsp;·&nbsp;
  DUAL WATCH / DUAL IGNITION = strong watch.&nbsp;·&nbsp;
  One-sided names = research only.
</div>

<div class="cards">
  <div class="card"><b>{counts.get("dual_trigger",0)}</b><span>DUAL TRIGGER</span></div>
  <div class="card"><b>{counts.get("dual_watch",0)}</b><span>DUAL WATCH</span></div>
  <div class="card"><b>{counts.get("dual_ignition",0)}</b><span>DUAL IGNITION</span></div>
  <div class="card"><b>{counts.get("dual_board",0)}</b><span>DUAL BOARD</span></div>
  <div class="card"><b>{counts.get("only_grok",0)}</b><span>GROK ONLY</span></div>
  <div class="card"><b>{counts.get("only_claude",0)}</b><span>CLAUDE ONLY</span></div>
</div>

{table(c.get("agreements") or [], "Agreement (both systems)")}
{table(c.get("only_grok") or [], "Grok only (this screener)")}
{table(c.get("only_claude") or [], "Claude only")}

<footer class="muted" style="margin-top:28px;font-size:11px">
  Claude scores from their screener.py + snapshot.json. Grok scores from this run (incl. X/Adanos leg).
  Slightly different logic by design — agreement is the upgrade signal.
</footer>
</body></html>
"""
    out_path = Path(out_path)
    out_path.write_text(doc, encoding="utf-8")
    return out_path



BUYABLE_LEVELS = {"DUAL TRIGGER", "DUAL WATCH"}
BUYABLE_FULL_FILE = DATA / "buyable_full.json"


def _buyable_why(r: Dict[str, Any]) -> str:
    """Compact why from THIS scan's flags / X buzz. Dual-agree names only."""
    bits = []
    flags = " ".join(
        str(f) for f in (r.get("grok_flags") or []) + (r.get("claude_flags") or [])
    )
    if "GAMMA RAMP" in flags.upper():
        bits.append("gamma ramp")
    xb = r.get("grok_x_buzz")
    if xb is None:
        xb = r.get("claude_x_buzz")
    if xb not in (None, ""):
        try:
            xb_s = f"{float(xb):.0f}"
        except (TypeError, ValueError):
            xb_s = str(xb)
        fl = flags.lower()
        trend = ""
        if "falling" in fl or "rolling over" in fl or "exhaustion" in fl:
            trend = " falling"
        elif "rising" in fl:
            trend = " rising"
        bits.append(f"X buzz {xb_s}{trend}")
    return " · ".join(bits)


def persist_buyable_full(
    consensus: Dict[str, Any],
    as_of: Optional[str] = None,
    as_of_label: Optional[str] = None,
    why_by_ticker: Optional[Dict[str, str]] = None,
) -> Path:
    """Write dual-trigger + dual-watch from a FULL-OPTIONS scan.

    Call only when options/gamma actually ran (max_options > 0 / not --quick).
    Midday --quick and --consensus-only must not call this — they must not
    overwrite data/buyable_full.json.
    """
    names = []
    asof = as_of or consensus.get("as_of")
    for r in consensus.get("agreements") or []:
        if r.get("level") not in BUYABLE_LEVELS:
            continue
        tk = str(r.get("ticker") or "").upper()
        why = (why_by_ticker or {}).get(tk) or _buyable_why(r)
        names.append(
            {
                "ticker": tk,
                "level": r["level"],
                "grok_score": r.get("grok_score"),
                "grok_tier": r.get("grok_tier"),
                "grok_fuel": r.get("grok_fuel"),
                "grok_ign": r.get("grok_ign"),
                "claude_score": r.get("claude_score"),
                "claude_tier": r.get("claude_tier"),
                "claude_fuel": r.get("claude_fuel"),
                "claude_ign": r.get("claude_ign"),
                "fuel": r.get("grok_fuel"),
                "ign": r.get("grok_ign"),
                "as_of": asof,
                "price": r.get("price"),
                "si": r.get("si_pct"),
                "si_pct": r.get("si_pct"),
                "why": why,
            }
        )
    names.sort(
        key=lambda x: (
            0 if x["level"] == "DUAL TRIGGER" else 1,
            -(
                max(
                    float(x["grok_score"]) if x.get("grok_score") is not None else -1.0,
                    float(x["claude_score"]) if x.get("claude_score") is not None else -1.0,
                )
            ),
            x["ticker"],
        )
    )
    payload = {
        "source": "full-options",
        "as_of": asof,
        "as_of_label": as_of_label or asof,
        "names": names,
    }
    DATA.mkdir(parents=True, exist_ok=True)
    BUYABLE_FULL_FILE.write_text(
        json.dumps(payload, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"  Buyable persist (full-options) -> {BUYABLE_FULL_FILE} ({len(names)} names)")
    return BUYABLE_FULL_FILE

def run_consensus(
    grok_results: List[Dict[str, Any]],
    grok_triggers: List[Dict[str, Any]],
    grok_meta: Optional[Dict[str, Any]] = None,
    claude_dir: Optional[Path] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    claude_dir = resolve_claude_dir(claude_dir)
    print(f"Consensus: scoring Claude snapshot in {claude_dir} ...")
    try:
        c_results, c_triggers, c_meta = score_claude_snapshot(claude_dir)
    except Exception as e:
        print(f"  [consensus] failed: {e}")
        return {"error": str(e), "as_of": date.today().isoformat(), "agreements": []}

    print(f"  Claude: {len(c_results)} names, {len(c_triggers)} triggers")
    consensus = build_consensus(
        grok_results, grok_triggers, c_results, c_triggers, grok_meta, c_meta
    )
    if persist:
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / "consensus.json").write_text(json.dumps(consensus, indent=2, default=str), encoding="utf-8")
        write_consensus_html(consensus, ROOT / "consensus.html")
    n = consensus.get("counts") or {}
    print(
        f"  Consensus: dual_trigger={n.get('dual_trigger')} dual_watch={n.get('dual_watch')} "
        f"dual_ign={n.get('dual_ignition')} dual_board={n.get('dual_board')} "
        f"only_grok={n.get('only_grok')} only_claude={n.get('only_claude')}"
    )
    if persist:
        print(f"  -> {ROOT / 'consensus.html'}")
    else:
        print("  consensus.json left in place (proof scan, persist=False)")
    return consensus


def print_consensus_summary(consensus: Dict[str, Any]) -> None:
    if consensus.get("error"):
        print(f"\n-- CONSENSUS -- error: {consensus['error']}")
        return
    print("\n-- DUAL-SCREENER CONSENSUS ----------------------------------")
    ag = consensus.get("agreements") or []
    if not ag:
        print("  No dual agreements this run.")
    else:
        for r in ag:
            g = r.get("grok_score")
            c = r.get("claude_score")
            print(
                f"  * {r['level']:14} {r['ticker']:6}  "
                f"Grok {g if g is not None else '-'} / Claude {c if c is not None else '-'}"
            )
    only_g = consensus.get("only_grok") or []
    only_c = consensus.get("only_claude") or []
    if only_g[:5]:
        print("  Grok only:  " + ", ".join(x["ticker"] for x in only_g[:8]))
    if only_c[:5]:
        print("  Claude only: " + ", ".join(x["ticker"] for x in only_c[:8]))
    print(f"  Full report -> {ROOT / 'consensus.html'}")
