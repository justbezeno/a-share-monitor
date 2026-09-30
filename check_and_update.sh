#!/bin/bash
# check_and_update.sh — 每小时被 launchd 调用，判断 arisk_data.json 是否需要重新抓取
# （macOS 版；路径改为脚本所在目录，python 用 venv）
#
# 判据是「数据里的日期」而不是「文件修改时间」。检查所有日频字段的 date / stale：
#   成交额/换手率(turnover, vol_7d)、ETF(etf_categories)、行业(sector_live)、
#   HV30(hv30)、PE(pe_300)、10Y国债(bond10y)、破净率(below_net_asset)、两融(margin)、
#   主要指数走势(index_trend)、黄金(gold)
#   其中两融为 T+1 发布，只要求到上一个交易日
#   1. JSON 不存在                                   → 更新
#   2. 任一日频字段 date 落后于"应有的最新交易日"      → 更新
#   3. 任一日频字段 stale=true（上次抓取失败复用旧值） → 更新
#   4. 全部到位                                       → 跳过（收盘数据不会再变）
# 月频字段（社融 credit_yoy、基金新发 fund_issuance）不参与判断。
# 低频/跨市场字段走 SLOW_FIELDS 的"距今超 N 天"规则（见下），不参与日频比较。
#
# "应有的最新交易日" = 最近一个工作日；当天 18:00 之前算前一个工作日。
# 非交易日判定只看交易所成交额/换手率（turnover）：若更新后它仍落后且 $EXP 已过去一天仍抓不到，
# 判定为非交易日记入 .arisk_nontrading_dates，之后自动跳过；若 $EXP 就是今天则不拉黑，留给下小时重试。
# 其它字段（如 PE、ETF 份额）发布较晚导致的落后只触发重试，不会把交易日误判为非交易日。

DIR="$(cd "$(dirname "$0")" && pwd)"
JSON="$DIR/arisk_data.json"
LOG="$DIR/arisk_update.log"
NONTRADING="$DIR/.arisk_nontrading_dates"
UPDATER="$DIR/run_arisk_update.sh"
PY="$DIR/venv/bin/python"

DAILY_FIELDS="turnover vol_7d etf_categories sector_live hv30 pe_300 bond10y below_net_asset margin index_trend gold"
# T+1 发布的字段：两融（交易所次日早上发布），只要求到"上一个交易日"。
# 注：ETF 份额是当天发布、只是时间不固定（历史上 16:26~22:11 都有），不属于 T+1。
T1_FIELDS="margin"
# 低频/跨市场字段：不能按"date 等于最近交易日"判断（否则永远判落后 → 每小时空跑一次全量更新），
# 改用"数据日期距今超过 N 天"的容忍规则。格式 字段:天数。
#   mktcap_gdp    巴菲特指标，沪深市价总值是**月度**数据、次月中旬发布 → 容忍 75 天；
#   us_index_trend 美股指数，美股时区/假日与 A 股不同，最后一根K线常年早 1~3 天 → 容忍 5 天；
#   us10y         美国10年期国债，跟随美国交易日历（感恩节/圣诞等连休），与 A 股不同步 → 容忍 5 天；
#   gold          伦敦金现 XAU/USD（2026-09-30 由上海金切换），跟随国际日历：周一到周五 24 小时
#                 交易、几乎不休市（仅圣诞/元旦），故按普通日频判断即可。
SLOW_FIELDS="mktcap_gdp:75 us_index_trend:5 us10y:5"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# expected_date [back]：应有的最新交易日；back=1 时返回它的上一个交易日
expected_date() {
    "$PY" - "$NONTRADING" "${1:-0}" <<'PY'
import sys, datetime
try:
    skip = {l.strip() for l in open(sys.argv[1]) if l.strip()}
except FileNotFoundError:
    skip = set()
now = datetime.datetime.now()
d = now.date()
if now.hour < 18:                              # 收盘+发布缓冲之前，今天还不该有数据
    d -= datetime.timedelta(days=1)
def last_trading(d):
    for _ in range(30):
        if d.weekday() < 5 and d.isoformat() not in skip:
            return d
        d -= datetime.timedelta(days=1)
    return d
d = last_trading(d)
for _ in range(int(sys.argv[2])):
    d = last_trading(d - datetime.timedelta(days=1))
print(d.isoformat())
PY
}

