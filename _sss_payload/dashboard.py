"""HTML dashboard generator for the short-squeeze screener."""
from __future__ import annotations

import html
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional


TIER_COLORS = {
    "RED HOT": "#ff4757",
    "WATCH": "#ffa502",
    "MONITOR": "#70a1ff",
    "BACKGROUND": "#57606f",
}
PRESSURE_COLORS = {"RED": "#ff4757", "AMBER": "#ffa502", "GREEN": "#2ed573"}


def _bar(v: float, mx: float, color: str) -> str:
    pct = 0 if mx == 0 else min(100, v / mx * 100)
    return f'<div class="bar"><div style="width:{pct}%;background:{color}"></div></div>'


def _esc(x: Any) -> str:
    return html.escape(str(x) if x is not None else "")


def build_dashboard(
    results: List[Dict[str, Any]],
    triggers: List[Dict[str, Any]],
    positions: List[Dict[str, Any]],
    meta: Dict[str, Any],
    out_path: Path,
    paper: Optional[Dict[str, Any]] = None,
    consensus: Optional[Dict[str, Any]] = None,
) -> Path:
    d = meta.get("as_of", str(date.today()))

    # ── Position exit-pressure cards ──
    pos_html = ""
    for p in positions:
        c = PRESSURE_COLORS.get(p.get("level", "GREEN"), "#2ed573")
        pl = f'{p["pl_pct"]:+.1f}%' if p.get("pl_pct") is not None else "set entry in positions.csv"
        if p.get("pl_usd") is not None:
            pl += f' / ${p["pl_usd"]:+,.0f}'
        sig = (
            "".join(f'<div class="pos-sig">⚠ {_esc(s)}</div>' for s in p.get("signals", []))
            or '<div class="pos-sig ok">✓ no exit signals — let it work</div>'
        )
        entry_txt = f'${p["entry"]}' if p.get("entry") else "?"
        px_txt = f'${p["price"]}' if p.get("price") else "?"
        stop_txt = ""
        if p.get("stop"):
            dist = f' ({p["stop_dist"]}% below px)' if p.get("stop_dist") is not None else ""
            stop_txt = f' · stop ${p["stop"]}{dist}'
        pos_html += (
            f'<div class="position" style="border-color:{c}">'
            f'<div class="pos-head"><b>{_esc(p["ticker"])}</b> POSITION · '
            f'{p.get("shares") or "?"} sh · entry {entry_txt} → {px_txt} ({pl}){stop_txt} '
            f'<span class="tier" style="background:{c};float:right">EXIT PRESSURE: {_esc(p.get("level"))}</span>'
            f'</div>{sig}</div>'
        )
    if not pos_html:
        pos_html = (
            '<div class="muted" style="margin-bottom:16px">'
            "No open positions. Add rows to <code>data/positions.csv</code> to track exit pressure."
            "</div>"
        )

    # ── Trigger stack ──
    trig_html = ""
    if triggers:
        items = []
        for t in triggers:
            px = t.get("price")
            px_txt = f" @ ${px:.2f}" if isinstance(px, (int, float)) else ""
            items.append(
                f'<div class="trig-item"><b>{_esc(t["ticker"])}</b> '
                f'({t["score"]}, {_esc(t["tier"])}){px_txt}'
                f' — {_esc("; ".join(t["why"]))}</div>'
            )
        trig_html = (
            f'<div class="trigger-stack">'
            f'<div class="trig-head">⚡ IGNITION ALERTS — look / research now '
            f'(NOT buy orders · tier be damned)</div>'
            f'<div class="trig-sub">Something heated up. Open the chart and checklist — '
            f'do not treat this as a purchase recommendation.</div>'
            f"{''.join(items)}</div>"
        )
    else:
        trig_html = (
            '<div class="trigger-stack empty">'
            '<div class="trig-head">⚡ IGNITION ALERTS</div>'
            '<div class="trig-item muted">None this scan. Watch transitions, not standings.</div>'
            "</div>"
        )

    # ── Top ignition among fueled ──
    fueled = [r for r in results if r.get("fuel", 0) >= 12 and r.get("ign", 0) > 0]
    fueled.sort(key=lambda r: -r["ign"])
    ign_html = ""
    if fueled[:5]:
        items = " · ".join(
            f'<b>{_esc(r["ticker"])}</b> ign {r["ign"]}/47 (fuel {r["fuel"]})' for r in fueled[:5]
        )
        ign_html = f'<div class="ign-bar">🔥 TOP IGNITION among fueled names: {items}</div>'

    # ── Legend ──
    legend = """
<div class="legend">
  <div><b>SETUP facts</b> (not buy signals): SI · float · DTC · WSB velocity · volume ·
    gamma · borrow · catalyst · tape structure · X heat
  </div>
  <div style="margin-top:6px"><b>IGNITION ALERTS</b> = look/research now.
    <b>DUAL TRIGGER</b> (Grok×Claude) = strongest dual-system attention for a possible purchase <i>consideration</i>.
    Single-screener alerts alone are never auto-buys.
  </div>
  <div style="margin-top:6px"><b>EXIT markers</b>
    dilution filing · social velocity &lt;1x · parabolic on ≥5x vol ·
    borrow fee/avail normalizing · options flip to puts · 3–4x scale-out (SPRT rule)
  </div>
  <div style="margin-top:6px"><b>Tiers</b>
    <span class="tier" style="background:#ff4757">RED HOT ≥70</span>
    <span class="tier" style="background:#ffa502">WATCH 55–69</span>
    <span class="tier" style="background:#70a1ff">MONITOR 40–54</span>
    <span class="tier" style="background:#57606f">BACKGROUND &lt;40</span>
    &nbsp;· Fuel = SI+float · Ignition = vol+velocity+gamma+catalyst
  </div>
</div>
"""

    # ── Table rows ──
    rows_html = []
    for i, r in enumerate(results):
        t = r["ticker"]
        tc = TIER_COLORS.get(r["tier"], "#57606f")
        flags = " ".join(f'<span class="flag">{_esc(f)}</span>' for f in r.get("flags", []))
        markers = " · ".join(_esc(m) for m in (r.get("entry_markers") or [])[:4])
        exit_bits = " · ".join(_esc(s.split("—")[0].replace("EXIT: ", "")) for s in (r.get("exit_signals") or [])[:2])
        wsb = f'#{r["wsb_rank"]} ({r.get("mentions") or 0}m)' if r.get("wsb_rank") else "—"
        vel = ""
        if r.get("mentions") and r.get("mentions_24h") is not None:
            vel = f'{r["mentions"] / max(1, r["mentions_24h"]):.1f}x'
        elif r.get("velocity_ratio"):
            vel = f'{r["velocity_ratio"]:.1f}x'
        dtc = f'{r["dtc"]}d' if r.get("dtc") else (f'{r["dtc_yahoo"]}d*' if r.get("dtc_yahoo") else "—")
        gam = r["gamma"] if r.get("gamma") else "—"
        xb = f'{r["x_buzz"]:.0f}' if r.get("x_buzz") is not None else "—"
        xtr = ""
        if r.get("x_trend") == "rising":
            xtr = "↑"
        elif r.get("x_trend") == "falling":
            xtr = "↓"
        px = f'${r["price"]:.2f}' if r.get("price") else "—"
        chg = f'{r["chg_pct"]:+.1f}%' if r.get("chg_pct") is not None else "—"
        si = f'{r["si_pct"]:.1f}%' if r.get("si_pct") is not None else "—"
        fl = f'{r["float_m"]}' if r.get("float_m") is not None else "—"
        mb = r.get("mech_breakdown") or {}
        sb = r.get("soc_breakdown") or {}
        gb = r.get("gamma_breakdown") or {}
        detail = (
            f'SI {mb.get("si",0)}/25 · Float {mb.get("float",0)}/15 · Vol {mb.get("vol",0)}/10 &nbsp;|&nbsp; '
            f'Rank {sb.get("rank",0)}/10 · Velocity {sb.get("velocity",0)}/20 · '
            f'Upvotes {sb.get("upvotes",0)}/5 · Brand {sb.get("brand",0)}/10 &nbsp;|&nbsp; '
            f'Gamma OI {gb.get("oi_float",0)}/4 · CallShare {gb.get("call_share",0)}/3 · '
            f'Prem {gb.get("premium",0)}/3 · IV {gb.get("iv",0)}/2'
            f' &nbsp;|&nbsp; <b>Fuel {r.get("fuel",0)}/40 · Ignition {r.get("ign",0)}/47</b>'
        )
        if r.get("x_buzz") is not None:
            detail += (
                f'<br>𝕏 buzz {r["x_buzz"]} · trend {r.get("x_trend") or "—"} · '
                f'accel {r.get("x_accel") if r.get("x_accel") is not None else "—"} · '
                f'mentions {r.get("x_mentions") or 0} · bullish {r.get("x_bullish_pct")}%'
            )
        if markers:
            detail += f'<br><span class="mk-in">SETUP (fuel/heat facts): {markers}</span>'
        if exit_bits:
            detail += f'<br><span class="mk-out">EXIT watch: {exit_bits}</span>'
        detail += (
            f' &nbsp; <a href="https://finance.yahoo.com/quote/{t}/" target="_blank">Yahoo</a> · '
            f'<a href="https://apewisdom.io/stocks/{t}/" target="_blank">ApeWisdom</a> · '
            f'<a href="https://fintel.io/ss/us/{t.lower()}" target="_blank">Fintel SI</a> · '
            f'<a href="https://optioncharts.io/options/{t}" target="_blank">Options</a> · '
            f'<a href="https://stockanalysis.com/stocks/{t.lower()}/" target="_blank">Stats</a>'
        )
        rows_html.append(
            f"""
<tr class="main" onclick="this.nextElementSibling.classList.toggle('show')">
  <td>{i+1}</td>
  <td class="tk"><a href="https://stockanalysis.com/stocks/{t.lower()}/" target="_blank">{_esc(t)}</a></td>
  <td class="nm">{_esc(str(r.get("name",""))[:32])}</td>
  <td class="num">{px}<div class="subnum">{chg}</div></td>
  <td><span class="tier" style="background:{tc}">{_esc(r["tier"])}</span></td>
  <td class="num"><b>{r["score"]}</b>{_bar(r["score"], 100, tc)}</td>
  <td class="num">{r["mech"]}{_bar(r["mech"], 50, "#2ed573")}</td>
  <td class="num">{r["soc"]}{_bar(r["soc"], 50, "#e056fd")}</td>
  <td class="num">{gam}</td>
  <td class="num">{r.get("fuel",0)}</td>
  <td class="num">{r.get("ign",0)}</td>
  <td class="num">{si}</td>
  <td class="num">{fl}M</td>
  <td class="num">{dtc}</td>
  <td class="num">{wsb}</td>
  <td class="num">{vel or "—"}</td>
  <td class="num">{xb}{xtr}</td>
  <td>{flags}</td>
</tr>
<tr class="detail"><td colspan="18">{detail}</td></tr>"""
        )

    paper_html = ""
    if paper:
        open_n = len(paper.get("open") or [])
        closed_n = len(paper.get("closed") or [])
        paper_html = (
            f'<div class="paper">📋 Paper ledger: {open_n} open · {closed_n} closed'
            f' · paper-trades ignition alerts only (not real buys); exits on signal / −25% / 14d</div>'
        )

    # ── Dual consensus strip ──
    consensus_html = ""
    if consensus and not consensus.get("error"):
        cnt = consensus.get("counts") or {}
        ag = consensus.get("agreements") or []
        stars = "".join(
            f'<span class="c-item"><b>{_esc(a["ticker"])}</b> '
            f'<span class="c-lvl">{_esc(a["level"])}</span></span>'
            for a in ag[:12]
        ) or '<span class="muted">No dual agreements this run</span>'
        consensus_html = (
            f'<div class="consensus-bar">'
            f'<div class="trig-head" style="color:#7bed9f">🎯 DUAL CONSENSUS '
            f'(Grok × Claude) — TRIGGER {cnt.get("dual_trigger",0)} · '
            f'WATCH {cnt.get("dual_watch",0)} · IGN {cnt.get("dual_ignition",0)} · '
            f'BOARD {cnt.get("dual_board",0)}'
            f' &nbsp; <a href="consensus.html" style="color:#7bed9f">full report →</a></div>'
            f'<div class="c-row">{stars}</div></div>'
        )
    elif consensus and consensus.get("error"):
        consensus_html = (
            f'<div class="consensus-bar empty"><div class="muted">'
            f'Consensus unavailable: {_esc(consensus.get("error"))}</div></div>'
        )

    # X heat strip
    x_rows = sorted(
        [r for r in results if r.get("x_buzz") is not None],
        key=lambda r: -(r.get("x_buzz") or 0),
    )[:8]
    x_html = ""
    if x_rows:
        items = " · ".join(
            f'<b>{_esc(r["ticker"])}</b> {r["x_buzz"]:.0f}'
            f'{("↑" if r.get("x_trend")=="rising" else "↓" if r.get("x_trend")=="falling" else "")}'
            for r in x_rows
        )
        q = (meta.get("x") or {}).get("quota_remaining")
        x_html = (
            f'<div class="x-bar">𝕏 FinTwit batch: {items}'
            f'{f" · Adanos quota left {q}" if q is not None else ""}</div>'
        )

    doc = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Short Squeeze Screener — { _esc(d) }</title>
