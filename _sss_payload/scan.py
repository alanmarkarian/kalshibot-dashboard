#!/usr/bin/env python3
"""
Short Squeeze Screener — CLI

Usage:
  python scan.py              Full live scan → console + dashboard.html
  python scan.py --quick      Skip options chains (faster)
  python scan.py --backtest   Validate scoring DNA on GME/SPRT/BBBY/WEN/AMC
  python scan.py --no-browser Don't open the dashboard
  python scan.py --max 20     Cap universe size for speed
  python scan.py --no-x       Skip X/Adanos sentiment call
  python scan.py --no-tape    Skip price/volume microstructure flags
  python scan.py --no-consensus  Skip dual-screener compare vs Claude
  python scan.py --consensus-only  Re-score last snapshot + Claude compare only
  python event_study.py       Mine historical pre-squeeze tape triggers
"""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
HISTORY_FILE = DATA / "history.jsonl"
PAPER_FILE = DATA / "paper_trades.json"
SNAPSHOT_FILE = DATA / "last_snapshot.json"
DASHBOARD_FILE = ROOT / "dashboard.html"
CONSENSUS_FILE = ROOT / "consensus.html"

sys.path.insert(0, str(ROOT))

from scoring import (  # noqa: E402
    EXIT_DOCTRINE,
    exit_signals,
    rank_universe,
    run_backtest,
)
from data_fetch import build_universe, load_positions  # noqa: E402
from dashboard import build_dashboard  # noqa: E402
from x_sentiment import enrich_rows_with_x  # noqa: E402
from tape_features import enrich_rows_with_tape  # noqa: E402
from consensus import persist_buyable_full, print_consensus_summary, run_consensus  # noqa: E402
from claude_snapshot import (  # noqa: E402
    CLAUDE_ENGINE_DIR,
    grok_rows_to_claude_snapshot,
    write_claude_snapshot,
)