# 某个字段的数据日期（缺失输出空）
field_date() {
    "$PY" - "$JSON" "$1" <<'PY'
import sys, json
try:
    print((json.load(open(sys.argv[1])).get(sys.argv[2]) or {}).get('date') or '')
except Exception:
    print('')
PY
}

# 列出落后或 stale 的日频字段，每行一个「字段(日期[,stale])」；全部到位则无输出
# $1 = 应有交易日；$2 = 上一个交易日（T+1 字段的要求）
lagging_fields() {
    "$PY" - "$JSON" "$1" "$2" "$T1_FIELDS" "$SLOW_FIELDS" $DAILY_FIELDS <<'PY'
import sys, json, datetime
path, exp, exp_prev = sys.argv[1], sys.argv[2], sys.argv[3]
t1, slow, keys = sys.argv[4].split(), sys.argv[5], sys.argv[6:]
slow_map = {}
for s in slow.split():
    if ':' in s:
        k, n = s.split(':', 1)
        try:
            slow_map[k] = int(n)
        except ValueError:
            pass
try:
    d = json.load(open(path))
except Exception:
    d = {}
today = datetime.date.today()
for k in keys:
    f = d.get(k) or {}
    date, stale = f.get('date') or '', bool(f.get('stale'))
    if k in slow_map:
        # 低频字段：不比较交易日，只看数据日期距今是否超过容忍天数
        aged = True
        if date:
            try:
                dt = datetime.date.fromisoformat(date if len(date) == 10 else date + '-01')
                aged = (today - dt).days > slow_map[k]
            except ValueError:
                aged = True
        if not date or aged or stale:
            print(f"{k}({date or '缺失'}{',stale' if stale else ''})")
        continue
    need = exp_prev if k in t1 else exp
    if not date or date < need or stale:
        print(f"{k}({date or '缺失'}{',stale' if stale else ''})")
PY
}

if [ ! -f "$JSON" ]; then
    log "check: json 不存在 → 更新"
    exec "$UPDATER"
fi

EXP=$(expected_date)
EXP_PREV=$(expected_date 1)
LAG=$(lagging_fields "$EXP" "$EXP_PREV" | tr '\n' ' ')

if [ -z "$LAG" ]; then
    log "check: skip — 日频字段均已覆盖应有交易日 $EXP 且无 stale"
    exit 0
fi

log "check: 应有交易日 $EXP，以下字段落后或 stale：${LAG}→ 更新"
"$UPDATER"
RC=$?

if [ "$RC" -ne 0 ]; then
    log "check: 更新脚本失败 (exit $RC)，下小时重试"
    exit "$RC"
fi

NEW=$(field_date turnover)
TODAY=$(date +%F)
if [ -n "$NEW" ] && [[ "$NEW" < "$EXP" ]]; then
    if [[ "$EXP" < "$TODAY" ]]; then
        grep -qxF "$EXP" "$NONTRADING" 2>/dev/null || echo "$EXP" >> "$NONTRADING"
        log "check: 更新后交易所数据日期仍为 $NEW，且 $EXP 已过去一天仍抓不到，判定为非交易日，已记入 $(basename $NONTRADING)"
    else
        log "check: 更新后交易所数据日期仍为 $NEW，$EXP 就是今天，判定为数据源尚未发布，下小时重试（不拉黑）"
    fi
else
    LAG=$(lagging_fields "$EXP" "$EXP_PREV" | tr '\n' ' ')
    if [ -n "$LAG" ]; then
        log "check: 更新完成，仍落后或 stale：${LAG}（数据源可能尚未发布，下小时重试）"
    else
        log "check: 更新完成，日频字段均已到 $EXP"
    fi
fi
