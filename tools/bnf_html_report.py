"""
BNF 매매법 백테스트 결과(.tmp/bnf_backtest_trades.csv, .tmp/bnf_backtest_meta.json)를
읽어서 집계/분석하고, 자기완결형 HTML 리포트 하나로 렌더링한다. (v3)

trades.csv에는 다음 조합이 전부 섞여있다:
  - signal_mode: "급락5%"(기존 변형 규칙) / "BNF원전"(이격도-20%+RSI30+MACD전환)
  - entry_mode: "9시30분"(장 시작 30분 후) / "마감30분전"(15시)
  - stop_loss_mode: "이전저점" / "고정-5%" / "고정-8%" (같은 신호를 손절 방식만 바꿔 비교)
  - market_panic: 신호일에 코스피 지수도 같이 빠졌는지(불리언)
  - return_pct / return_pct_after_fee: 세전/세후(근사) 수익률

리포트 구조:
  0) (bnf_owner_*.csv/json이 있으면) 맨 위에 "사장님 규칙" 섹션 — 10시 스캔·14시50분 매수,
     -4%/+10%/3거래일, 원 단위 손익·필요 자금
  1) signal_mode x entry_mode 4가지 조합의 핵심 지표 비교표(맨 위, "이전저점" 손절 기준)
  2) 각 조합별 상세 블록: 손절 방식 비교, 시장패닉 여부 비교, 청산사유별, 승패비율,
     월별 추이, 베스트/워스트, 진행중 포지션 (상세 섹션은 "이전저점" 손절을 기본으로 보여줌)

실행: python -m tools.bnf_html_report
출력: .tmp/bnf_backtest_report.html
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT_DIR = Path(".tmp")
TRADES_PATH = OUT_DIR / "bnf_backtest_trades.csv"
META_PATH = OUT_DIR / "bnf_backtest_meta.json"
REPORT_PATH = OUT_DIR / "bnf_backtest_report.html"
OWNER_TRADES_PATH = OUT_DIR / "bnf_owner_trades.csv"
OWNER_CAPITAL_PATH = OUT_DIR / "bnf_owner_capital.csv"
OWNER_META_PATH = OUT_DIR / "bnf_owner_meta.json"

EXIT_REASON_ORDER = ["손절", "이격도100_익절", "10%_익절", "3일만기_매도"]
EXIT_REASON_LABEL = {
    "손절": "손절",
    "이격도100_익절": "이격도 100 익절",
    "10%_익절": "+10% 익절",
    "3일만기_매도": "3거래일 만기 매도",
}
REASON_COLOR_VARS = {
    "손절": "var(--accent-4)",
    "이격도100_익절": "var(--accent)",
    "10%_익절": "var(--accent-2)",
    "3일만기_매도": "var(--accent-3)",
}
PRIMARY_STOP = "이전저점"
STOP_MODE_ORDER = ["이전저점", "고정-5%", "고정-8%"]
SIGNAL_MODE_ORDER = ["급락5%", "BNF원전"]
ENTRY_MODE_ORDER = ["9시30분", "마감30분전"]
SIGNAL_LABEL = {"급락5%": "급락 5% (변형)", "BNF원전": "BNF원전 (이격도·RSI·MACD)"}
ENTRY_LABEL = {"9시30분": "9시30분 매수(장시작 30분후)", "마감30분전": "15시 매수(마감 30분전)"}


def load_data() -> tuple[pd.DataFrame, dict]:
    trades = pd.read_csv(TRADES_PATH, dtype={"ticker": str})
    with open(META_PATH, encoding="utf-8") as f:
        meta = json.load(f)
    return trades, meta


def _fmt(v, digits=2):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return round(float(v), digits)


def _win_rate(returns: pd.Series) -> float | None:
    if len(returns) == 0:
        return None
    return float((returns > 0).mean() * 100)


def _profit_factor(returns: pd.Series) -> float | None:
    wins = returns[returns > 0].sum()
    losses = abs(returns[returns <= 0].sum())
    if losses == 0:
        return None
    return float(wins / losses)


def build_combo_data(df_primary: pd.DataFrame, df_all_stop: pd.DataFrame) -> dict:
    """df_primary: stop_loss_mode=='이전저점'만. df_all_stop: 세 손절방식 전부(같은 조합)."""
    open_mask = df_primary["exit_reason"] == "진행중"
    open_positions = df_primary[open_mask].copy()
    closed = df_primary[~open_mask].copy()
    closed["return_pct"] = closed["return_pct"].astype(float)
    closed["return_pct_after_fee"] = closed["return_pct_after_fee"].astype(float)

    total_signals = len(df_primary)
    total_closed = len(closed)
    total_open = len(open_positions)
    win_rate = _win_rate(closed["return_pct"])
    avg_return = closed["return_pct"].mean() if total_closed else None
    avg_return_fee = closed["return_pct_after_fee"].mean() if total_closed else None
    profit_factor = _profit_factor(closed["return_pct"]) if total_closed else None

    summary = {
        "total_signals": total_signals,
        "total_closed": total_closed,
        "total_open": total_open,
        "win_count": int((closed["return_pct"] > 0).sum()),
        "loss_count": int((closed["return_pct"] <= 0).sum()),
        "win_rate_pct": _fmt(win_rate),
        "avg_return_pct": _fmt(avg_return),
        "avg_return_after_fee_pct": _fmt(avg_return_fee),
        "profit_factor": _fmt(profit_factor),
    }

    # 손절 방식 비교 (df_all_stop 전체 사용)
    stop_comparison = []
    for mode in STOP_MODE_ORDER:
        sub = df_all_stop[df_all_stop["stop_loss_mode"] == mode]
        sub_closed = sub[sub["exit_reason"] != "진행중"].copy()
        sub_closed["return_pct"] = sub_closed["return_pct"].astype(float)
        n_closed = len(sub_closed)
        stop_comparison.append(
            {
                "mode": mode,
                "count": len(sub),
                "closed": n_closed,
                "win_rate_pct": _fmt(_win_rate(sub_closed["return_pct"])),
                "avg_return_pct": _fmt(sub_closed["return_pct"].mean()) if n_closed else None,
                "stop_triggered": int((sub_closed["exit_reason"] == "손절").sum()),
            }
        )

    # 시장 패닉 여부 비교 (이전저점 청산완료 기준)
    panic_comparison = []
    for label, flag in [("시장 전체 패닉일", True), ("개별 하락(비패닉)", False)]:
        sub = closed[closed["market_panic"] == flag]
        panic_comparison.append(
            {
                "label": label,
                "count": len(sub),
                "win_rate_pct": _fmt(_win_rate(sub["return_pct"])),
                "avg_return_pct": _fmt(sub["return_pct"].mean()) if len(sub) else None,
            }
        )

    exit_breakdown = []
    if total_closed:
        for reason, grp in closed.groupby("exit_reason"):
            exit_breakdown.append(
                {
                    "reason": reason,
                    "label": EXIT_REASON_LABEL.get(reason, reason),
                    "count": int(len(grp)),
                    "share_pct": _fmt(len(grp) / total_closed * 100),
                    "avg_return_pct": _fmt(grp["return_pct"].mean()),
                    "win_rate_pct": _fmt(_win_rate(grp["return_pct"])),
                }
            )
        exit_breakdown.sort(
            key=lambda d: EXIT_REASON_ORDER.index(d["reason"]) if d["reason"] in EXIT_REASON_ORDER else 99
        )

    monthly = []
    if total_closed:
        closed["signal_month"] = pd.to_datetime(closed["signal_date"]).dt.strftime("%Y-%m")
        for month, grp in closed.groupby("signal_month"):
            monthly.append(
                {
                    "month": month,
                    "count": int(len(grp)),
                    "win_rate_pct": _fmt(_win_rate(grp["return_pct"])),
                }
            )
        monthly.sort(key=lambda d: d["month"])

    histogram = []
    if total_closed >= 2:
        values = closed["return_pct"].to_numpy()
        n_bins = min(10, max(4, int(np.sqrt(total_closed))))
        counts, edges = np.histogram(values, bins=n_bins)
        for c, lo, hi in zip(counts, edges[:-1], edges[1:]):
            mid = (lo + hi) / 2
            histogram.append({"label": f"{lo:.1f}~{hi:.1f}%", "count": int(c), "sign": "good" if mid >= 0 else "critical"})

    def _rows(df: pd.DataFrame) -> list[dict]:
        out = []
        for _, r in df.iterrows():
            out.append(
                {
                    "ticker": r["ticker"],
                    "name": r["name"],
                    "signal_date": r["signal_date"],
                    "exit_date": r["exit_date"],
                    "return_pct": _fmt(r["return_pct"]),
                    "exit_reason": EXIT_REASON_LABEL.get(r["exit_reason"], r["exit_reason"]),
                    "market_panic": bool(r["market_panic"]),
                }
            )
        return out

    best_trades = _rows(closed.sort_values("return_pct", ascending=False).head(10))
    worst_trades = _rows(closed.sort_values("return_pct", ascending=True).head(10))

    open_rows = []
    for _, r in open_positions.iterrows():
        open_rows.append(
            {
                "ticker": r["ticker"],
                "name": r["name"],
                "signal_date": r["signal_date"],
                "entry_price": r["entry_price"],
            }
        )

    return {
        "summary": summary,
        "stop_comparison": stop_comparison,
        "panic_comparison": panic_comparison,
        "exit_breakdown": exit_breakdown,
        "monthly": monthly,
        "histogram": histogram,
        "best_trades": best_trades,
        "worst_trades": worst_trades,
        "open_positions": open_rows,
    }


def build_report_data(trades: pd.DataFrame, meta: dict) -> dict:
    combos = {}
    for signal_mode in SIGNAL_MODE_ORDER:
        for entry_mode in ENTRY_MODE_ORDER:
            key = f"{signal_mode}__{entry_mode}"
            df_all = trades[(trades["signal_mode"] == signal_mode) & (trades["entry_mode"] == entry_mode)]
            df_primary = df_all[df_all["stop_loss_mode"] == PRIMARY_STOP]
            combos[key] = build_combo_data(df_primary, df_all)

    failed = meta.get("failed_tickers", [])
    return {
        "meta": {
            "signal_start": meta.get("signal_start"),
            "signal_end": meta.get("signal_end"),
            "run_at": meta.get("run_at"),
            "universe_size": meta.get("universe_size"),
            "failed_count": len(failed),
            "params": meta.get("params", {}),
            "benchmark": meta.get("benchmark_kospi"),
            "am_stats": meta.get("am_stats"),
        },
        "combos": combos,
    }


STYLE = """
:root{
  --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink-2:#52514e;
  --muted:#898781; --grid:#e1e0d9; --baseline:#c3c2b7;
  --border:rgba(11,11,11,0.10);
  --accent:#2a78d6; --accent-2:#eb6834; --accent-3:#1baf7a; --accent-4:#eda100;
  --good:#0ca30c; --good-text:#006300; --critical:#d03b3b;
  color-scheme:light;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink-2:#c3c2b7;
    --muted:#898781; --grid:#2c2c2a; --baseline:#383835;
    --border:rgba(255,255,255,0.10);
    --accent:#3987e5; --accent-2:#d95926; --accent-3:#199e70; --accent-4:#c98500;
    --good:#0ca30c; --good-text:#0ca30c; --critical:#e66767;
    color-scheme:dark;
  }
}
:root[data-theme="dark"]{
  --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink-2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --baseline:#383835;
  --border:rgba(255,255,255,0.10);
  --accent:#3987e5; --accent-2:#d95926; --accent-3:#199e70; --accent-4:#c98500;
  --good:#0ca30c; --good-text:#0ca30c; --critical:#e66767;
  color-scheme:dark;
}
*{box-sizing:border-box;}
body{
  background:var(--page); color:var(--ink);
  font-family:'IBM Plex Sans KR','Segoe UI',-apple-system,sans-serif;
  margin:0; padding:0 20px; padding-block:32px;
}
.wrap{max-width:1120px; margin:0 auto; display:flex; flex-direction:column; gap:22px;}
.mono{font-family:'IBM Plex Mono',ui-monospace,monospace; font-variant-numeric:tabular-nums;}
header.report-header{display:flex; flex-direction:column; gap:6px;}
.eyebrow{font-size:12.5px; font-weight:600; letter-spacing:.08em; color:var(--accent); text-transform:uppercase;}
h1{font-size:clamp(21px,4vw,29px); margin:0; text-wrap:balance; font-weight:700;}
.subtitle{color:var(--ink-2); font-size:14px; line-height:1.6; max-width:72ch;}
.meta-line{color:var(--muted); font-size:12.5px; display:flex; flex-wrap:wrap; gap:4px 14px;}