<style>
  :root {{ --bg:#0f1117; --card:#12161f; --border:#2f3542; --text:#dfe4ea; --muted:#747d8c; }}
  * {{ box-sizing: border-box; }}
  body {{ background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif;
         margin:0; padding:20px 24px 48px; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .sub {{ color:var(--muted); font-size:13px; margin-bottom:18px; }}
  .legend {{ background:var(--card); border:1px solid var(--border); border-radius:8px;
             padding:12px 16px; margin-bottom:16px; font-size:12px; color:#a4b0be; line-height:1.45; }}
  .position {{ background:var(--card); border:1px solid; border-radius:8px; padding:12px 16px; margin-bottom:10px; }}
  .pos-head {{ font-size:14px; margin-bottom:6px; }}
  .pos-sig {{ font-size:13px; color:#ff9f9f; padding:2px 0; }}
  .pos-sig.ok {{ color:#2ed573; }}
  .trigger-stack {{ background:#1c1420; border:1px solid #ff4757; border-radius:8px;
                    padding:12px 16px; margin-bottom:14px; }}
  .trigger-stack.empty {{ border-color:#3d4450; background:#141820; }}
  .trig-head {{ color:#ff6b6b; font-weight:700; font-size:14px; margin-bottom:4px; }}
  .trig-sub {{ color:#a4b0be; font-size:11px; margin-bottom:8px; line-height:1.4; }}
  .trig-item {{ font-size:13px; padding:3px 0; }}
  .ign-bar {{ background:#1a1a12; border:1px solid #ffa502; border-radius:8px;
              padding:10px 16px; margin-bottom:14px; font-size:13px; color:#ffdd59; }}
  .paper {{ font-size:12px; color:var(--muted); margin-bottom:14px; }}
  .consensus-bar {{ background:#0f1a14; border:1px solid #2ed573; border-radius:8px;
                    padding:12px 16px; margin-bottom:14px; }}
  .consensus-bar.empty {{ border-color:#3d4450; background:#141820; }}
  .c-row {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:6px; font-size:13px; }}
  .c-item {{ background:#1a2420; padding:4px 8px; border-radius:4px; }}
  .c-lvl {{ color:#7bed9f; font-size:10px; margin-left:4px; }}
  .x-bar {{ background:#141820; border:1px solid #57606f; border-radius:8px;
            padding:10px 16px; margin-bottom:14px; font-size:13px; color:#a4b0be; }}
  .muted {{ color:var(--muted); }}
  table {{ border-collapse:collapse; width:100%; font-size:12.5px; }}
  th {{ text-align:left; color:#a4b0be; font-weight:600; padding:8px 8px;
       border-bottom:2px solid var(--border); position:sticky; top:0; background:var(--bg); z-index:2; }}
  td {{ padding:7px 8px; border-bottom:1px solid #1e2430; vertical-align:middle; }}
  tr.main {{ cursor:pointer; }}
  tr.main:hover {{ background:#161b26; }}
  tr.detail {{ display:none; background:#0c0e14; }}
  tr.detail.show {{ display:table-row; }}
  tr.detail td {{ color:#a4b0be; font-size:12px; padding:10px 12px; line-height:1.5; }}
  .tk a {{ color:#70a1ff; text-decoration:none; font-weight:700; }}
  .tk a:hover {{ text-decoration:underline; }}
  .nm {{ color:#a4b0be; max-width:140px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .num {{ font-variant-numeric:tabular-nums; white-space:nowrap; }}
  .subnum {{ font-size:11px; color:var(--muted); }}
  .tier {{ display:inline-block; color:#fff; font-size:10px; font-weight:700;
           padding:2px 7px; border-radius:4px; letter-spacing:0.3px; }}
  .flag {{ display:inline-block; background:#2f1b1b; color:#ff9f9f; font-size:10px;
           padding:2px 6px; border-radius:3px; margin:1px 2px; }}
  .bar {{ height:4px; background:#1e2430; border-radius:2px; margin-top:3px; width:56px; overflow:hidden; }}
  .bar > div {{ height:100%; border-radius:2px; }}
  .mk-in {{ color:#7bed9f; }}
  .mk-out {{ color:#ff6b6b; }}
  a {{ color:#70a1ff; }}
  code {{ background:#1e2430; padding:1px 5px; border-radius:3px; font-size:11px; }}
  .filters {{ display:flex; gap:10px; flex-wrap:wrap; margin-bottom:12px; align-items:center; font-size:13px; }}
  .filters label {{ color:var(--muted); }}
  .filters input, .filters select {{ background:#1e2430; border:1px solid var(--border);
      color:var(--text); border-radius:4px; padding:4px 8px; }}
  footer {{ margin-top:28px; color:var(--muted); font-size:11px; line-height:1.5; }}
</style>
</head><body>
<h1>🚀 Short Squeeze Screener</h1>
<div class="sub">as of { _esc(d) } · {meta.get("n_tickers","?")} names ·
click any row for entry/exit markers &amp; score breakdown ·
score = mechanics 50 + social 50 + gamma bonus 12</div>

{legend}
{pos_html}
{consensus_html}
{trig_html}
{x_html}
{ign_html}
{paper_html}

<div class="filters">
  <label>Min score <input type="number" id="minScore" value="0" min="0" max="100" style="width:64px"></label>
  <label>Tier
    <select id="tierFilter">
      <option value="ALL">All</option>
      <option value="RED HOT">RED HOT</option>
      <option value="WATCH">WATCH</option>
      <option value="MONITOR">MONITOR</option>
      <option value="BACKGROUND">BACKGROUND</option>
    </select>
  </label>
  <label><input type="checkbox" id="trigOnly"> Trigger / flag only</label>
  <label><input type="checkbox" id="fuelOnly"> Fuel ≥12</label>
  <span class="muted" id="rowCount"></span>
</div>

<div style="overflow-x:auto">
<table id="board">
<thead><tr>
  <th>#</th><th>Ticker</th><th>Name</th><th>Price</th><th>Tier</th><th>Score</th>
  <th>Mech</th><th>Soc</th><th>γ</th><th>Fuel</th><th>Ign</th>
  <th>SI%</th><th>Float</th><th>DTC</th><th>WSB</th><th>Vel</th><th>X</th><th>Flags</th>
</tr></thead>
<tbody>
{''.join(rows_html)}
</tbody>
</table>
</div>

<footer>
  Data: Yahoo Finance (price/volume/float/SI/options), ApeWisdom (WSB), StockTwits trending,
  Adanos X/FinTwit (≤10 tickers/scan), highshortinterest.com + <code>data/short_overrides.csv</code>.
  Dual consensus scores Claude&rsquo;s <code>snapshot.json</code> with their screener.py.
  SI is often bi-weekly. Watchlist tool, not a buy-every-signal system. Exit: half at 3–4x, trail rest.
</footer>

<script>
function applyFilters() {{
  const minS = parseFloat(document.getElementById('minScore').value) || 0;
  const tier = document.getElementById('tierFilter').value;
  const trigOnly = document.getElementById('trigOnly').checked;
  const fuelOnly = document.getElementById('fuelOnly').checked;
  const mains = document.querySelectorAll('tr.main');
  let shown = 0;
  mains.forEach(tr => {{
    const score = parseFloat(tr.children[5].innerText) || 0;
    const t = tr.children[4].innerText.trim();
    const flags = tr.children[17].innerText;
    const fuel = parseFloat(tr.children[9].innerText) || 0;
    const hasFlag = flags.trim().length > 0 || flags.includes('TRIGGER') || flags.includes('RAMP') || flags.includes('ACCELER') || flags.includes('X ');
    let ok = score >= minS;
    if (tier !== 'ALL' && t !== tier) ok = false;
    if (trigOnly && !hasFlag) ok = false;
    if (fuelOnly && fuel < 12) ok = false;
    tr.style.display = ok ? '' : 'none';
    const det = tr.nextElementSibling;
    if (!ok) det.classList.remove('show');
    det.style.display = ok && det.classList.contains('show') ? 'table-row' : 'none';
    if (ok) shown++;
  }});
  document.getElementById('rowCount').textContent = shown + ' shown';
}}
['minScore','tierFilter','trigOnly','fuelOnly'].forEach(id => {{
  document.getElementById(id).addEventListener('change', applyFilters);
  document.getElementById(id).addEventListener('input', applyFilters);
}});
document.querySelectorAll('tr.main').forEach(tr => {{
  tr.addEventListener('click', () => {{
    setTimeout(applyFilters, 0);
  }});
}});
applyFilters();
</script>
</body></html>
"""
    out_path = Path(out_path)
    out_path.write_text(doc, encoding="utf-8")
    return out_path
