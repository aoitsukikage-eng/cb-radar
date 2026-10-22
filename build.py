"""步驟 3：把四種原始資料串成一張表，算溢價率，存進 SQLite 並輸出 CSV。

用法：python build.py            # 處理今天
      python build.py 2026-10-21 # 處理指定日期
"""
import datetime as dt
import json
import sqlite3
import sys

import pandas as pd

import config as C


def load(day, name):
    p = C.RAW_DIR / day / f"{name}.json"
    return pd.DataFrame(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else pd.DataFrame()


def pick(df, fields):
    """依 config 的欄位對照表取欄位並改名；缺欄位時直接報錯，提醒去改對照表。"""
    missing = [v for v in fields.values() if v not in df.columns]
    if missing:
        raise KeyError(f"找不到欄位 {missing}，實際欄位是 {list(df.columns)}，請改 config.py")
    return df[list(fields.values())].rename(columns={v: k for k, v in fields.items()})


def to_num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", "").str.strip(), errors="coerce")


def adjusted_prices():
    """轉換價調整表：從 MOPS 公告整理，格式 cb_id, conv_price, effective_date。
    MOPS 爬蟲沿用既有監控工具，輸出成 data/conv_price_adjustments.csv 即可。"""
    p = C.DATA_DIR / "conv_price_adjustments.csv"
    if not p.exists():
        return pd.DataFrame(columns=["cb_id", "conv_price_adj"])
    # utf-8-sig 會去掉 Excel／PowerShell 存檔時加在開頭的隱形標記；欄位名稱也去掉前後空白
    adj = pd.read_csv(p, dtype=str, encoding="utf-8-sig", skipinitialspace=True)
    adj.columns = adj.columns.str.strip().str.lstrip("﻿")
    need = {"cb_id", "conv_price", "effective_date"}
    if not need <= set(adj.columns):
        raise KeyError(f"conv_price_adjustments.csv 第一行要是 cb_id,conv_price,effective_date，"
                       f"目前讀到的是 {list(adj.columns)}")
    adj["cb_id"] = adj["cb_id"].str.strip()
    adj = adj.sort_values("effective_date")
    return adj.groupby("cb_id").tail(1).rename(columns={"conv_price": "conv_price_adj"})[["cb_id", "conv_price_adj"]]


def roc(day):
    """2026-10-20 → 1151020（櫃買中心 OpenAPI 用民國日期）"""
    d = dt.date.fromisoformat(day)
    return f"{d.year - 1911}{d:%m%d}"


def check_stock_date(day, raw, label):
    """OpenAPI 只給「最新一天」的股價。如果和可轉債行情日期不同，溢價率會用錯天的股價。"""
    if "Date" in raw.columns and len(raw):
        got = str(raw["Date"].iloc[0]).strip()
        if got not in (roc(day), day.replace("-", "")):
            print(f"⚠ {label} 股價日期是 {got}，不是 {roc(day)}。"
                  f"請改用最新交易日重新執行 fetch.py 和 build.py，否則溢價率會用到不同天的股價。")


def build(day):
    raw_twse, raw_tpex = load(day, "stock_twse"), load(day, "stock_tpex")
    check_stock_date(day, raw_twse, "上市")
    check_stock_date(day, raw_tpex, "上櫃")
    terms = pick(load(day, "cb_terms"), C.TERMS_FIELDS)
    # 私募債沒有在市場交易、代號不是 5 碼數字，先排除
    terms["cb_id"] = terms["cb_id"].astype(str).str.strip()
    terms = terms[terms["cb_id"].str.fullmatch(r"\d{5,6}")].copy()
    quotes = pick(load(day, "cb_quotes"), C.QUOTE_FIELDS).rename(columns={"close": "cb_close"})
    stocks = pd.concat([
        pick(raw_twse, C.TWSE_STOCK_FIELDS),
        pick(raw_tpex, C.TPEX_STOCK_FIELDS),
    ]).rename(columns={"close": "stock_close"})

    for df, col in [(terms, "cb_id"), (quotes, "cb_id"), (stocks, "stock_id")]:
        df[col] = df[col].astype(str).str.strip()
    terms["stock_id"] = terms["cb_id"].str[:4]

    t = (terms.merge(quotes, on="cb_id", how="left")
              .merge(stocks, on="stock_id", how="left")
              .merge(adjusted_prices(), on="cb_id", how="left"))

    for col in ["conv_price", "conv_price_adj", "cb_close", "stock_close", "put_price"]:
        if col in t:
            t[col] = to_num(t[col])
    # 有調整過就用最新轉換價，否則用發行時轉換價
    t["conv_price_now"] = t["conv_price_adj"].fillna(t["conv_price"])
    t["conv_value"] = 100 / t["conv_price_now"] * t["stock_close"]
    t["premium"] = t["cb_close"] / t["conv_value"] - 1
    t["date"] = day

    # 提醒欄位：哪些溢價率不能直接拿來判斷套利
    t["is_exchangeable"] = t["cb_name"].astype(str).str.contains("E")  # 交換債，換的是別家股票
    t["conv_not_started"] = pd.to_numeric(t["conv_start"], errors="coerce") > int(day.replace("-", ""))
    t["traded_today"] = t["cb_close"].notna()
    t.loc[t["is_exchangeable"], "premium"] = float("nan")

    # 三大法人：每檔股票最近 5 個交易日的合計買賣超（股數 → 張）
    inst = load(day, "institutional")
    if not inst.empty:
        inst["stock_id"] = inst["stock_id"].astype(str).str.strip()
        if "net" in inst:  # 官方日報：已經是三大法人合計買賣超（股）
            inst["net"] = to_num(inst["net"]) / 1000
        else:              # 舊的 FinMind 格式：每個法人一列，有買進、賣出
            inst["net"] = (to_num(inst["buy"]) - to_num(inst["sell"])) / 1000
        daily = inst.groupby(["stock_id", "date"])["net"].sum().reset_index().sort_values("date")
        last5 = daily.groupby("stock_id").tail(5)
        t = t.merge(last5.groupby("stock_id")["net"].sum().rename("inst_net_5d").reset_index(),
                    on="stock_id", how="left")

    out = t.sort_values("premium")
    C.DATA_DIR.mkdir(exist_ok=True)
    with sqlite3.connect(C.DB_PATH) as con:
        old = pd.read_sql("SELECT * FROM daily", con) if _has_table(con) else pd.DataFrame()
        if not old.empty:
            old = old[old["date"] != day]  # 同一天重跑時換掉舊資料，避免重複
        # 欄位有增減時，舊資料一起重寫，資料庫才不會因為欄位不同而存不進去
        pd.concat([old, out], ignore_index=True).to_sql("daily", con, if_exists="replace", index=False)
    out.to_csv(C.DATA_DIR / f"daily_{day}.csv", index=False, encoding="utf-8-sig")
    disc = out[(out["premium"] < 0) & ~out["conv_not_started"]]
    print(f"{day}：{len(out)} 檔上市櫃可轉債，{out['traded_today'].sum()} 檔今天有成交，"
          f"{len(disc)} 檔折價且已可轉換")
    health_check(out)
    return out


# 健康檢查的警戒線（依 2026-10-21 正常資料設定：389 檔、99.5% 有股價、44% 有成交）
MIN_CB_COUNT = 300        # 可轉債總數低於這個數，可能是發行資料沒抓完整
MIN_STOCK_COVERAGE = 0.95  # 有股價的比例
MIN_TRADED_RATIO = 0.15    # 有成交的比例；太低通常是抓到非交易日或行情檔不完整
SUSPICIOUS_DISCOUNT = -0.05  # 折價超過 5% 很少見，多半是轉換價沒更新
EXTREME_PREMIUM = 5.0        # 溢價率超過 500%，可轉債幾乎只剩債券價值


def health_check(out):
    """印出每日資料健康狀況。⚠ 代表數字不正常，要回頭檢查資料來源。"""
    n = len(out)
    stock_cov = out["stock_close"].notna().mean() if n else 0
    traded = out["traded_today"].mean() if n else 0
    no_conv = out["conv_price_now"].isna() | (out["conv_price_now"] <= 0)
    tradable = out[out["traded_today"] & ~out["conv_not_started"]]
    deep = tradable[tradable["premium"] < SUSPICIOUS_DISCOUNT]
    extreme = (out["premium"] > EXTREME_PREMIUM).sum()

    def line(ok, text):
        print(f"  {'✓' if ok else '⚠'} {text}")

    print("健康檢查：")
    line(n >= MIN_CB_COUNT, f"可轉債總數 {n} 檔（正常 ≥ {MIN_CB_COUNT}）")
    line(stock_cov >= MIN_STOCK_COVERAGE, f"有股價 {stock_cov:.1%}（正常 ≥ {MIN_STOCK_COVERAGE:.0%}）")
    line(traded >= MIN_TRADED_RATIO, f"有成交 {traded:.1%}（正常 ≥ {MIN_TRADED_RATIO:.0%}）")
    line(not no_conv.any(), f"缺轉換價 {no_conv.sum()} 檔")
    line(deep.empty, f"折價超過 {-SUSPICIOUS_DISCOUNT:.0%} 的 {len(deep)} 檔（請確認轉換價是否已調整）")
    print(f"  · 溢價率超過 {EXTREME_PREMIUM:.0%} 的 {extreme} 檔（債性高，不是錯誤）")

    missing_stock = out[out["stock_close"].isna()]
    if len(missing_stock):
        names = "、".join(missing_stock["cb_name"].astype(str).str.strip().head(5))
        print(f"  · 沒有股價：{names}{' 等' if len(missing_stock) > 5 else ''}（可能是興櫃或暫停交易）")
    for _, r in deep.iterrows():
        print(f"    - {r['cb_id']} {str(r['cb_name']).strip()}：溢價率 {r['premium']:.2%}，轉換價 {r['conv_price_now']}")


def _has_table(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name='daily'").fetchone() is not None


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat())