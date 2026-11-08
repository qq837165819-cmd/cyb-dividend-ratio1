# -*- coding: utf-8 -*-
"""Backtest first-board next-day premium after extreme limit-up industry concentration."""
import json, os, time, traceback
from collections import Counter, defaultdict
from datetime import datetime
import pandas as pd
import akshare as ak

START = "20190101"
END = datetime.now().strftime("%Y%m%d")
OUT = "data/first-board-premium-backtest.json"
UA = {"User-Agent": "Mozilla/5.0"}

def norm_code(v):
    s = str(v).strip()
    return s.zfill(6)

def fetch_pool(day):
    # Eastmoney limit-up pool includes the stock's industry and consecutive-board count.
    for attempt in range(4):
        try:
            df = ak.stock_zt_pool_em(date=day)
            if df is None or df.empty:
                return []
            cols = {str(c).strip(): c for c in df.columns}
            code_col = cols.get("代码")
            industry_col = cols.get("所属行业")
            board_col = cols.get("连板数")
            if not code_col or not industry_col:
                raise RuntimeError("涨停池字段缺少代码/所属行业: " + str(list(df.columns)))
            rows = []
            for _, r in df.iterrows():
                code = norm_code(r[code_col])
                industry = str(r[industry_col]).strip()
                raw_board = str(r[board_col]).strip() if board_col else ""
                try:
                    n = int(float(raw_board))
                except Exception:
                    n = 1 if raw_board in ("首板", "1板", "1") else 0
                rows.append({"code": code, "industry": industry or "未知行业", "is_first": n == 1})
            return rows
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    return []

def fetch_stock_daily(code):
    for attempt in range(4):
        try:
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=START, end_date=END, adjust="")
            if df is None or df.empty:
                return {}
            out = {}
            for _, r in df.iterrows():
                d = str(r.get("日期", "")).replace("-", "")[:8]
                try:
                    op, cl = float(r["开盘"]), float(r["收盘"])
                except Exception:
                    continue
                if len(d) == 8 and op > 0 and cl > 0:
                    out[d] = {"open": op, "close": cl}
            return out
        except Exception:
            if attempt == 3:
                print("stock history failed", code, flush=True)
                return {}
            time.sleep(1.5 * (attempt + 1))
    return {}

def main():
    os.makedirs("data", exist_ok=True)
    td = ak.tool_trade_date_hist_sina()
    dates = sorted(str(x).replace("-", "")[:8] for x in td["trade_date"].astype(str)
                   if START <= str(x).replace("-", "")[:8] <= END)
    if not dates:
        raise RuntimeError("未取得交易日历")
    pools = {}
    stock_codes = set()
    hhi = {}
    first_by_day = {}
    failures = []
    for i, day in enumerate(dates, 1):
        try:
            rows = fetch_pool(day)
            pools[day] = rows
            counts = Counter(x["industry"] for x in rows if x["industry"] != "未知行业")
            total = sum(counts.values())
            hhi[day] = sum((n / total) ** 2 for n in counts.values()) if total else None
            first = [x["code"] for x in rows if x["is_first"]]
            first_by_day[day] = first
            stock_codes.update(x["code"] for x in rows)
        except Exception as e:
            failures.append(day)
            print("pool failed", day, str(e)[:160], flush=True)
        if i % 50 == 0:
            print(f"limit-up pools {i}/{len(dates)}; stocks={len(stock_codes)}; failures={len(failures)}", flush=True)
        time.sleep(0.12)
    nonempty_days = sum(1 for rows in pools.values() if rows)
    total_pool_rows = sum(len(rows) for rows in pools.values())
    if len(pools) < 500 or nonempty_days < 500 or total_pool_rows < 10000:
        raise RuntimeError(f"涨停池历史覆盖不足：日历日期{len(pools)}天，非空涨停池{nonempty_days}天，股票记录{total_pool_rows}条；不能生成可信回测")
    print("Downloading daily bars for", len(stock_codes), "stocks", flush=True)
    bars = {}
    for i, code in enumerate(sorted(stock_codes), 1):
        bars[code] = fetch_stock_daily(code)
        if i % 100 == 0:
            print(f"stock bars {i}/{len(stock_codes)}", flush=True)
        time.sleep(0.12)

    # Build each trading day's first-board next-session opening/closing premium.
    session = {}
    for i, day in enumerate(dates[:-1]):
        next_day = dates[i + 1]
        vals = []
        for code in first_by_day.get(day, []):
            b = bars.get(code, {})
            prev = b.get(day)
            nxt = b.get(next_day)
            if prev and nxt and prev["close"] > 0:
                vals.append({"open": (nxt["open"] / prev["close"] - 1) * 100,
                             "close": (nxt["close"] / prev["close"] - 1) * 100})
        if vals:
            session[day] = {
                "count": len(vals),
                "open": round(sum(x["open"] for x in vals) / len(vals), 4),
                "close": round(sum(x["close"] for x in vals) / len(vals), 4)
            }

    # Trigger when HHI is in the top decile of the preceding 252 available sessions.
    # For each trigger, sample first-board cohorts on T+1/T+2/T+3 and their next-day premiums.
    event_samples = {1: [], 2: [], 3: []}
    normal_samples = []
    trigger_dates = []
    for i, day in enumerate(dates):
        v = hhi.get(day)
        hist = [hhi.get(dates[j]) for j in range(max(0, i - 252), i)]
        hist = [x for x in hist if x is not None]
        if v is None or len(hist) < 120:
            continue
        threshold = sorted(hist)[max(0, int(len(hist) * 0.90) - 1)]
        if v >= threshold:
            trigger_dates.append(day)
            for lag in (1, 2, 3):
                if i + lag < len(dates):
                    cohort_day = dates[i + lag]
                    if cohort_day in session:
                        event_samples[lag].append({"date": cohort_day, **session[cohort_day]})
        else:
            # Non-extreme sessions provide a baseline using the same first-board metric.
            if day in session:
                normal_samples.append({"date": day, **session[day]})

    def summarize(items):
        if not items:
            return {"days": 0, "stocks": 0, "open": None, "close": None}
        # Equal weight each session, not each stock, so a few high-volume days do not dominate.
        return {
            "days": len(items),
            "stocks": sum(x["count"] for x in items),
            "open": round(sum(x["open"] for x in items) / len(items), 4),
            "close": round(sum(x["close"] for x in items) / len(items), 4)
        }

    result = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start_date": dates[0], "end_date": dates[-1],
        "pool_days": len(pools), "pool_failures": len(failures),
        "stock_count": len(stock_codes),
        "method": "行业集中度=当日涨停股所属行业数量份额HHI（各行业占比平方和）；触发阈值=此前252个有效交易日HHI的90分位。T+1/T+2/T+3分别取触发日之后第1/2/3个交易日的首板股票，计算这些首板股票在其下一交易日的开盘/收盘相对首板日收盘涨幅；先算每个交易日均值，再对交易日等权平均。普通日对照为未触发极端集中条件的交易日。",
        "definition": "这是行业涨停集中度的代理口径；不代表行业资金流向。首板样本要求涨停池连板数=1。",
        "trigger_count": len(trigger_dates),
        "trigger_dates": trigger_dates,
        "lags": [{"lag": lag, "label": f"T+{lag}", **summarize(event_samples[lag])} for lag in (1,2,3)],
        "normal": summarize(normal_samples),
        "failure_dates": failures[:100]
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    print("Wrote", OUT, "triggers", len(trigger_dates), "lags", result["lags"], flush=True)

if __name__ == "__main__":
    main()
