#!/usr/bin/env python3
"""recalc_section.py — 只重算 arisk_data.json 里指定的段，不跑全量（全量约 35 分钟）。

用法：
    ./venv/bin/python recalc_section.py gold us10y
    ./venv/bin/python recalc_section.py mktcap_gdp

用途：改过 update_arisk_data.py 某段的口径或字段后，立刻让新字段落地，不必等全量更新。

· 逻辑与全量更新**完全一致** —— 直接 import update_arisk_data 里对应的 fetch_*，不另写一份，
  所以不会出现「补算结果与全量结果不一致」。
· 某段重算失败（value 为空）时跳过该段；全部失败则整体不写回，不会写坏文件。
· 写回时**会更新 generated_at / generated_date**（2026-10-01 修正）：
  前端缓存的失效戳**正是 generated_at**，不是 CKEY/SCHEMA —— 见 arisk_monitor_local.html
  的 loadC：`if(expectGen!==undefined&&…&&r.gen!==expectGen)return null`，expectGen 取自
  /prebuilt 返回的 generated_at。CKEY/SCHEMA 只管「缓存对象字段结构」变没变，
  数据内容变了它们是不动的。**所以补算后不更新这个戳，会出现「脚本报成功、页面上还是旧值」**。
  页面本身不引用这个时间戳（全页无 generated_at 引用），它只是数据版本号、不是给用户看的
  时间，更新它不构成虚报；各字段自己仍带真实 date / stale，新鲜度以那里的为准。
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

# ★ 必须更新 generated_at：它是前端缓存的失效戳（见文件头说明）。
#   少了这一步，补算结果写进了 JSON，浏览器却命中旧缓存继续显示旧数据。
_now = datetime.now()
_old_gen = data.get('generated_at')
data['generated_at'] = _now.strftime('%Y-%m-%dT%H:%M:%S')
data['generated_date'] = _now.strftime('%Y-%m-%d')

tmp = OUT + '.tmp'
with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
os.replace(tmp, OUT)
print(f"✓ 已写回 {OUT}：{changed}（{_now.strftime('%H:%M:%S')}）")
print(f"  generated_at: {_old_gen} → {data['generated_at']}（前端据此丢弃旧缓存）")
