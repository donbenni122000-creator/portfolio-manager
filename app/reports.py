"""Printable client documents (HTML, print to PDF from the browser):
  * Investment Policy Statement (IPS) - objectives, risk, allocation, rebalancing policy, constraints, fees.
  * Client statement for any period - value, performance, holdings, allocation, activity, fees, gains, goals.
"""
from __future__ import annotations

import html
from datetime import date

from . import accounts, billing, config, db, direct_index, model_library, planning, portfolio, risk_profile, tax

CSS = """
@font-face{font-family:Raleway;font-weight:100 900;src:url('/static/fonts/raleway-latin-wght-normal.woff2') format('woff2')}
*{box-sizing:border-box} body{font-family:Raleway,Segoe UI,system-ui,sans-serif;font-weight:500;color:#0a0a0a;margin:0;background:#efefef;
  font-variant-numeric:lining-nums tabular-nums;font-feature-settings:"lnum" 1,"tnum" 1;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.page{max-width:880px;margin:24px auto;background:#fff;padding:0 0 44px;box-shadow:0 20px 60px -30px rgba(0,0,0,.35);border-radius:24px;overflow:hidden}
.page>*:not(.brand){margin-left:52px;margin-right:52px;max-width:calc(100% - 104px)} .page>table{width:calc(100% - 104px)}
h1{font-size:44px;line-height:1;letter-spacing:-2px;font-weight:700;margin:0}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.16em;font-weight:700;color:#0a0a0a;border:0;padding:0;margin:34px 0 12px}
h2::before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;background:#DD2F20;margin-right:9px;vertical-align:1px}
.sub{color:rgba(255,255,255,.85);margin-top:10px;font-size:15px} table{width:100%;border-collapse:collapse;font-size:13px;margin:6px 0}
th{text-align:left;color:#8a8a8a;font-weight:700;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;border-bottom:1px solid #eaeaea;padding:8px 6px}
td{padding:8px 6px;border-bottom:1px solid #f2f2f2}
.num{text-align:right} .pos{color:#15803d} .neg{color:#DD2F20}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:22px 52px 10px} .kpi{border:1px solid #ececec;border-radius:18px;padding:14px 16px}
.kpi .l{font-size:10.5px;color:#8a8a8a;text-transform:uppercase;letter-spacing:.1em;font-weight:700} .kpi .v{font-size:24px;font-weight:700;letter-spacing:-.8px;margin-top:4px}
.brand{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;padding:40px 52px 34px;margin:0 0 8px;color:#fff;
  background:radial-gradient(700px 360px at 90% 10%,#ff4a36 0%,#DD2F20 45%,#b8200f 100%)}
.brand h1{color:#fff}
.logo{display:flex;align-items:center;gap:10px;color:#fff;font-weight:700;font-size:16px;letter-spacing:-.3px}
.logo svg{width:30px;height:30px}
.brand .muted{color:rgba(255,255,255,.85)}
.muted{color:#6f6f6f;font-size:12px} p{font-size:14px;line-height:1.6}
.sig{display:grid;grid-template-columns:1fr 1fr;gap:40px;margin-top:48px} .sig div{border-top:2px solid #0a0a0a;padding-top:8px;font-size:12px}
.toolbar{max-width:880px;margin:18px auto 0;text-align:right} .toolbar button{font:inherit;font-weight:600;padding:10px 20px;border-radius:999px;border:0;background:#0a0a0a;color:#fff;cursor:pointer}
.toolbar button:hover{background:#DD2F20}
@media print{body{background:#fff}.page{box-shadow:none;margin:0;border-radius:0}.toolbar{display:none}}
"""


LOGO = ("<svg viewBox='0 0 40 40'><rect width='40' height='40' rx='11' fill='#fff'/><rect x='9' y='22' width='5.2' height='9' rx='1.6' fill='#DD2F20'/>"
        "<rect x='17.4' y='16' width='5.2' height='15' rx='1.6' fill='#DD2F20'/><rect x='25.8' y='9' width='5.2' height='22' rx='1.6' fill='#DD2F20'/>"
        "<path d='M7.5 19.5 L19.5 11.5 L32.5 6.5' stroke='#0a0a0a' stroke-width='2.4' fill='none' stroke-linecap='round'/></svg>")


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _m(v, d=2) -> str:
    return "—" if v is None else (f"-${abs(v):,.{d}f}" if v < 0 else f"${v:,.{d}f}")