.card{background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:20px; display:flex; flex-direction:column; gap:14px;}
.card h2{font-size:16px; margin:0; font-weight:700;}
.card h3{font-size:13.5px; margin:0; font-weight:700;}
.card .desc{color:var(--muted); font-size:12.5px; line-height:1.5;}

.combo-block{border-radius:14px; border:1px solid var(--border); overflow:hidden;}
.combo-head{padding:14px 20px; display:flex; align-items:center; gap:10px; flex-wrap:wrap;}
.combo-head.sig-drop{background:color-mix(in srgb, var(--accent) 10%, var(--surface));}
.combo-head.sig-bnf{background:color-mix(in srgb, var(--accent-3) 10%, var(--surface));}
.combo-badge{font-size:11.5px; font-weight:700; padding:3px 9px; border-radius:999px; color:#fff;}
.combo-badge.sig-drop{background:var(--accent);}
.combo-badge.sig-bnf{background:var(--accent-3);}
.combo-badge.entry{background:var(--muted);}
.combo-title{font-size:15.5px; font-weight:700;}
.combo-body{display:flex; flex-direction:column; gap:16px; padding:16px 20px 20px;}

.stat-grid{display:grid; grid-template-columns:repeat(auto-fit,minmax(148px,1fr)); gap:10px;}
.stat-tile{background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:14px; display:flex; flex-direction:column; gap:6px;}
.stat-label{font-size:11.5px; color:var(--ink-2); font-weight:500;}
.stat-value{font-size:21px; font-weight:600;}
.stat-sub{font-size:11px; color:var(--muted);}
.good{color:var(--good-text);} .critical{color:var(--critical);}

.legend{display:flex; gap:14px; flex-wrap:wrap; font-size:12px; color:var(--ink-2);}
.legend-item{display:flex; align-items:center; gap:6px;}
.legend-swatch{width:10px; height:10px; border-radius:3px; flex:none;}

.hbar-row{display:flex; align-items:center; gap:10px;}
.hbar-label{width:150px; flex:none; font-size:12px; color:var(--ink-2);}
.hbar-track{flex:1; height:11px; background:var(--grid); border-radius:6px; overflow:hidden;}
.hbar-fill{height:100%; border-radius:6px;}
.hbar-value{width:150px; flex:none; text-align:right; font-size:12px;}

table{border-collapse:collapse; width:100%; font-size:12.5px;}
th,td{padding:6px 8px; text-align:right; border-bottom:1px solid var(--border); white-space:nowrap;}
th:first-child,td:first-child, th:nth-child(2),td:nth-child(2){text-align:left;}
th{color:var(--muted); font-weight:500; font-size:11px;}
.table-wrap{overflow-x:auto;}

.vbars{display:flex; align-items:flex-end; gap:6px; height:150px; padding-top:18px;}
.vbar-col{display:flex; flex-direction:column; align-items:center; justify-content:flex-end; flex:1; height:100%; min-width:0;}
.vbar-value{font-size:10px; color:var(--ink-2); margin-bottom:4px;}
.vbar{width:100%; max-width:28px; border-radius:4px 4px 0 0;}
.vbar-label{font-size:9.5px; color:var(--muted); margin-top:6px; text-align:center;}

.split{display:grid; grid-template-columns:1fr 1fr; gap:16px;}
.split3{display:grid; grid-template-columns:repeat(3,1fr); gap:12px;}
@media (max-width:760px){ .split, .split3{grid-template-columns:1fr;} }

.wl-bar{display:flex; height:24px; border-radius:6px; overflow:hidden; border:1px solid var(--border);}
.wl-seg{display:flex; align-items:center; justify-content:center; color:#fff; font-size:11px; font-weight:600;}

.assump-list{margin:0; padding-left:18px; display:flex; flex-direction:column; gap:6px; font-size:13px; color:var(--ink-2); line-height:1.55;}
.panic-tag{font-size:10.5px; padding:1px 6px; border-radius:5px; background:var(--grid); color:var(--ink-2);}

footer{color:var(--muted); font-size:11.5px; text-align:center; padding-top:8px;}
"""


def _stat_tile(label, value, sub="", cls=""):
    return f"""<div class="stat-tile"><div class="stat-label">{label}</div><div class="stat-value mono {cls}">{value}</div><div class="stat-sub">{sub}</div></div>"""


def render_top_comparison(combos: dict) -> str:
    def cell(key):
        s = combos[key]["summary"]
        wr = f"{s['win_rate_pct']:.1f}%" if s["win_rate_pct"] is not None else "-"
        ar = f"{s['avg_return_pct']:+.2f}%" if s["avg_return_pct"] is not None else "-"
        arf = f"{s['avg_return_after_fee_pct']:+.2f}%" if s["avg_return_after_fee_pct"] is not None else "-"
        pf = f"{s['profit_factor']:.2f}" if s["profit_factor"] is not None else "-"
        return s, wr, ar, arf, pf

    headers = ""
    rows = {"총 신호": [], "승률": [], "평균 수익률(세전)": [], "평균 수익률(세후 근사)": [], "손익비": []}
    for signal_mode in SIGNAL_MODE_ORDER:
        for entry_mode in ENTRY_MODE_ORDER:
            key = f"{signal_mode}__{entry_mode}"
            headers += f"<th>{SIGNAL_LABEL[signal_mode]}<br><span style='font-weight:400;color:var(--muted)'>{ENTRY_LABEL[entry_mode]}</span></th>"
            s, wr, ar, arf, pf = cell(key)
            rows["총 신호"].append(f"{s['total_signals']} ({s['total_closed']}/{s['total_open']})")
            rows["승률"].append(wr)
            rows["평균 수익률(세전)"].append(ar)
            rows["평균 수익률(세후 근사)"].append(arf)
            rows["손익비"].append(pf)

    body = ""
    for label, vals in rows.items():
        body += f"<tr><td>{label}</td>" + "".join(f"<td class='mono'>{v}</td>" for v in vals) + "</tr>"

    return f"""
<section class="card">
  <h2>신호 방식 x 매수 시점 — 4가지 조합 비교</h2>
  <div class="desc">"급락5%"는 지금까지 쓴 변형 규칙(당일 -5% 급락), "BNF원전"은 BNF 리서치에서 확인한 원조 방식(이격도 -20% + RSI 30 미만 + MACD 음→양 전환, 신호 다음 거래일 매수)입니다. 손절은 4가지 조합 모두 "이전 저점" 기준.</div>
  <div class="table-wrap"><table>
    <thead><tr><th>지표</th>{headers}</tr></thead>
    <tbody>{body}</tbody>
  </table></div>
</section>
"""


def render_combo_section(signal_mode: str, entry_mode: str, data: dict) -> str:
    s = data["summary"]
    is_drop = signal_mode == "급락5%"
    head_cls = "sig-drop" if is_drop else "sig-bnf"

    win_rate_disp = f"{s['win_rate_pct']:.1f}%" if s["win_rate_pct"] is not None else "-"
    avg_ret_disp = f"{s['avg_return_pct']:+.2f}%" if s["avg_return_pct"] is not None else "-"
    avg_ret_cls = "good" if (s["avg_return_pct"] or 0) >= 0 else "critical"
    avg_ret_fee_disp = f"{s['avg_return_after_fee_pct']:+.2f}%" if s["avg_return_after_fee_pct"] is not None else "-"
    pf_disp = f"{s['profit_factor']:.2f}" if s["profit_factor"] is not None else "-"

    stats_html = f"""
<div class="stat-grid">
  {_stat_tile("총 신호", f"{s['total_signals']}건", f"청산완료 {s['total_closed']} · 진행중 {s['total_open']}")}
  {_stat_tile("승률", win_rate_disp, f"이익 {s['win_count']}건 / 손실 {s['loss_count']}건")}
  {_stat_tile("평균 수익률(세전)", avg_ret_disp, "트레이드 1건당 평균", avg_ret_cls)}
  {_stat_tile("평균 수익률(세후 근사)", avg_ret_fee_disp, "매도세+수수료 약 0.2%p 차감")}
  {_stat_tile("손익비", pf_disp, "총이익÷총손실")}
</div>
"""

    # 손절 방식 비교
    stop_rows = ""
    for sc in data["stop_comparison"]:
        wr = f"{sc['win_rate_pct']:.1f}%" if sc["win_rate_pct"] is not None else "-"
        ar = f"{sc['avg_return_pct']:+.2f}%" if sc["avg_return_pct"] is not None else "-"
        stop_rows += (
            f"<tr><td>{sc['mode']}</td><td class='mono'>{sc['closed']}</td>"
            f"<td class='mono'>{wr}</td><td class='mono'>{ar}</td>"
            f"<td class='mono'>{sc['stop_triggered']}</td></tr>"
        )
    stop_html = f"""
<div>
  <h3 style="margin-bottom:8px">손절 방식 비교 (같은 신호, 손절선만 다르게 적용)</h3>
  <div class="table-wrap"><table>
    <thead><tr><th>손절 방식</th><th>청산완료</th><th>승률</th><th>평균 수익률</th><th>손절 발동</th></tr></thead>
    <tbody>{stop_rows}</tbody>
  </table></div>
</div>
"""

    # 시장 패닉 비교
    panic_rows = ""
    for pc in data["panic_comparison"]:
        wr = f"{pc['win_rate_pct']:.1f}%" if pc["win_rate_pct"] is not None else "-"
        ar = f"{pc['avg_return_pct']:+.2f}%" if pc["avg_return_pct"] is not None else "-"
        panic_rows += f"<tr><td>{pc['label']}</td><td class='mono'>{pc['count']}</td><td class='mono'>{wr}</td><td class='mono'>{ar}</td></tr>"
    panic_html = f"""
<div>
  <h3 style="margin-bottom:8px">시장 전체 패닉일 vs 개별 하락일</h3>
  <div class="desc">신호일에 코스피 지수도 같이 빠졌는지로 나눈 비교(코스피 지수를 업종지수의 근사치로 사용).</div>
  <div class="table-wrap"><table>
    <thead><tr><th>구분</th><th>건수</th><th>승률</th><th>평균 수익률</th></tr></thead>
    <tbody>{panic_rows}</tbody>
  </table></div>
</div>
"""

    eb = data["exit_breakdown"]
    max_count = max([r["count"] for r in eb], default=1)
    legend_html = "".join(
        f'<div class="legend-item"><span class="legend-swatch" style="background:{REASON_COLOR_VARS.get(r["reason"],"var(--muted)")}"></span>{r["label"]}</div>'
        for r in eb
    )
    hbar_rows = "".join(
        f"""<div class="hbar-row">
      <div class="hbar-label">{r['label']}</div>
      <div class="hbar-track"><div class="hbar-fill" style="width:{(r['count']/max_count*100 if max_count else 0):.1f}%;background:{REASON_COLOR_VARS.get(r['reason'],'var(--muted)')}"></div></div>
      <div class="hbar-value mono">{r['count']}건({r['share_pct']:.1f}%)·평균{r['avg_return_pct']:+.2f}%</div>
    </div>"""
        for r in eb
    )
    exit_html = f"""
<div>
  <h3 style="margin-bottom:8px">청산 사유별 비교 (손절: 이전 저점 기준)</h3>
  <div class="legend" style="margin-bottom:8px">{legend_html}</div>
  {hbar_rows}
</div>
""" if eb else '<div class="desc">청산 완료된 트레이드가 없습니다.</div>'

    win_pct = s["win_rate_pct"] or 0
    loss_pct = 100 - win_pct if s["total_closed"] else 0
    wl_html = f"""
  <div class="wl-bar">
    <div class="wl-seg" style="width:{win_pct:.2f}%;background:var(--good)">{s['win_count']}건</div>
    <div class="wl-seg" style="width:{loss_pct:.2f}%;background:var(--critical)">{s['loss_count']}건</div>
  </div>
""" if s["total_closed"] else '<div class="desc">데이터 없음</div>'

    hist = data["histogram"]
    max_hist = max([h["count"] for h in hist], default=1)
    hist_cols = "".join(
        f"""<div class="vbar-col"><div class="vbar-value mono">{h['count']}</div>
        <div class="vbar" style="height:{max(h['count']/max_hist*120,3):.0f}px;background:var(--{'good' if h['sign']=='good' else 'critical'})"></div>
        <div class="vbar-label">{h['label']}</div></div>"""
        for h in hist
    )
    hist_html = f'<div class="vbars">{hist_cols}</div>' if hist else '<div class="desc">데이터 부족</div>'

    wl_section = f"""
<div>
  <h3 style="margin-bottom:8px">오른 종목 vs 떨어진 종목</h3>
  <div class="split">
    <div>{wl_html}</div>
    <div>{hist_html}</div>
  </div>
</div>
"""

    monthly = data["monthly"]
    max_month = max([m["count"] for m in monthly], default=1)
    month_cols = "".join(
        f"""<div class="vbar-col"><div class="vbar-value mono">{m['count']}/{m['win_rate_pct']:.0f}%</div>
        <div class="vbar" style="height:{max(m['count']/max_month*120,3):.0f}px;background:var(--accent)"></div>
        <div class="vbar-label">{m['month'][2:]}</div></div>"""
        for m in monthly
    )
    monthly_html = f"""
<div>
  <h3 style="margin-bottom:8px">월별 추이 (신호건수 / 승률)</h3>
  <div class="vbars">{month_cols}</div>
</div>
""" if monthly else ""

    def trades_table(rows):
        if not rows:
            return '<div class="desc">데이터 없음</div>'
        body = "".join(
            f"<tr><td>{r['ticker']}</td><td>{r['name']}</td><td class='mono'>{r['signal_date']}</td>"
            f"<td class='mono {'good' if r['return_pct']>=0 else 'critical'}'>{r['return_pct']:+.2f}%</td>"
            f"<td>{r['exit_reason']}</td><td>{'<span class=\"panic-tag\">패닉</span>' if r['market_panic'] else ''}</td></tr>"
            for r in rows
        )
        return f"""<div class="table-wrap"><table>
        <thead><tr><th>종목코드</th><th>종목명</th><th>매수일</th><th>수익률</th><th>청산사유</th><th></th></tr></thead>
        <tbody>{body}</tbody></table></div>"""

    best_worst_html = f"""
<div>
  <h3 style="margin-bottom:8px">베스트 / 워스트 트레이드</h3>
  <div class="split">
    <div><div class="stat-label" style="margin-bottom:6px">수익률 상위 10건</div>{trades_table(data['best_trades'])}</div>
    <div><div class="stat-label" style="margin-bottom:6px">수익률 하위 10건</div>{trades_table(data['worst_trades'])}</div>
  </div>
</div>
"""

    op = data["open_positions"]
    if op:
        op_body = "".join(
            f"<tr><td>{r['ticker']}</td><td>{r['name']}</td><td class='mono'>{r['signal_date']}</td><td class='mono'>{r['entry_price']:,.0f}</td></tr>"
            for r in op
        )
        open_html = f"""
<div>
  <h3 style="margin-bottom:8px">진행중 포지션 ({len(op)}건)</h3>
  <div class="table-wrap"><table>
    <thead><tr><th>종목코드</th><th>종목명</th><th>매수일</th><th>매수가</th></tr></thead>
    <tbody>{op_body}</tbody>
  </table></div>
</div>
"""
    else:
        open_html = ""

    badge_entry = ENTRY_LABEL[entry_mode]
    return f"""
<div class="combo-block">
  <div class="combo-head {head_cls}">
    <span class="combo-badge {head_cls}">{SIGNAL_LABEL[signal_mode]}</span>
    <span class="combo-badge entry">{badge_entry}</span>
  </div>
  <div class="combo-body">
    {stats_html}
    {stop_html}
    {panic_html}
    {exit_html}
    {wl_section}
    {monthly_html}
    {best_worst_html}
    {open_html}
  </div>
</div>
"""


OWNER_REASON_ORDER = ["손절(-4%)", "익절(+10%)", "3일만기_매도"]
OWNER_REASON_COLOR = {
    "손절(-4%)": "var(--critical)",
    "익절(+10%)": "var(--good)",
    "3일만기_매도": "var(--accent)",
}


def load_owner_data() -> tuple[pd.DataFrame, pd.DataFrame, dict] | None:
    if not (OWNER_TRADES_PATH.exists() and OWNER_META_PATH.exists()):
        return None
    trades = pd.read_csv(OWNER_TRADES_PATH, dtype={"ticker": str})
    capital = pd.read_csv(OWNER_CAPITAL_PATH) if OWNER_CAPITAL_PATH.exists() else pd.DataFrame()
    with open(OWNER_META_PATH, encoding="utf-8") as f:
        meta = json.load(f)
    return trades, capital, meta


def _won(v: float) -> str:
    return f"{v:+,.0f}원"


def render_owner_section(trades: pd.DataFrame, capital: pd.DataFrame, meta: dict) -> str:
    """사장님 규칙(10시 스캔 + 14시50분 매수, -4%/+10%/3거래일) 전용 섹션."""
    p = meta.get("params", {})
    if trades.empty:
        return '<section class="card"><h2>사장님 규칙</h2><div class="desc">기간 중 매수 조건을 충족한 종목이 없었습니다.</div></section>'

    closed = trades[trades["exit_reason"] != "진행중"].copy()
    open_pos = trades[trades["exit_reason"] == "진행중"]
    ret = closed["return_pct"].astype(float)
    pnl_fee = closed["pnl_won_after_fee"].astype(float)
    wr = _win_rate(ret)
    pf = _profit_factor(ret)
    total_pnl = pnl_fee.sum()
    # 시점별 동시 보유가 가장 많았던 날 필요한 돈 대비 수익
    max_cap = meta.get("max_capital") or 0
    roi_on_max = total_pnl / max_cap * 100 if max_cap else None

    stats_html = f"""
<div class="stat-grid">
  {_stat_tile("매수 건수", f"{len(trades)}건", f"청산완료 {len(closed)} · 진행중 {len(open_pos)}")}
  {_stat_tile("승률", f"{wr:.1f}%" if wr is not None else "-", f"이익 {(ret>0).sum()}건 / 손실 {(ret<=0).sum()}건")}
  {_stat_tile("평균 수익률(세전)", f"{ret.mean():+.2f}%" if len(ret) else "-", f"중간값 {ret.median():+.2f}%" if len(ret) else "", "good" if ret.mean() >= 0 else "critical")}
  {_stat_tile("평균 수익률(세후 근사)", f"{closed['return_pct_after_fee'].astype(float).mean():+.2f}%" if len(ret) else "-", f"약 {p.get('round_trip_fee_pct')}%p 차감")}
  {_stat_tile("총 손익(세후 근사)", _won(total_pnl), "청산완료 건 합계", "good" if total_pnl >= 0 else "critical")}
  {_stat_tile("최대 필요 자금", f"{max_cap:,.0f}원", f"{meta.get('max_capital_date')} · 동시 {meta.get('max_positions')}종목")}
  {_stat_tile("최대 자금 대비 수익", f"{roi_on_max:+.1f}%" if roi_on_max is not None else "-", "총 손익 ÷ 최대 필요 자금")}
  {_stat_tile("손익비", f"{pf:.2f}" if pf is not None else "-", "총이익÷총손실(수익률 기준)")}
</div>
"""

    # 청산 사유
    reason_rows = ""
    for reason in OWNER_REASON_ORDER:
        g = closed[closed["exit_reason"] == reason]
        if g.empty:
            continue
        share = len(g) / len(closed) * 100
        reason_rows += f"""<div class="hbar-row">
      <div class="hbar-label">{reason}</div>
      <div class="hbar-track"><div class="hbar-fill" style="width:{share:.1f}%;background:{OWNER_REASON_COLOR[reason]}"></div></div>
      <div class="hbar-value mono">{len(g)}건({share:.1f}%)·평균{g['return_pct'].mean():+.2f}%</div>
    </div>"""
    reason_html = f'<div><h3 style="margin-bottom:8px">어떻게 팔렸나 (청산 사유)</h3>{reason_rows}</div>'

    # 월별 손익(원)
    closed["month"] = pd.to_datetime(closed["signal_date"]).dt.strftime("%Y-%m")
    monthly = closed.groupby("month").agg(
        count=("return_pct", "size"),
        win=("return_pct", lambda s: (s > 0).mean() * 100),
        avg=("return_pct", "mean"),
        pnl=("pnl_won_after_fee", "sum"),
    ).reset_index()
    max_abs = max(monthly["pnl"].abs().max(), 1)
    month_cols = "".join(
        f"""<div class="vbar-col"><div class="vbar-value mono">{r.pnl/10000:+,.0f}만</div>
        <div class="vbar" style="height:{max(abs(r.pnl)/max_abs*120,3):.0f}px;background:var(--{'good' if r.pnl>=0 else 'critical'})"></div>
        <div class="vbar-label">{r.month[2:]}</div></div>"""
        for r in monthly.itertuples()
    )
    month_table = "".join(
        f"<tr><td>{r.month}</td><td class='mono'>{r.count}</td><td class='mono'>{r.win:.1f}%</td>"
        f"<td class='mono {'good' if r.avg>=0 else 'critical'}'>{r.avg:+.2f}%</td>"
        f"<td class='mono {'good' if r.pnl>=0 else 'critical'}'>{_won(r.pnl)}</td></tr>"
        for r in monthly.itertuples()
    )
    monthly_html = f"""
<div>
  <h3 style="margin-bottom:8px">월별 손익 (세후 근사, 원)</h3>
  <div class="split">
    <div class="vbars">{month_cols}</div>
    <div class="table-wrap"><table>
      <thead><tr><th>월</th><th>건수</th><th>승률</th><th>평균</th><th>손익</th></tr></thead>
      <tbody>{month_table}</tbody></table></div>
  </div>
</div>
"""

    # 자금: 필요 자금이 가장 컸던 날 상위 10일 + 평균
    cap_html = ""
    if not capital.empty:
        active = capital[capital["positions"] > 0]
        top = capital.sort_values("capital", ascending=False).head(10)
        top_rows = "".join(
            f"<tr><td>{r.date}</td><td class='mono'>{r.new_buys}</td><td class='mono'>{r.positions}</td><td class='mono'>{r.capital:,.0f}원</td></tr>"
            for r in top.itertuples()
        )
        cap_html = f"""
<div>
  <h3 style="margin-bottom:8px">돈이 얼마나 필요했나</h3>
  <div class="desc">매수 날 14시50분 직후 들고 있는 종목 수·투입금액 합계. 포지션이 있었던 날 평균 {active['positions'].mean():.1f}종목 · {active['capital'].mean():,.0f}원, 중간값 {active['capital'].median():,.0f}원. 아래는 가장 많이 필요했던 10일.</div>
  <div class="table-wrap"><table>
    <thead><tr><th>날짜</th><th>그날 신규 매수</th><th>보유 종목 수</th><th>필요 자금</th></tr></thead>
    <tbody>{top_rows}</tbody></table></div>
</div>
"""

    # 시장 패닉 vs 개별 하락
    panic_rows = ""
    for label, flag in [("시장 전체 패닉일(코스피도 -1% 이하)", True), ("개별 하락일", False)]:
        g = closed[closed["market_panic"] == flag]
        if g.empty:
            continue
        panic_rows += (
            f"<tr><td>{label}</td><td class='mono'>{len(g)}</td><td class='mono'>{_win_rate(g['return_pct']):.1f}%</td>"
            f"<td class='mono'>{g['return_pct'].mean():+.2f}%</td><td class='mono'>{_won(g['pnl_won_after_fee'].sum())}</td></tr>"
        )
    panic_html = f"""
<div>
  <h3 style="margin-bottom:8px">시장 전체가 빠진 날 vs 그 종목만 빠진 날</h3>
  <div class="table-wrap"><table>
    <thead><tr><th>구분</th><th>건수</th><th>승률</th><th>평균 수익률</th><th>손익(세후)</th></tr></thead>
    <tbody>{panic_rows}</tbody></table></div>
</div>
"""

    def tbl(df):
        body = "".join(
            f"<tr><td>{r.ticker}</td><td>{r.name}</td><td class='mono'>{r.signal_date}</td>"
            f"<td class='mono'>{r.quantity}주</td>"
            f"<td class='mono {'good' if r.return_pct>=0 else 'critical'}'>{r.return_pct:+.2f}%</td>"
            f"<td class='mono'>{_won(r.pnl_won_after_fee)}</td><td>{r.exit_reason}</td></tr>"
            for r in df.itertuples()
        )
        return f"""<div class="table-wrap"><table>
        <thead><tr><th>종목코드</th><th>종목명</th><th>매수일</th><th>수량</th><th>수익률</th><th>손익</th><th>사유</th></tr></thead>
        <tbody>{body}</tbody></table></div>"""

    best_worst_html = f"""
<div>
  <h3 style="margin-bottom:8px">베스트 / 워스트</h3>
  <div class="split">
    <div><div class="stat-label" style="margin-bottom:6px">수익 상위 10건</div>{tbl(closed.sort_values('return_pct', ascending=False).head(10))}</div>
    <div><div class="stat-label" style="margin-bottom:6px">손실 상위 10건</div>{tbl(closed.sort_values('return_pct').head(10))}</div>
  </div>
</div>
"""

    open_html = ""
    if not open_pos.empty:
        rows = "".join(
            f"<tr><td>{r.ticker}</td><td>{r.name}</td><td class='mono'>{r.signal_date}</td><td class='mono'>{r.entry_price:,.0f}</td><td class='mono'>{r.quantity}주</td></tr>"
            for r in open_pos.itertuples()
        )
        open_html = f"""
<div>
  <h3 style="margin-bottom:8px">아직 보유 중 ({len(open_pos)}건)</h3>
  <div class="table-wrap"><table>
    <thead><tr><th>종목코드</th><th>종목명</th><th>매수일</th><th>매수가</th><th>수량</th></tr></thead>
    <tbody>{rows}</tbody></table></div>
</div>
"""

    rule_desc = (
        f"10시에 전일 대비 {p.get('drop_threshold_pct'):.0f}% 이하 → 14시50분에도 {p.get('drop_threshold_pct'):.0f}% 이하면 매수 "
        f"(종목당 {p.get('budget_won', 0):,.0f}원 이내, 1주가 넘으면 1주). "
        f"장중 {p.get('stop_pct'):.0f}% 손절 / +{p.get('take_profit_pct'):.0f}% 익절, 둘 다 아니면 "
        f"{p.get('hold_trading_days')}거래일째 종가 매도. 10시 기준 후보 {meta.get('candidate_days')}일 중 {len(trades)}건 매수."
    )
    return f"""
<div class="combo-block">
  <div class="combo-head sig-drop">
    <span class="combo-badge sig-drop">사장님 규칙</span>
    <span class="combo-badge entry">10시 스캔 · 14시50분 매수</span>
  </div>
  <div class="combo-body">
    <div class="desc">{rule_desc}</div>
    {stats_html}
    {reason_html}
    {monthly_html}
    {cap_html}
    {panic_html}
    {best_worst_html}
    {open_html}
  </div>
</div>
"""


def render_html(data: dict, owner_html: str = "") -> str:
    m = data["meta"]
    title = "BNF 매매법 백테스트"

    benchmark_line = ""
    if m["benchmark"]:
        b = m["benchmark"]
        benchmark_line = f"코스피 지수 {b['return_pct']:+.2f}%(참고용, 8개월 보유 기준)"

    am = m.get("am_stats") or {}
    am_drop = am.get("급락5%", {})
    am_candidate_lines = "".join(
        f"<span>급락5%({ENTRY_LABEL.get(mode,mode)}): 후보 {stats.get('candidate_days','-')}일 → 실신호 {stats.get('real_signals','-')}건</span>"
        for mode, stats in am_drop.items()
    )

    header_html = f"""
<div class="wrap">
<header class="report-header">
  <div class="eyebrow">BNF 매매법 백테스트 v6 · 사장님 규칙 + 이전 비교</div>
  <h1>신호 방식·매수 시점·손절 방식 종합 비교 · {m['signal_start']} ~ {m['signal_end']}</h1>
  <div class="subtitle">코스피 시가총액 상위 {m['universe_size']}개 개별 우량주(ETF·ETN·리츠 등 제외)를 대상으로, "급락5%"(변형 규칙)와 "BNF원전"(이격도 -20%+RSI 35 미만+MACD 전환, 최근 {m['params'].get('bnf_indicator_window_days')}거래일 내 완화 적용) 두 신호를 각각 9시30분/마감30분전 매수로, 손절은 이전 저점/-5%/-8% 세 가지로 비교했습니다. 신호일에 코스피 지수도 같이 빠졌는지(시장 전체 패닉 여부)도 태깅했습니다.</div>
  <div class="meta-line">
    <span>실행: {m['run_at'][:16].replace('T',' ')}</span>
    <span>세전/세후(근사, 약 {m['params'].get('round_trip_fee_pct')}%p 차감) 병기</span>
    <span>데이터 조회 실패 {m['failed_count']}종목 제외</span>
    <span>{benchmark_line}</span>
  </div>
  <div class="meta-line">
    {am_candidate_lines}
  </div>
</header>
"""

    top_comparison = render_top_comparison(data["combos"])
    if owner_html:
        top_comparison = (
            owner_html
            + '<section class="card"><h2>참고: 이전 비교(v5)</h2><div class="desc">아래는 사장님 규칙 확정 전에 돌린 신호·매수시점·손절 방식 비교입니다.</div></section>'
            + top_comparison
        )

    combo_sections = ""
    for signal_mode in SIGNAL_MODE_ORDER:
        for entry_mode in ENTRY_MODE_ORDER:
            key = f"{signal_mode}__{entry_mode}"
            combo_sections += render_combo_section(signal_mode, entry_mode, data["combos"][key])

    assumptions_html = f"""
<section class="card">
  <h2>가정과 한계</h2>
  <ul class="assump-list">
    <li><b>"급락5%"</b>는 전일 대비 9시30분/마감30분전 체결가 -5% 이하를 신호로 삼는, 이 프로젝트에서 만든 변형 규칙입니다. <b>"BNF원전"</b>은 BNF 리서치에서 확인한 원조 방식에 가장 가까운 조합(최근 {m['params'].get('bnf_indicator_window_days')}거래일 내 이격도 {m['params'].get('bnf_disparity_max')} 이하 + RSI {m['params'].get('bnf_rsi_max')} 미만 + 당일 MACD 히스토그램 음→양 전환)이며, 신호는 종가로만 확정되므로 매수는 신호 다음 거래일에 이뤄집니다. (RSI 임계값 30→35, "당일 일치"→"최근 며칠 내 일치"로 완화 — 원안 그대로는 200종목·8개월간 신호가 2건뿐이라 통계적으로 무의미했음. 이격도 -20% 기준 자체는 유지.)</li>
    <li><b>손절선 3종</b>(이전 {m['params'].get('stop_loss_lookback_days')}거래일 저점 / 매수가 대비 -5% / -8%)은 같은 신호에 대해 추가 조회 없이 동시에 계산한 비교이며, 상단 비교표와 상세 통계는 "이전 저점" 기준입니다.</li>
    <li><b>시장 전체 패닉 태깅</b>은 신호일에 코스피 지수 자체가 {m['params'].get('market_panic_kospi_drop_pct')}% 이하로 빠졌는지로 판정했습니다. BNF 원전은 "업종 전체가 같이 빠졌는지"를 봤다고 알려져 있는데, 종목별 업종지수를 전부 추가로 조회해야 해서 이번엔 코스피 지수로 근사했습니다 — 더 정밀하게 하려면 업종지수 연동이 필요합니다.</li>
    <li><b>세후 수익률</b>은 매도세+수수료 왕복 약 {m['params'].get('round_trip_fee_pct')}%p를 단순 차감한 근사치입니다. 실제 세율·수수료는 증권사·시점마다 다릅니다.</li>
    <li>대상 종목({m['universe_size']}개)은 <b>현재 시점 코스피 시가총액 상위 스냅샷</b>(개별주만, ETF/ETN/리츠 제외)을 분석 기간 전체에 소급 적용한 것입니다. <b>코스닥은 아직 포함하지 않았습니다</b> — 코스닥 종목마스터 파일의 필드 구조가 코스피와 달라(정상 거래 종목이 거래정지로 잘못 인식되는 것을 확인) 정확한 스펙을 확인하기 전까지는 빼두었습니다.</li>
    <li>매수 시점은 BNF가 "장 시작 후 30분, 마감 전 30분을 집중 관찰"했다는 리서치 내용을 반영해 <b>9시30분</b>(장시작 30분후)과 <b>15시</b>(마감 30분전)로 비교했습니다.</li>
    <li>종목당 동시 1포지션만 허용하며, 각 (신호방식×매수시점) 조합은 서로 독립적으로 포지션을 관리합니다.</li>
    <li>일봉 조회에 실패한 {m['failed_count']}개 종목은 분석에서 제외되었습니다.</li>
  </ul>
</section>
"""

    body = (
        header_html
        + top_comparison
        + combo_sections
        + assumptions_html
        + '<footer>BNF 매매법 백테스트 · 투자 조언이 아니며 과거 데이터 기반 참고 자료입니다.</footer>'
        + "</div>"
    )

    font_link = (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        "family=IBM+Plex+Sans+KR:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600"
        '&display=swap">'
    )

    return f"<title>{title}</title>\n{font_link}\n<style>{STYLE}</style>\n{body}"


def main() -> None:
    trades, meta = load_data()
    data = build_report_data(trades, meta)
    owner = load_owner_data()
    owner_html = render_owner_section(*owner) if owner else ""
    html = render_html(data, owner_html)
    REPORT_PATH.write_text(html, encoding="utf-8")
    print(f"리포트 생성 완료: {REPORT_PATH}")
    for signal_mode in SIGNAL_MODE_ORDER:
        for entry_mode in ENTRY_MODE_ORDER:
            key = f"{signal_mode}__{entry_mode}"
            s = data["combos"][key]["summary"]
            print(
                f"[{signal_mode}/{entry_mode}] 총 {s['total_signals']}건 "
                f"(완료 {s['total_closed']}, 진행중 {s['total_open']}), "
                f"승률 {s['win_rate_pct']}%, 평균수익률 {s['avg_return_pct']}%"
            )


if __name__ == "__main__":
    main()
