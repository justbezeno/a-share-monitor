"""
ERP 口径回测：比较哪种 ERP 对沪深300未来收益最有预测力。

对比的 ERP 版本：
  A. 沪深300 ERP · 2%–7% 线性映射（现行打分方式）
  B. 沪深300 ERP · 滚动5年分位
  C. 全A中位数PE ERP · 2%–7% 线性映射
  D. 全A中位数PE ERP · 滚动5年分位
  E. 全A等权PE ERP · 2%–7% 线性映射
  F. 全A等权PE ERP · 滚动5年分位

ERP = 1/PE(TTM) − 中国10Y国债收益率
目标：沪深300 未来 6 个月 / 12 个月收益，月末采样。
主样本期与原回测一致：2016-01 起；另报 2011 起的扩展样本和分段结果。

用法（在项目 venv 里）：
    python backtest_erp.py              # 联网抓数据并缓存到 ./erp_bt_cache/
    python backtest_erp.py --offline    # 只用缓存
输出：终端报告 + erp_bt_result.md + erp_bt_monthly.csv
"""
import argparse, os, sys
import numpy as np
import pandas as pd

CACHE = "erp_bt_cache"
MAIN_START = "2016-01-01"
EXT_START = "2011-01-01"
ROLL_M = 60                     # 滚动分位窗口：60 个月 = 5 年
LO, HI = 2.0, 7.0               # 现行线性映射区间（%）


# ───────────────────────── 数据 ─────────────────────────
def _pick(df, *keys, exclude=()):
    for c in df.columns:
        s = str(c)
        if all(k in s for k in keys) and not any(x in s for x in exclude):
            return c
    raise KeyError(f"找不到包含 {keys} 的列，实际列：{list(df.columns)}")


