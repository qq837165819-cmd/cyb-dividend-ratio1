# -*- coding: utf-8 -*-
"""Build five-year category-level ETF holdings-share and cumulative-flow history."""
import json, time, requests
from datetime import date
from bisect import bisect_right
import akshare as ak

SOURCE = "https://raw.githubusercontent.com/wwww1998/national-team-etf-flow/main/_etf_shares_cache.json"
TARGET = "data/national-team-category-history.json"
CAT_MAP = {
 "510300":"沪深300","510310":"沪深300","510330":"沪深300","159919":"沪深300",
 "510050":"上证50","510100":"上证50",
 "510500":"中证500","512500":"中证500","159922":"中证500",
 "512100":"中证1000","159845":"中证1000","560010":"中证1000","159629":"中证1000",
 "159915":"创业板","159952":"创业板","159977":"创业板","159949":"创业板50",
 "588080":"科创板50","588050":"科创板50","588000":"科创板50",
 "563300":"中证A500","159338":"中证A500","159352":"中证A500","159361":"中证A500",
 "510180":"上证180","510230":"上证180金融","159901":"深证100",
 "515800":"中证800","560050":"MSCI中国A50",
}
CATEGORIES = list(dict.fromkeys(CAT_MAP.values()))
def get_nav(code):
    for attempt in range(3):
        try:
            df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            out = {}
            for _, row in df.iterrows():
                d = str(row.get("净值日期",""))[:10].replace("-","")
                try: v = float(row.get("单位净值"))
                except Exception: continue
                if len(d)==8 and d.isdigit() and v>0: out[d]=v
            if out: return out
        except Exception as e:
            if attempt==2: print("NAV failed",code,str(e)[:120],flush=True)
            time.sleep(2*(attempt+1))
    return {}

def main():
    r=requests.get(SOURCE,timeout=90,headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    cache=r.json()
    shares=cache.get("shares",{})
    all_dates=sorted(d for d,m in shares.items() if m)
    if not all_dates: raise RuntimeError("No share history found")
    start=(date.today().replace(year=date.today().year-5)).strftime("%Y%m%d")
    dates=[d for d in all_dates if d>=start]
    codes=sorted(CAT_MAP)
    navs={}
    for i,code in enumerate(codes,1):
        navs[code]=get_nav(code)
        print(f"NAV {i}/{len(codes)} {code}: {len(navs[code])}",flush=True)
    nav_dates={c:sorted(navs[c]) for c in codes}
    nav_index={c:0 for c in codes}
    last_nav={}
    last_shares={}
    prev_shares={}
    cat_mv={cat:[] for cat in CATEGORIES}
    cat_flow={cat:[] for cat in CATEGORIES}
    total_mv=[]
    cumulative={cat:0.0 for cat in CATEGORIES}
    cat_cum={cat:[] for cat in CATEGORIES}
    for d in dates:
        day=shares.get(d,{})
        flows={cat:0.0 for cat in CATEGORIES}
        mv={cat:0.0 for cat in CATEGORIES}
        for code in codes:
            if code in day:
                current=float(day[code])
                old=prev_shares.get(code)
                nd=nav_dates[code]
                # Use the latest published NAV on or before this share date.
                j=bisect_right(nd,d)-1
                if j>=0: last_nav[code]=navs[code][nd[j]]
                nav=last_nav.get(code)
                if nav is not None:
                    last_shares[code]=current
                    mv[CAT_MAP[code]] += current*nav/1e8
                    if old is not None:
                        flows[CAT_MAP[code]] += (current-old)*nav/1e8
                prev_shares[code]=current
            elif code in last_shares and code in last_nav:
                mv[CAT_MAP[code]] += last_shares[code]*last_nav[code]/1e8
        total=sum(mv.values())
        total_mv.append(round(total,3))
        for cat in CATEGORIES:
            cat_mv[cat].append(round(mv[cat]/total*100,5) if total>0 else None)
            # No artificial zero before the category's first observable ETF data.
            has_history=any(c for c in codes if CAT_MAP[c]==cat and c in prev_shares)
            if has_history:
                cumulative[cat]+=flows[cat]
                cat_flow[cat].append(round(flows[cat],3))
                cat_cum[cat].append(round(cumulative[cat],3))
            else:
                cat_flow[cat].append(None)
                cat_cum[cat].append(None)
    payload={
      "source":"SSE/SZSE ETF daily share snapshots from upstream national-team-etf-flow cache; NAV via Eastmoney/Tiantian Fund AKShare",
      "method":"Daily flow=(share change since previous observed share snapshot)*latest published unit NAV; cumulative sum by index category. Category share=category ETF market value / total tracked ETF market value.",
      "latest_date":dates[-1],
      "start_date":dates[0],
      "etf_count":len(codes),
      "categories":[{"name":cat,"share":cat_mv[cat],"flow":cat_flow[cat],"cumulative":cat_cum[cat]} for cat in CATEGORIES],
      "dates":[f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates],
      "total_market_value":total_mv,
      "note":"ETF share-change estimates are flow proxies, not verified Central Huijin holdings. Newly added or incompletely covered ETFs only contribute from their first available historical share record; missing earlier history is not treated as zero."
    }
    with open(TARGET,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,separators=(",",":"))
    print("Wrote",TARGET,"dates",len(dates),"range",dates[0],dates[-1],flush=True)
if __name__=="__main__": main()