def _p(v, d=1, sign=False) -> str:
    return "—" if v is None else (("+" if sign and v > 0 else "") + f"{v * 100:.{d}f}%")


def _wrap(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{_e(title)}</title><style>{CSS}</style></head><body>"
            f"<div class='toolbar'><button onclick='window.print()'>Print / Save as PDF</button></div>"
            f"<div class='page'>{body}</div></body></html>")


def _header(title: str, client: dict, subtitle: str) -> str:
    return (f"<div class='brand'><div><div class='logo'>{LOGO}{_e(config.BRAND_NAME)} · Portfolio Manager</div><h1 style='margin-top:18px'>{_e(title)}</h1>"
            f"<div class='sub'>{_e(subtitle)}</div></div><div class='muted' style='text-align:right'>{_e(client['name'])}<br>"
            f"{_e(client['account_type'])} account<br>Account #{client['id']:06d}<br>Prepared {date.today():%B %d, %Y}</div></div>")


def _svg_line(points: list[float], w=760, h=160, color="#DD2F20") -> str:
    if len(points) < 2:
        return ""
    lo, hi = min(points), max(points)
    rng = (hi - lo) or 1
    pts = " ".join(f"{i * w / (len(points) - 1):.1f},{h - 10 - (v - lo) / rng * (h - 20):.1f}" for i, v in enumerate(points))
    return (f"<svg viewBox='0 0 {w} {h}' width='100%' height='{h}'><polyline fill='none' stroke='{color}' stroke-width='2' points='{pts}'/>"
            f"<text x='0' y='12' font-size='11' fill='#6b7280'>{_m(hi, 0)}</text><text x='0' y='{h - 2}' font-size='11' fill='#6b7280'>{_m(lo, 0)}</text></svg>")


def _alloc_bars(rows: list[tuple[str, float, float]]) -> str:
    out = "<table><tr><th>Asset class</th><th class='num'>Actual</th><th class='num'>Target</th><th style='width:40%'></th></tr>"
    for name, act, tgt in rows:
        out += (f"<tr><td>{_e(name)}</td><td class='num'>{_p(act)}</td><td class='num'>{_p(tgt)}</td><td>"
                f"<div style='background:#e5e7eb;height:8px;border-radius:4px;position:relative'>"
                f"<div style='background:#DD2F20;height:8px;border-radius:4px;width:{min(100, act * 100):.1f}%'></div>"
                f"<div style='position:absolute;top:-3px;left:{min(100, tgt * 100):.1f}%;width:2px;height:14px;background:#111'></div></div></td></tr>")
    return out + "</table>"


def _by_class(val: dict, di_syms=frozenset()):
    act, tgt = {}, {}
    for h in val["holdings"]:
        ac = "Direct index (US equity)" if h["symbol"] in di_syms else h["asset_class"].split(" · ")[0]
        act[ac] = act.get(ac, 0) + h["weight"]
        tgt[ac] = tgt.get(ac, 0) + h["target_weight"]
    act["Cash"] = val["cash_weight"]
    tgt["Cash"] = val["cash_target"]
    return sorted([(k, act.get(k, 0), tgt.get(k, 0)) for k in set(act) | set(tgt)], key=lambda r: -r[2])


