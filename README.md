# A股风险监视器 · A-RISK/MONITOR

一个本地运行的 A 股大盘风险监测看板：ERP 股权风险溢价、沪深300 PE(TTM)、10Y 国债、破净率、两市成交额×换手率、HV30 波动率、信贷脉冲（社融存量同比一阶导）、两融余额+动量、ETF 资金流向、申万行业热力图，以及一个「两层漏斗决策模型」给出综合仓位建议。

数据每交易日收盘后自动抓取（AKShare + 央行官网直连 + 沪深交易所 + 新浪/东财），本地静态页面渲染，**无需任何后端服务器**。

面板上每个数字都带**来源 / 数据日期 / 是否估算**徽标；某项本次抓取失败、复用旧值时，卡片显示「⚠ 数据停留在 {日期}」，不会悄悄展示旧值。

![看板首页](docs/screenshot.png)

> 📊 [查看完整长图（含 ETF 资金流向、行业热力图、决策模型）](docs/screenshot-full.png)

> ⚠️ 本项目仅供研究学习，所有指标不构成投资建议。投资有风险，入市需谨慎。

## 组成

| 文件 | 作用 |
|---|---|
| `arisk_monitor_local.html` | 看板本体（Chart.js 走 CDN，其余内联） |
| `update_arisk_data.py` | 抓全量数据 → 生成 `arisk_data.json`（约 2–3 分钟，每段 180s 超时保护） |
| `proxy.py` | 本地代理(8899)，供浏览器盘中实时抓数 + 妙想API 转发 |
| `check_and_update.sh` | 检查所有日频字段的日期与 stale，任一落后/失败才更新 |
| `run_arisk_update.sh` | 跑一次更新（被 check 调用，或手动） |
| `start.sh` / `stop.sh` | 一键起停（代理 8899 + 静态服务器 8788） |
| `arisk_data.json` | 数据快照（仓库内为种子数据，跑一次更新即刷新） |
| `backtest_erp.py` | ERP 口径回测（结果见 `docs/erp_bt_result.md`） |

## 数据源与字段

`arisk_data.json` 中除 `generated_at` / `generated_date` 外，每个字段统一为：

```json
{"value": ..., "source": "央行", "date": "2026-09-25", "is_estimate": false, "stale": false, "note": "可选口径说明"}
```

`date` 是数据本身的日期（月频为 `YYYY-MM`），不是抓取时间；`stale=true` 表示本次抓取失败、复用了旧值。

| 字段 | 指标 | 数据源（按降级顺序） | 频率 |
|---|---|---|---|
| `pe_300` | 沪深300 PE(TTM) | 乐咕乐股（AKShare `stock_index_pe_lg`） | 日 |
| `bond10y` | 中国 10Y 国债 | 东财（AKShare `bond_zh_us_rate`） | 日 |
| `erp_history` | ERP 近5年真实分位（仅展示） | 乐咕乐股 + 东财，月末点对齐 | 月 |
| `below_net_asset` | 破净率（全部A股） | 乐咕乐股（`stock_a_below_net_asset_statistics`） | 日 |
| `turnover` / `vol_7d` | 全A换手率 / 两市成交额（不含北交所） | 沪深交易所；交易所完全不可得时新浪估算兜底（标估算） | 日 |
| `hv30` | 沪深300 HV30 | 新浪K线 | 日 |
| `margin` | 两融余额 | 上交所；深市按 ×1.85 估算（标估算） | 日 |
| `credit_yoy` | 社融存量同比 | 央行 → 妙想（可选）→ 商务部镜像累计 → M2兜底（信号无效） | 月 |
| `sector_live` | 申万一级 60 日涨跌 / 当日涨跌 `today` | 申万一级指数 | 日 |
| `etf_categories` | ETF 60日份额变化（仅沪市） | 上交所份额 × 东财现价（新浪备用） | 日 |
| `fund_issuance` | 偏股基金新发（股票/混合，剔除偏债） | 东财 | 月 |
| `limit_7d` | 涨跌停家数 | 东财 | 日 |

**口径说明**
- ERP = 1/PE(沪深300 TTM) − r10Y。决策打分用「ERP 位置」= (ERP−2%)/(7%−2%) 线性映射——它**不是历史分位**；横幅上并列的「5年真实分位」仅供对照。`backtest_erp.py` 对比 6 种口径后，沪深300·线性映射的 12 个月 IC 最高（2016 起 0.45，2011 起 0.57），故沿用。
- 换手率同理，决策页的「换手率位置」是 换手率/3.5% 的线性映射。

**字段更名（升级自旧版时注意）**：`m2_monthly → credit_yoy`、`sector_live[].excess → today`、页面 JS `peFullA → pe300`；`/pe` 不再返回 `pb`。

## 快速开始

```bash
git clone <你的仓库地址> arisk
cd arisk

# 1. 建虚拟环境 + 装依赖（需 Python 3.9+）
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# 2.（可选）配妙想 API key；不配也能跑，社融走央行直连（妙想仅作为央行失败时的降级源）
cp .env.example .env
#   然后编辑 .env 填入 MX_APIKEY

# 3. 首次抓数
./venv/bin/python update_arisk_data.py

# 4. 启动并打开看板
bash start.sh
```

看板地址：<http://localhost:8788/arisk_monitor_local.html>

> ⚠️ **必须通过 `start.sh`（本地 http）打开，不能直接双击 HTML**——`file://` 协议下浏览器禁止读取本地 JSON，页面会空白。

停止服务：`bash stop.sh`

## 每日自动更新

数据只在**交易日收盘后（约 18:00 起）**发布，`check_and_update.sh` 会判断当前数据是否已覆盖最新交易日：已覆盖则秒退，落后才抓。

- **macOS（launchd）**：见 `com.arisk.update.plist.example`，把 `__ARISK_DIR__` 换成本目录绝对路径后装入 `~/Library/LaunchAgents/`，每天 16:10–22:10 每小时判断一次。
- **Linux（cron）**：`crontab -e` 添加
  ```
  10 16-22 * * * /bin/bash /path/to/arisk/check_and_update.sh
  ```

手动立即更新：`bash run_arisk_update.sh`

## 已知限制

- **数据源在中国境内**（东财/新浪/央行）。海外服务器直连可能受限或较慢，`proxy.py` 已用 `curl_cffi` 模拟 Chrome TLS 指纹绕过部分反爬；仍不通时需自行加代理。
- `proxy.py` 的语义端点（`/pe` `/bond` `/sectors` `/margin` `/sf` `/fund` `/prebuilt`）都读同一份 `arisk_data.json`，原样返回上述统一结构；面板没有分钟级盘中刷新。
- 妙想 API（`MX_APIKEY`）为可选数据源：配置后作为社融降级链第二级；`proxy.py` 的 `POST /mx` 保留给盘中手动查询。未配置时一切照常，日志提示「未配置，跳过」。
- 两融深市部分目前按上交所 ×1.85 经验比估算，面板标「估算」。
- 旧版的「AI 验证」（浏览器直连 api.anthropic.com）已移除。

## 安全

`.env` 含你的 API key，已被 `.gitignore` 排除。**切勿把真实 `.env` 提交或分享。** 如误提交，请立即在东财后台吊销并更换 key。
