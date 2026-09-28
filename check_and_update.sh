#!/bin/bash
# check_and_update.sh — 每小时被 launchd 调用，判断 arisk_data.json 是否需要重新抓取
# （macOS 版；路径改为脚本所在目录，python 用 venv）
#
# 判据是「数据里的日期」而不是「文件修改时间」。检查所有日频字段的 date / stale：
#   成交额/换手率(turnover, vol_7d)、ETF(etf_categories)、行业(sector_live)、
#   HV30(hv30)、PE(pe_300)、两融(margin)
#   1. JSON 不存在                                   → 更新
#   2. 任一日频字段 date 落后于"应有的最新交易日"      → 更新
#   3. 任一日频字段 stale=true（上次抓取失败复用旧值） → 更新
#   4. 全部到位                                       → 跳过（收盘数据不会再变）
# 月频字段（社融 credit_yoy、基金新发 fund_issuance）不参与判断。
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

DAILY_FIELDS="turnover vol_7d etf_categories sector_live hv30 pe_300 margin"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

expected_date() {
    "$PY" - "$NONTRADING" <<'PY'
import sys, datetime
try:
    skip = {l.strip() for l in open(sys.argv[1]) if l.strip()}
except FileNotFoundError:
    skip = set()
now = datetime.datetime.now()
d = now.date()
if now.hour < 18:                              # 收盘+发布缓冲之前，今天还不该有数据
    d -= datetime.timedelta(days=1)
for _ in range(30):
    if d.weekday() < 5 and d.isoformat() not in skip:
        break
    d -= datetime.timedelta(days=1)
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

# 列出落后于 $1 或 stale 的日频字段，每行一个「字段(日期[,stale])」；全部到位则无输出
lagging_fields() {
    "$PY" - "$JSON" "$1" $DAILY_FIELDS <<'PY'
import sys, json
path, exp, keys = sys.argv[1], sys.argv[2], sys.argv[3:]
try:
    d = json.load(open(path))
except Exception:
    d = {}
for k in keys:
    f = d.get(k) or {}
    date, stale = f.get('date') or '', bool(f.get('stale'))
    if not date or date < exp or stale:
        print(f"{k}({date or '缺失'}{',stale' if stale else ''})")
PY
}

if [ ! -f "$JSON" ]; then
    log "check: json 不存在 → 更新"
    exec "$UPDATER"
fi

EXP=$(expected_date)
LAG=$(lagging_fields "$EXP" | tr '\n' ' ')

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
    LAG=$(lagging_fields "$EXP" | tr '\n' ' ')
    if [ -n "$LAG" ]; then
        log "check: 更新完成，仍落后或 stale：${LAG}（数据源可能尚未发布，下小时重试）"
    else
        log "check: 更新完成，日频字段均已到 $EXP"
    fi
fi