# ------------------------------------------------------------------ IPS
def ips(client_id: int) -> str:
    c = portfolio.get_client(client_id)
    prof = risk_profile.profile_by_name(c["risk_profile"])
    val = portfolio.valuation(client_id)
    targets = portfolio.get_targets(client_id)
    cma = planning.client_assumptions(client_id)
    model = None
    if c.get("model_id"):
        try:
            model = model_library.get_model(c["model_id"])["name"]
        except KeyError:
            pass
    answers = c.get("questionnaire") or {}
    qa = "".join(f"<tr><td>{_e(q['question'])}</td><td>{_e(q['options'][int(answers[q['id']])][0])}</td></tr>"
                 for q in risk_profile.QUESTIONNAIRE if q["id"] in answers)
    bens = "".join(f"<tr><td>{_e(b['name'])}</td><td>{_e(b['relationship'])}</td><td>{_e(b['type'])}</td><td class='num'>{b['share']:.0f}%</td></tr>"
                   for b in c["beneficiaries"]) or "<tr><td colspan='4' class='muted'>None designated</td></tr>"
    di = direct_index.get_config(client_id)
    di_syms = set(di["holdings"]) if di else set()
    rows = [(s, risk_profile.asset_class(s), w) for s, w in targets.items() if s not in di_syms]
    if di_syms:
        rows.append((f"Direct index ({len(di_syms)} stocks)", "US Equity - personalized S&P 500",
                     sum(w for s, w in targets.items() if s in di_syms)))
    alloc = "".join(f"<tr><td><b>{_e(s)}</b></td><td>{_e(ac)}</td><td class='num'>{_p(w * (1 - c['cash_target']))}</td>"
                    f"<td class='num'>{_p(max(0, w * (1 - c['cash_target']) - c['drift_abs_band']))} – {_p(w * (1 - c['cash_target']) + c['drift_abs_band'])}</td></tr>"
                    for s, ac, w in sorted(rows, key=lambda r: -r[2]))
    sched = billing.client_schedule(c)
    rules = accounts.rules(c)
    body = _header("Investment Policy Statement", c, f"{prof['name']} risk profile · {model or 'custom'} model")
    body += f"""
    <h2>1. Purpose</h2><p>This Investment Policy Statement (IPS) sets out the objectives, risk tolerance, asset allocation and
    governance for the {_e(c['account_type'])} account of {_e(c['name'])}. It guides investment decisions and is reviewed at least annually
    or when the client's circumstances change.</p>
    <h2>2. Client & account</h2><table><tr><td>Client</td><td>{_e(c['name'])}</td></tr><tr><td>Email / phone</td><td>{_e(c.get('email') or '—')} / {_e(c.get('phone') or '—')}</td></tr>
    <tr><td>Account type</td><td>{_e(c['account_type'])} ({_e(rules['tax'])})</td></tr><tr><td>Current value</td><td>{_m(val['equity'])}</td></tr>
    <tr><td>Stated objective</td><td>{_e(c.get('objective') or '—')}</td></tr></table>
    <table><tr><th>Beneficiary</th><th>Relationship</th><th>Type</th><th class='num'>Share</th></tr>{bens}</table>
    <h2>3. Risk profile</h2><p>Risk score <b>{c['risk_score']:.0f}/100</b> → <b>{_e(prof['name'])}</b>. Risk capacity (horizon, income, liquidity)
    caps risk willingness, so the portfolio never takes more risk than the client can afford.</p><table>{qa}</table>
    <h2>4. Return & risk objectives</h2><table>
    <tr><td>Expected long-run return (capital market assumptions)</td><td class='num'>{_p(cma['expected_return'])} per year</td></tr>
    <tr><td>Expected volatility</td><td class='num'>{_p(cma['volatility'])}</td></tr>
    <tr><td>Target volatility band</td><td class='num'>{_p(prof['vol_band'][0], 0)} – {_p(prof['vol_band'][1], 0)}</td></tr>
    <tr><td>Maximum drawdown tolerance</td><td class='num'>{_p(prof['max_dd'], 0)}</td></tr></table>
    <h2>5. Strategic asset allocation</h2><table><tr><th>Security</th><th>Asset class</th><th class='num'>Target</th><th class='num'>Permitted range</th></tr>{alloc}
    <tr><td><b>Cash</b></td><td>Cash reserve</td><td class='num'>{_p(c['cash_target'])}</td><td></td></tr></table>
    <h2>6. Rebalancing policy</h2><p>Holdings are monitored daily. A rebalance is indicated when any holding drifts more than
    <b>{_p(c['drift_abs_band'], 0)}</b> (absolute) or <b>{_p(c['drift_rel_band'], 0)}</b> of its target (relative). Sells are executed before buys,
    a {_p(c['cash_target'], 0)} cash reserve is maintained, and tax consequences are estimated before trading.</p>
    <h2>7. Constraints & guidelines</h2><table>
    <tr><td>Maximum single-stock position</td><td>{_p(min(c['max_position'], prof['max_position']), 0)}</td></tr>
    <tr><td>Maximum individual-stock sleeve</td><td>{_p(prof['stock_sleeve'], 0)}</td></tr>
    <tr><td>Stop-loss review trigger</td><td>{_p(c['stop_loss_pct'], 0)} below cost</td></tr>
    <tr><td>Direct indexing</td><td>{('Yes — ' + _p(di['sleeve_weight'], 0) + ' sleeve, top ' + str(di['top_n']) + ' names; excludes ' + (', '.join(di['excluded_sectors'] + di['excluded_symbols']) or 'nothing')) if di else 'Not used'}</td></tr>
    <tr><td>Leverage</td><td>{'Margin permitted' if c['margin_enabled'] else 'No margin'}{'' if rules['margin'] else ' (not allowed in retirement accounts)'}</td></tr>
    <tr><td>Options</td><td>{_e(accounts.OPTIONS_LEVELS[c.get('options_level') or 0])}</td></tr></table>
    <h2>8. Tax considerations</h2><p>{'Taxable account: tax lots use the <b>' + _e(c['lot_method']) + '</b> method; tax-loss harvesting is used when losses exceed $200 and 5%, avoiding wash sales. Estimated rates: ' + _p(c['st_tax_rate'], 0) + ' short-term, ' + _p(c['lt_tax_rate'], 0) + ' long-term.' if rules['tax'] == 'taxable' else 'Tax-advantaged (' + _e(rules['tax']) + ') account: trades inside the account do not create taxable gains; tax-loss harvesting is not applicable.'}</p>
    <h2>9. Fees</h2><p>Advisory fee schedule <b>{_e(sched['name'])}</b> ({_e(sched.get('description') or '')}), billed quarterly in arrears on average daily assets.
    Uninvested cash earns the prevailing cash sweep rate.</p>
    <h2>10. Monitoring & review</h2><p>Performance is reported against the policy allocation quarterly. This IPS will be reviewed annually,
    and whenever the client's objectives, time horizon, income or risk tolerance change.</p>
    <p class='muted'>Paper-trading platform — for educational and planning purposes; not a solicitation or personalised investment advice.</p>
    <div class='sig'><div>Client signature / date</div><div>Advisor signature / date</div></div>"""
    return _wrap(f"IPS - {c['name']}", body)