def _cached(name, fn, offline):
    path = os.path.join(CACHE, name + ".csv")
    if os.path.exists(path) and offline:
        return pd.read_csv(path, parse_dates=["date"])
    if offline:
        sys.exit(f"缺少缓存 {path}，请先联网运行一次")
    df = fn()
    os.makedirs(CACHE, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def load_data(offline=False):

    def pe300():
        import akshare as ak
        raw = ak.stock_index_pe_lg(symbol="沪深300")
        d = _pick(raw, "日期")
        c = _pick(raw, "滚动市盈率", exclude=("等权", "中位"))
        return pd.DataFrame({"date": pd.to_datetime(raw[d]), "pe300": pd.to_numeric(raw[c], errors="coerce")})

    def pe_all():
        import akshare as ak
        raw = ak.stock_a_ttm_lyr()
        return pd.DataFrame({
            "date": pd.to_datetime(raw["date"]),
            "pe_all_mid": pd.to_numeric(raw["middlePETTM"], errors="coerce"),
            "pe_all_avg": pd.to_numeric(raw["averagePETTM"], errors="coerce"),
        })

    def bond():
        import akshare as ak
        raw = ak.bond_zh_us_rate(start_date="20050101")
        d = _pick(raw, "日期")
        c = _pick(raw, "中国", "10年", exclude=("-",))
        return pd.DataFrame({"date": pd.to_datetime(raw[d]), "y10": pd.to_numeric(raw[c], errors="coerce")})

    def hs300():
        import akshare as ak
        raw = ak.stock_zh_index_daily(symbol="sh000300")
        return pd.DataFrame({"date": pd.to_datetime(raw["date"]), "close": pd.to_numeric(raw["close"], errors="coerce")})

    frames = {
        "pe300": _cached("pe300", pe300, offline),
        "pe_all": _cached("pe_all", pe_all, offline),
        "bond": _cached("bond", bond, offline),
        "hs300": _cached("hs300", hs300, offline),
    }
    for k, v in frames.items():
        v = v.dropna()
        print(f"  {k:7s} {v['date'].min().date()} → {v['date'].max().date()}  {len(v)} 行")
    return frames


def to_monthly(frames):
    """各序列取月末最后一个有效值后合并。"""
    out = None
    for k, df in frames.items():
        x = df.dropna().set_index("date").sort_index()
        m = x.groupby(x.index.to_period("M")).last()   # 兼容新旧 pandas
        out = m if out is None else out.join(m, how="outer")
    out = out.sort_index().ffill(limit=1)
    out.index = out.index.to_timestamp(how="end").normalize()   # 国债偶有月末缺值，只允许前填 1 个月
    return out


# ───────────────────────── 因子 ─────────────────────────
def build(m):
    m = m.copy()
    for tag, col in [("300", "pe300"), ("allmid", "pe_all_mid"), ("allavg", "pe_all_avg")]:
        erp = 100.0 / m[col] - m["y10"]
        m[f"erp_{tag}"] = erp
        m[f"lin_{tag}"] = ((erp - LO) / (HI - LO) * 100).clip(0, 100)
        # 滚动 5 年分位：当月值在过去 60 个月（含当月）中的百分位，只用当时可得的数据
        m[f"pct_{tag}"] = erp.rolling(ROLL_M, min_periods=ROLL_M).apply(
            lambda x: (x[:-1] < x[-1]).mean() * 100 + (x[:-1] == x[-1]).mean() * 50, raw=True)
    for h in (6, 12):
        m[f"fwd{h}"] = (m["close"].shift(-h) / m["close"] - 1) * 100
    return m


VARIANTS = [
    ("A 沪深300·线性2–7%", "lin_300"),
    ("B 沪深300·5年分位", "pct_300"),
    ("C 全A中位数·线性", "lin_allmid"),
    ("D 全A中位数·5年分位", "pct_allmid"),
    ("E 全A等权·线性", "lin_allavg"),
    ("F 全A等权·5年分位", "pct_allavg"),
]


# ───────────────────────── 评估 ─────────────────────────
def ic(x, y):
    d = pd.concat([x, y], axis=1).dropna()
    if len(d) < 12:
        return np.nan, 0
    return d.iloc[:, 0].rank().corr(d.iloc[:, 1].rank()), len(d)


def t_eff(r, n, h):
    """重叠收益的粗略 t 值：有效样本数 ≈ n / h。"""
    ne = max(n / h, 3)
    return r * np.sqrt((ne - 2) / max(1 - r * r, 1e-9))


def evaluate(m, start):
    s = m[m.index >= start]
    rows = []
    for name, col in VARIANTS:
        r = {"版本": name}
        for h in (6, 12):
            v, n = ic(s[col], s[f"fwd{h}"])
            r[f"IC{h}m"] = v
            r[f"t{h}(有效N)"] = t_eff(v, n, h) if n else np.nan
            r["N"] = n
        # 12m 分组：最便宜 1/5 vs 最贵 1/5 的平均未来收益
        d = s[[col, "fwd12"]].dropna()
        if len(d) >= 25:
            q = pd.qcut(d[col].rank(method="first"), 5, labels=False)
            r["便宜组12m%"] = d["fwd12"][q == 4].mean()
            r["贵组12m%"] = d["fwd12"][q == 0].mean()
            r["多空差pp"] = r["便宜组12m%"] - r["贵组12m%"]
        # 滚动36个月 IC 为正的比例：看稳定性
        roll = [ic(s[col].iloc[i - 36:i], s["fwd12"].iloc[i - 36:i])[0]
                for i in range(36, len(s) + 1)]
        roll = [x for x in roll if not np.isnan(x)]
        r["滚动IC>0占比"] = np.mean([x > 0 for x in roll]) * 100 if roll else np.nan
        # 线性映射的饱和度：多少月份被压到 0 或 100（区间外信息丢失）
        if col.startswith("lin_"):
            x = s[col].dropna()
            r["触顶/触底%"] = ((x >= 100) | (x <= 0)).mean() * 100
        rows.append(r)
    return pd.DataFrame(rows)


def subperiods(m):
    periods = [("2011–2015", "2011-01-01", "2015-12-31"),
               ("2016–2020", "2016-01-01", "2020-12-31"),
               ("2021–今", "2021-01-01", "2100-01-01")]
    rows = []
    for name, col in VARIANTS:
        r = {"版本": name}
        for pname, a, b in periods:
            s = m[(m.index >= a) & (m.index <= b)]
            r[pname] = ic(s[col], s["fwd12"])[0]
        rows.append(r)
    return pd.DataFrame(rows)


def crowding_distribution(m, start):
    """各口径下 (100−ERP得分) 的分布：决定『贵』的判定有多频繁。
    换手率部分无历史数据时，仅看 ERP 这一半的贡献。"""
    s = m[m.index >= start]
    rows = []
    for name, col in VARIANTS:
        x = (100 - s[col]).dropna() * 0.55
        rows.append({"版本": name, "ERP贡献均值": x.mean(),
                     "ERP贡献≥44占比%": (x >= 44).mean() * 100,   # 单靠ERP即可把拥挤分推过80需≥44（=80×0.55）
                     "当前得分": s[col].dropna().iloc[-1] if s[col].notna().any() else np.nan})
    return pd.DataFrame(rows)


def fmt(df):
    return df.to_markdown(index=False, floatfmt=".2f")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    print("加载数据 ...")
    m = build(to_monthly(load_data(args.offline)))
    last = m.dropna(subset=["erp_300"]).iloc[-1]

    parts = [
        "# ERP 口径回测结果\n",
        f"数据截至 {last.name.date()}。当前：沪深300 ERP {last['erp_300']:.2f}%，"
        f"全A中位数 ERP {last['erp_allmid']:.2f}%，全A等权 ERP {last['erp_allavg']:.2f}%，10Y {last['y10']:.2f}%。\n",
        "IC 为 Spearman 秩相关，正值 = 得分越高（越便宜）未来收益越高。"
        "月度样本、收益窗口重叠，t 值按有效样本 N/h 粗略折算，|t|>2 可视为较可靠。\n",
        f"## 主样本（{MAIN_START[:7]} 起，与原回测一致）\n", fmt(evaluate(m, MAIN_START)),
        f"\n## 扩展样本（{EXT_START[:7]} 起）\n", fmt(evaluate(m, EXT_START)),
        "\n## 分段 12m IC（看是否只在某一段有效）\n", fmt(subperiods(m)),
        "\n## 对拥挤分的影响（主样本）\n", fmt(crowding_distribution(m, MAIN_START)),
        "\n### 怎么读\n"
        "- 先看主样本 IC12m 和 t 值，再看分段是否每段都为正、滚动 IC>0 占比是否够高。\n"
        "- 线性映射版本若『触顶/触底%』很高，说明 2%–7% 区间和该口径不匹配，大量月份信息被截断。\n"
        "- 『ERP贡献≥44占比』反映单靠估值就判定为极度拥挤的频率；换口径时这个数字变化大，说明拥挤分阈值需要重新校准。\n",
    ]
    report = "\n".join(parts)
    print("\n" + report)
    with open("erp_bt_result.md", "w", encoding="utf-8") as f:
        f.write(report)
    m.to_csv("erp_bt_monthly.csv", encoding="utf-8-sig")
    print("\n已写出 erp_bt_result.md / erp_bt_monthly.csv")


if __name__ == "__main__":
    main()
