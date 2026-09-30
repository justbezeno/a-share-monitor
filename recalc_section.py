#!/usr/bin/env python3
"""recalc_section.py — 只重算 arisk_data.json 里指定的段，不跑全量（全量约 35 分钟）。

用法：
    ./venv/bin/python recalc_section.py gold us10y
    ./venv/bin/python recalc_section.py mktcap_gdp

用途：改过 update_arisk_data.py 某段的口径或字段后，立刻让新字段落地，不必等全量更新。

· 逻辑与全量更新**完全一致** —— 直接 import update_arisk_data 里对应的 fetch_*，不另写一份，
  所以不会出现「补算结果与全量结果不一致」。
· 某段重算失败（value 为空）时跳过该段；全部失败则整体不写回，不会写坏文件。
· 不改 generated_at：其余段仍是上一次全量更新的数据，把生成时间改成本刻属于虚报。
  前端缓存靠 CKEY/SCHEMA 版本戳失效，不依赖这个时间戳。
· mktcap_gdp 依赖 JSON 里已有的 turnover 序列（日线分子），不能用于首次生成。
"""
import importlib.util, json, os, sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'arisk_data.json')

spec = importlib.util.spec_from_file_location('uad', os.path.join(HERE, 'update_arisk_data.py'))
uad = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uad)

with open(OUT, encoding='utf-8') as f:
    data = json.load(f)

BUILDERS = {
    'mktcap_gdp': lambda d: uad.fetch_mktcap_gdp(d.get('turnover')),
    'gold':       lambda d: uad.fetch_gold(),
    'us10y':      lambda d: uad.fetch_us10y(),
    'sector_live':      lambda d: uad.fetch_sector_live(),
    'turnover':         lambda d: uad.fetch_turnover(),
    'vol_7d':           lambda d: uad.fetch_vol_7d(d.get('turnover')),
    'bond10y':          lambda d: uad.fetch_bond10y(),
    'pe_300':           lambda d: uad.fetch_pe_300(),
    'erp_history':      lambda d: uad.fetch_erp_history(),
    'hv30':             lambda d: uad.fetch_hv30(),
    'margin':           lambda d: uad.fetch_margin(),
    'index_trend':      lambda d: uad.fetch_index_trend(),
    'us_index_trend':   lambda d: uad.fetch_us_index_trend(),
    'below_net_asset':  lambda d: uad.fetch_below_net_asset(),
    'limit_7d':         lambda d: uad.fetch_limit_7d(),
    'credit_yoy':       lambda d: uad.fetch_credit_yoy(),
    'fund_issuance':    lambda d: uad.fetch_fund_issuance(),
    'etf_categories':   lambda d: uad.fetch_etf_categories(),
}

keys = sys.argv[1:]
if not keys:
    print(__doc__.strip())
    sys.exit(2)
bad = [k for k in keys if k not in BUILDERS]
if bad:
    print(f"✗ 未知段 {bad}；可用：{list(BUILDERS)}")
    sys.exit(2)

changed = []
for k in keys:
    new = BUILDERS[k](data)
    if not isinstance(new, dict) or new.get('value') is None:
        print(f"✗ {k} 重算失败（value 为空），跳过")
        continue
    data[k] = new
    changed.append(k)

if not changed:
    print("✗ 没有任何段重算成功，未写回")
    sys.exit(1)

tmp = OUT + '.tmp'
with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
os.replace(tmp, OUT)
print(f"✓ 已写回 {OUT}：{changed}（{datetime.now().strftime('%H:%M:%S')}）")