# ------------------------------------------------------------------ statement
def statement(client_id: int, start: date | None = None, end: date | None = None) -> str:
    c = portfolio.get_client(client_id)
    end = end or date.today()
    start = start or date(end.year, end.month, 1)
    val = portfolio.valuation(client_id)
    snaps = db.query("SELECT * FROM snapshots WHERE client_id=? AND date BETWEEN ? AND ? ORDER BY date",
                     (client_id, start.isoformat(), end.isoformat()))
    idx = [x for x in portfolio.time_weighted_index(client_id) if start.isoformat() <= x["date"] <= end.isoformat()]
    opened = date.fromisoformat(c["created_at"][:10])
    begin_val = 0.0 if opened >= start else (snaps[0]["equity"] if snaps else val["equity"])
    end_val = snaps[-1]["equity"] if snaps and end < date.today() else val["equity"]
    flows = db.query_one("SELECT COALESCE(SUM(amount),0) AS s FROM cash_flows WHERE client_id=? AND substr(created_at,1,10) BETWEEN ? AND ?",
                         (client_id, start.isoformat(), end.isoformat()))["s"]
    twr = (idx[-1]["index"] / idx[0]["index"] - 1) if len(idx) >= 2 else 0.0
    acts = [a for a in billing.activity(client_id, 1000) if a["date"] and start.isoformat() <= a["date"][:10] <= end.isoformat()]
    fees = -sum(a["amount"] for a in acts if a["type"] == "fee")
    income = sum(a["amount"] for a in acts if a["type"] in ("interest", "lending_income"))
    realized = db.query("SELECT * FROM realized WHERE client_id=? AND substr(sold_at,1,10) BETWEEN ? AND ?",
                        (client_id, start.isoformat(), end.isoformat()))
    goals = []
    try:
        goals = planning.plan(client_id)["goals"] if planning.list_goals(client_id) else []
    except Exception:
        goals = []
    hold = "".join(f"<tr><td><b>{_e(h['symbol'])}</b><div class='muted'>{_e(h['name'][:34])}</div></td><td class='num'>{h['qty']:,.4f}</td>"
                   f"<td class='num'>{_m(h['price'])}</td><td class='num'>{_m(h['value'])}</td><td class='num'>{_p(h['weight'])}</td>"
                   f"<td class='num {'pos' if h['unrealized'] >= 0 else 'neg'}'>{_m(h['unrealized'])}</td></tr>"
                   for h in val["holdings"] if h["qty"] > 0)
    opts = "".join(f"<tr><td>{_e(o['label'])}</td><td>{_e(o['strategy'])}</td><td class='num'>{o['qty']}</td><td class='num'>{_m(o['market_value'])}</td></tr>"
                   for o in val["options"])
    act_rows = "".join(f"<tr><td>{_e(a['date'][:10])}</td><td>{_e(a['type'])}</td><td>{_e(a['description'])}</td>"
                       f"<td class='num {'pos' if a['amount'] >= 0 else 'neg'}'>{_m(a['amount'])}</td></tr>" for a in acts[:60])
    goal_rows = "".join(f"<tr><td>{_e(g['goal']['name'])}</td><td>{_e(g['goal']['target_date'])}</td><td class='num'>{_m(g['goal']['target_amount'], 0)}</td>"
                        f"<td class='num'><b>{_p(g['probability'], 0)}</b></td><td>{_e(g['status'])}</td></tr>" for g in goals)
    rg = sum(r["gain"] for r in realized)
    body = _header("Client Statement", c, f"{start:%B %d, %Y} – {end:%B %d, %Y}")
    body += f"""<div class='kpis'>
      <div class='kpi'><div class='l'>Ending value</div><div class='v'>{_m(end_val, 0)}</div></div>
      <div class='kpi'><div class='l'>Beginning value</div><div class='v'>{_m(begin_val, 0)}</div>{"<div class='l'>opened " + opened.strftime('%b %d') + "</div>" if opened >= start else ""}</div>
      <div class='kpi'><div class='l'>Return (time-weighted)</div><div class='v {'pos' if twr >= 0 else 'neg'}'>{_p(twr, 2, True)}</div></div>
      <div class='kpi'><div class='l'>Net deposits</div><div class='v'>{_m(flows, 0)}</div></div></div>
    <div class='kpis'><div class='kpi'><div class='l'>Fees paid</div><div class='v'>{_m(fees)}</div></div>
      <div class='kpi'><div class='l'>Interest & lending income</div><div class='v pos'>{_m(income)}</div></div>
      <div class='kpi'><div class='l'>Realized gains</div><div class='v'>{_m(rg)}</div></div>
      <div class='kpi'><div class='l'>Loans outstanding</div><div class='v'>{_m(val['loan_balance'] + max(0, -val['cash']), 0)}</div></div></div>
    <h2>Account value</h2>{_svg_line([s['equity'] for s in snaps]) or "<p class='muted'>Not enough daily history in this period for a chart.</p>"}
    <h2>Allocation vs policy</h2>{_alloc_bars(_by_class(val, direct_index.symbols(client_id)))}
    <h2>Holdings</h2><table><tr><th>Security</th><th class='num'>Quantity</th><th class='num'>Price</th><th class='num'>Value</th><th class='num'>Weight</th><th class='num'>Unrealized</th></tr>{hold}
    <tr><td><b>Cash</b></td><td></td><td></td><td class='num'>{_m(val['cash'])}</td><td class='num'>{_p(val['cash_weight'])}</td><td></td></tr></table>
    {('<h2>Options</h2><table><tr><th>Contract</th><th>Strategy</th><th class=num>Contracts</th><th class=num>Value</th></tr>' + opts + '</table>') if opts else ''}
    <h2>Activity</h2><table><tr><th>Date</th><th>Type</th><th>Description</th><th class='num'>Amount</th></tr>{act_rows or "<tr><td colspan='4' class='muted'>No activity in this period</td></tr>"}</table>
    {('<h2>Goals</h2><table><tr><th>Goal</th><th>Target date</th><th class=num>Target</th><th class=num>Probability</th><th>Status</th></tr>' + goal_rows + '</table>') if goal_rows else ''}
    <p class='muted' style='margin-top:24px'>Paper-trading account — values are simulated. Prices may be delayed or simulated. Not a tax document.</p>"""
    return _wrap(f"Statement - {c['name']}", body)