def _force_utf8_stdio() -> None:
    """Task Scheduler defaults to cp1252; a single glyph must not kill the run."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass




def load_history() -> List[Dict[str, Any]]:
    if not HISTORY_FILE.exists():
        return []
    by: Dict[str, Dict] = {}
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            by[r["date"]] = r
        except Exception:
            pass
    return [by[d] for d in sorted(by)]


def append_history(asof: str, results: List[Dict[str, Any]]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    hist = load_history()
    tickers = {}
    for r in results:
        tickers[r["ticker"]] = {
            "score": r["score"],
            "tier": r["tier"],
            "price": r.get("price"),
            "si_pct": r.get("si_pct"),
            "fuel": r.get("fuel"),
            "ign": r.get("ign"),
            "flags": r.get("flags") or [],
            "velocity_ratio": r.get("velocity_ratio"),
            "borrow_fee_pct": r.get("borrow_fee_pct"),
            "shares_avail_k": r.get("shares_avail_k"),
            "near_call_oi_k": r.get("near_call_oi_k"),
            "vol_spike": r.get("vol_spike"),
            "x_buzz": r.get("x_buzz"),
            "x_trend": r.get("x_trend"),
            "x_accel": r.get("x_accel"),
        }
    rec = {"date": asof, "tickers": tickers}
    # replace same-day
    hist = [h for h in hist if h.get("date") != asof]
    hist.append(rec)
    HISTORY_FILE.write_text(
        "\n".join(json.dumps(h, ensure_ascii=False) for h in hist) + "\n",
        encoding="utf-8",
    )


def prior_map(hist: List[Dict[str, Any]], asof: str) -> Dict[str, Dict[str, Any]]:
    """Most recent prior run before today."""
    prior = {}
    for h in hist:
        if h.get("date") >= asof:
            continue
        for t, v in (h.get("tickers") or {}).items():
            prior[t] = v
    return prior


def update_paper_trades(
    results: List[Dict[str, Any]],
    triggers: List[Dict[str, Any]],
    asof_str: str,
) -> Dict[str, Any]:
    """Paper-trade every TRIGGER STACK hit. Measures edge without capital."""
    try:
        data = json.loads(PAPER_FILE.read_text(encoding="utf-8")) if PAPER_FILE.exists() else {
            "open": [],
            "closed": [],
        }
    except Exception:
        data = {"open": [], "closed": []}

    idx = {r["ticker"]: r for r in results}
    asof = datetime.strptime(asof_str, "%Y-%m-%d").date()
    still = []
    closed_today = set()

    for tr in data.get("open", []):
        r = idx.get(tr["ticker"], {})
        px = r.get("price") or tr.get("last_price")
        if r.get("price"):
            tr["last_price"] = r["price"]
            tr["last_date"] = asof_str
        days = (asof - datetime.strptime(tr["entry_date"], "%Y-%m-%d").date()).days
        sigs = exit_signals(r, {}) if r else []
        reason = None
        if px and px <= tr["entry"] * 0.75:
            reason = "stop -25%"
        elif sigs:
            reason = "exit signal: " + sigs[0]
        elif days >= 14:
            reason = "time stop 14d"
        if reason and px:
            tr.update(
                exit=px,
                exit_date=asof_str,
                exit_reason=reason,
                pl_pct=round((px / tr["entry"] - 1) * 100, 1),
            )
            data.setdefault("closed", []).append(tr)
            closed_today.add(tr["ticker"])
        else:
            still.append(tr)

    data["open"] = still
    open_tk = {t["ticker"] for t in data["open"]}
    closed_same_day = {
        t["ticker"] for t in data.get("closed", []) if t.get("exit_date") == asof_str
    }

    for t in triggers:
        r = idx.get(t["ticker"])
        px = r.get("price") if r else None
        live_sigs = exit_signals(r, {}) if r else []
        if (
            px
            and not live_sigs
            and t["ticker"] not in open_tk
            and t["ticker"] not in closed_today
            and t["ticker"] not in closed_same_day
        ):
            data["open"].append(
                {
                    "ticker": t["ticker"],
                    "entry": px,
                    "entry_date": asof_str,
                    "last_price": px,
                    "last_date": asof_str,
                    "reason": "; ".join(t["why"]),
                    "score": t["score"],
                }
            )

    PAPER_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def position_report(
    results: List[Dict[str, Any]], positions: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    idx = {r["ticker"]: r for r in results}
    out = []
    for pos in positions:
        t = pos["ticker"]
        r = idx.get(t, {"ticker": t})
        sigs = exit_signals(r, pos)
        entry = pos.get("entry")
        px = r.get("price")
        pl = round((px / entry - 1) * 100, 1) if entry and px else None
        level = (
            "RED"
            if (len(sigs) >= 2 or any("DILUTION" in x for x in sigs))
            else "AMBER"
            if sigs
            else "GREEN"
        )
        stop = pos.get("stop")
        stop_dist = round((px / stop - 1) * 100, 1) if stop and px else None
        shares = pos.get("shares")
        pl_usd = round((px - entry) * shares, 0) if entry and px and shares else None
        out.append(
            {
                "ticker": t,
                "entry": entry,
                "price": px,
                "pl_pct": pl,
                "pl_usd": pl_usd,
                "stop": stop,
                "stop_dist": stop_dist,
                "level": level,
                "signals": sigs,
                "score": r.get("score"),
                "tier": r.get("tier"),
                "shares": shares,
            }
        )
    return out


def print_report(
    results: List[Dict[str, Any]],
    triggers: List[Dict[str, Any]],
    pos_reps: List[Dict[str, Any]],
    paper: Dict[str, Any],
    meta: Dict[str, Any],
    x_meta: Optional[Dict[str, Any]] = None,
    consensus: Optional[Dict[str, Any]] = None,
) -> None:
    print()
    print("=" * 72)
    print(f"  SHORT SQUEEZE SCREENER  ·  {meta.get('as_of')}")
    print("=" * 72)

    # EXIT PRESSURE first (doctrine)
    if pos_reps:
        print("\n-- EXIT PRESSURE (positions) ---------------------------------")
        for p in pos_reps:
            pl = f'{p["pl_pct"]:+.1f}%' if p.get("pl_pct") is not None else "n/a"
            print(f"  [{p['level']:5}] {p['ticker']:6}  {pl:>8}  score={p.get('score')}")
            for s in p.get("signals") or ["(no exit signals)"]:
                print(f"           · {s}")
    else:
        print("\n-- EXIT PRESSURE -- no positions in data/positions.csv")

    if consensus is not None:
        print_consensus_summary(consensus)

    print("\n-- IGNITION ALERTS (look/research - NOT buy orders) ---------")
    if triggers:
        for t in triggers:
            px = t.get("price")
            px_s = f" @ ${px:.2f}" if isinstance(px, (int, float)) else ""
            print(f"  * {t['ticker']:6} {t['score']:>5} {t['tier']:10}{px_s}")
            for w in t["why"]:
                print(f"           · {w}")
    else:
        print("  (none - watch transitions, not standings)")

    # X heat table
    x_rows = [r for r in results if r.get("x_buzz") is not None]
    if x_rows:
        x_rows.sort(key=lambda r: -(r.get("x_buzz") or 0))
        print("\n-- X / FINTWIT (Adanos batch) --------------------------------")
        q = (x_meta or {}).get("quota_remaining")
        print(f"  quota remaining: {q if q is not None else '?'}")
        for r in x_rows[:10]:
            tr = r.get("x_trend") or "-"
            acc = f"{r['x_accel']:.1f}x" if r.get("x_accel") is not None else "-"
            print(
                f"  {r['ticker']:6} buzz {r['x_buzz']:5.1f}  trend {tr:8}  "
                f"accel {acc:>5}  ment {r.get('x_mentions') or 0}"
            )

    fueled = [r for r in results if r.get("fuel", 0) >= 12 and r.get("ign", 0) > 0]
    fueled.sort(key=lambda r: -r["ign"])
    if fueled:
        print("\n-- TOP IGNITION among fueled (fuel>=12) -----------------------")
        for r in fueled[:8]:
            print(
                f"  {r['ticker']:6} ign {r['ign']:4}/47  fuel {r['fuel']:4}/40  "
                f"score {r['score']:5}  {r['tier']}"
            )

    print("\n-- RANKED BOARD (top 20) -------------------------------------")
    print(
        f"  {'#':>2} {'Tkr':6} {'Score':>6} {'Tier':10} {'SI%':>6} {'Float':>7} "
        f"{'DTC':>5} {'Vel':>5} {'X':>5} {'g':>4}  Flags"
    )
    for i, r in enumerate(results[:20], 1):
        si = f"{r['si_pct']:.0f}" if r.get("si_pct") is not None else "-"
        fl = f"{r['float_m']:.0f}M" if r.get("float_m") is not None else "-"
        dtc = f"{r['dtc']:.0f}" if r.get("dtc") else "-"
        vel = f"{r['velocity_ratio']:.1f}x" if r.get("velocity_ratio") else "-"
        gam = f"{r['gamma']}" if r.get("gamma") else "-"
        xb = f"{r['x_buzz']:.0f}" if r.get("x_buzz") is not None else "-"
        flags = ", ".join((r.get("flags") or [])[:2])
        print(
            f"  {i:2} {r['ticker']:6} {r['score']:6} {r['tier']:10} {si:>6} {fl:>7} "
            f"{dtc:>5} {vel:>5} {xb:>5} {gam:>4}  {flags}"
        )

    open_n = len(paper.get("open") or [])
    closed = paper.get("closed") or []
    print(f"\n-- PAPER LEDGER -- open={open_n}  closed={len(closed)}")
    for tr in paper.get("open") or []:
        px = tr.get("last_price") or tr.get("entry")
        pl = (px / tr["entry"] - 1) * 100 if px and tr.get("entry") else 0
        print(f"  OPEN  {tr['ticker']:6} entry ${tr['entry']:.2f}  now ${px:.2f}  {pl:+.1f}%  ({tr.get('reason','')[:50]})")
    if closed[-3:]:
        print("  recent closes:")
        for tr in closed[-3:]:
            print(
                f"  CLOSE {tr['ticker']:6} {tr.get('pl_pct'):+.1f}%  "
                f"({tr.get('exit_reason','')[:48]})"
            )

    print(f"\n  Doctrine: {EXIT_DOCTRINE}")
    print(f"  Dashboard  -> {DASHBOARD_FILE}")
    print(f"  Consensus  -> {CONSENSUS_FILE}")
    print()



def _is_proof_scan(args: argparse.Namespace) -> bool:
    """Thin proofs must not clobber last_snapshot / consensus.
    Midday --quick (default --max 45, options off) is NOT a proof.
    """
    return bool(args.no_x or (args.max is not None and args.max <= 15))


def main() -> int:
    ap = argparse.ArgumentParser(description="Short squeeze stock screener")
    ap.add_argument("--backtest", action="store_true", help="Run historical DNA backtest cases")
    ap.add_argument("--quick", action="store_true", help="Skip options gamma fetch (faster)")
    ap.add_argument("--no-browser", action="store_true", help="Do not open dashboard.html")
    ap.add_argument("--max", type=int, default=45, help="Max SI-universe names to enrich")
    ap.add_argument("--max-options", type=int, default=20, help="Max tickers for options/gamma")
    ap.add_argument("--no-x", action="store_true", help="Skip X/Adanos FinTwit call")
    ap.add_argument("--no-tape", action="store_true", help="Skip event-study tape microstructure flags")
    ap.add_argument("--no-consensus", action="store_true", help="Skip dual-screener consensus")
    ap.add_argument(
        "--consensus-only",
        action="store_true",
        help="Re-run consensus on last_snapshot.json only (no market fetch)",
    )
    args = ap.parse_args()
    _force_utf8_stdio()

    if args.backtest:
        run_backtest()
        return 0

    DATA.mkdir(parents=True, exist_ok=True)

    if args.consensus_only:
        if not SNAPSHOT_FILE.exists():
            print("No data/last_snapshot.json - run a full scan first.")
            return 1
        snap = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
        results = snap.get("results") or []
        triggers = snap.get("triggers") or []
        csnap = grok_rows_to_claude_snapshot(snap)
        write_claude_snapshot(csnap)
        consensus = run_consensus(
            results, triggers, {"as_of": snap.get("as_of")}, claude_dir=CLAUDE_ENGINE_DIR
        )
        print_consensus_summary(consensus)
        if not args.no_browser:
            try:
                webbrowser.open(CONSENSUS_FILE.as_uri())
            except Exception:
                pass
        return 0

    max_opt = 0 if args.quick else args.max_options
    print("Building universe...")
    rows, meta = build_universe(max_si_names=args.max, max_options=max_opt)

    positions = meta.get("positions") or load_positions()

    print("X / FinTwit sentiment...")
    rows, x_meta = enrich_rows_with_x(
        rows, positions=positions, limit=10, enabled=not args.no_x
    )
    meta["x"] = x_meta

    print("Tape microstructure (event-study flags)...")
    rows = enrich_rows_with_tape(rows, max_names=25, enabled=not args.no_tape)

    asof = meta.get("as_of") or date.today().isoformat()
    hist = load_history()
    prior = prior_map(hist, asof)

    results, triggers = rank_universe(rows, date.fromisoformat(asof), prior)

    proof = _is_proof_scan(args)
    csnap = grok_rows_to_claude_snapshot(
        results,
        as_of=asof,
        watchlist=(meta.get("watchlist") if isinstance(meta, dict) else None),
        positions=positions,
    )
    if proof:
        proof_dir = DATA / "claude_engine_proof"
        proof_dir.mkdir(parents=True, exist_ok=True)
        (proof_dir / "snapshot.json").write_text(
            json.dumps(csnap, indent=1, default=str) + "\n", encoding="utf-8"
        )
        claude_dir = proof_dir
        print("PROOF SCAN: Claude snapshot -> data/claude_engine_proof (live claude_engine not overwritten)")
    else:
        write_claude_snapshot(csnap)
        claude_dir = CLAUDE_ENGINE_DIR

    consensus = None
    if not args.no_consensus:
        if proof:
            print("PROOF SCAN: will not overwrite last_snapshot.json or consensus.json (--max <=15 or --no-x)")
        consensus = run_consensus(
            results,
            triggers,
            {"as_of": asof, "x": x_meta},
            claude_dir=claude_dir,
            persist=not proof,
        )
        # Persist Buyable only when options/gamma actually ran (not --quick).
        if (not proof) and max_opt > 0 and consensus and not consensus.get("error"):
            now = datetime.now()
            hh = now.strftime("%I").lstrip("0") or "12"
            label = f"{now.month}/{now.day} {now.strftime('%a')} {hh}:{now.strftime('%M')} Premarket"
            persist_buyable_full(consensus, as_of=asof, as_of_label=label)

    pos_reps = position_report(results, positions)
    paper = update_paper_trades(results, triggers, asof)
    append_history(asof, results)

    # Persist snapshot for re-score offline
    snap = {
        "as_of": asof,
        "meta": {k: v for k, v in meta.items() if k != "positions"},
        "positions": positions,
        "results": [
            {k: v for k, v in r.items() if not callable(v)}
            for r in results
        ],
        "triggers": triggers,
        "consensus_counts": (consensus or {}).get("counts"),
    }
    if _is_proof_scan(args):
        print("[skip] last_snapshot.json left in place (proof scan)")
    else:
        try:
            SNAPSHOT_FILE.write_text(json.dumps(snap, indent=1, default=str), encoding="utf-8")
        except Exception as e:
            print(f"[warn] could not write snapshot: {e}")

    build_dashboard(
        results, triggers, pos_reps, meta, DASHBOARD_FILE, paper, consensus=consensus
    )
    print_report(results, triggers, pos_reps, paper, meta, x_meta, consensus)

    if not args.no_browser:
        try:
            webbrowser.open(DASHBOARD_FILE.as_uri())
            if consensus and not consensus.get("error") and CONSENSUS_FILE.exists():
                webbrowser.open(CONSENSUS_FILE.as_uri())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
