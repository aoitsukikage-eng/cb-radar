"""calc_valuation() 的公式測試：轉換價值/溢價率、null 理由代碼、訊號分級。

企劃書驗收標準：至少 20 個公式測試案例、50 檔處理時間在 10 分鐘內、無重複警示。

用法：pytest test_build.py -v
"""
import time

import pandas as pd
import pytest

from build import calc_valuation

DAY = "2026-10-22"
STARTED = "20200101"      # 早就開始的轉換期間
NOT_STARTED = "20270101"  # 還沒開始的轉換期間


def row(cb_close=100.0, stock_close=100.0, conv_price_now=100.0,
        conv_start=STARTED, cb_name="測試一", cb_id="11111", stock_id="1111"):
    """建一筆最小輸入列，預設是「剛好打平、可轉換、有成交」的正常案例。"""
    return pd.DataFrame([{
        "cb_id": cb_id, "cb_name": cb_name, "stock_id": stock_id,
        "cb_close": cb_close, "stock_close": stock_close,
        "conv_price_now": conv_price_now, "conv_start": conv_start, "date": DAY,
    }])


def calc1(**kwargs):
    return calc_valuation(row(**kwargs)).iloc[0]


# ── 正常計算：折價／溢價／打平 ──────────────────────────────
@pytest.mark.parametrize("cb_close, stock_close, conv_price_now, expect_premium", [
    (90, 100, 100, -0.10),   # 折價 10%
    (110, 100, 100, 0.10),   # 溢價 10%
    (100, 100, 100, 0.0),    # 剛好打平
    (95, 200, 100, -0.525),  # 轉換價值 200，CB 95 → 折價 52.5%
    (150, 80, 100, 0.875),   # 溢價 87.5%
])
def test_premium_formula(cb_close, stock_close, conv_price_now, expect_premium):
    r = calc1(cb_close=cb_close, stock_close=stock_close, conv_price_now=conv_price_now)
    assert r["premium"] == pytest.approx(expect_premium)
    assert r["premium_null_reason"] is None


# ── null 理由代碼：四種情況都要有明確理由，不能是裸 NaN ──────
def test_null_reason_not_traded():
    r = calc1(cb_close=None)
    assert pd.isna(r["premium"])
    assert r["premium_null_reason"] == "not_traded"


def test_null_reason_no_stock_price():
    r = calc1(stock_close=None)
    assert pd.isna(r["premium"])
    assert r["premium_null_reason"] == "no_stock_price"


@pytest.mark.parametrize("conv_price_now", [None, 0, -5])
def test_null_reason_no_conv_price(conv_price_now):
    r = calc1(conv_price_now=conv_price_now)
    assert pd.isna(r["premium"])
    assert r["premium_null_reason"] == "no_conv_price"


@pytest.mark.parametrize("cb_name", ["測試E", "某交換債E", "ABC-E"])
def test_null_reason_exchangeable(cb_name):
    r = calc1(cb_name=cb_name, cb_close=90)  # 即使有成交、折價，交換債也要清空 premium
    assert pd.isna(r["premium"])
    assert r["premium_null_reason"] == "exchangeable"
    assert r["is_exchangeable"]


def test_null_reason_priority_exchangeable_over_not_traded():
    """交換債且沒成交：理由要是 exchangeable，不是 not_traded（exchangeable 優先判斷）。"""
    r = calc1(cb_name="測試E", cb_close=None)
    assert r["premium_null_reason"] == "exchangeable"


# ── 訊號分級：candidate／suspect／none，跟「能不能直接拿來判斷套利」分開 ──
def test_signal_candidate_mild_discount():
    r = calc1(cb_close=98, stock_close=100, conv_price_now=100)  # premium ≈ -0.02
    assert r["signal"] == "candidate"


def test_signal_suspect_deep_discount():
    r = calc1(cb_close=80, stock_close=100, conv_price_now=100)  # premium = -0.20，超過門檻
    assert r["signal"] == "suspect"


def test_signal_just_above_threshold_is_candidate():
    """門檻是 -5%；折價 4.9% 還在門檻之內，算 candidate。"""
    r = calc1(cb_close=95.1, stock_close=100, conv_price_now=100)
    assert r["signal"] == "candidate"


def test_signal_just_below_threshold_is_suspect():
    """折價 5.1%，超過門檻，算 suspect（可疑，不是機會）。"""
    r = calc1(cb_close=94.9, stock_close=100, conv_price_now=100)
    assert r["signal"] == "suspect"


def test_signal_none_when_premium_positive():
    r = calc1(cb_close=120, stock_close=100, conv_price_now=100)
    assert r["signal"] == "none"


def test_signal_none_when_exchangeable():
    r = calc1(cb_name="測試E", cb_close=80, stock_close=100, conv_price_now=100)
    assert r["signal"] == "none"


def test_signal_none_when_not_started():
    r = calc1(cb_close=90, conv_start=NOT_STARTED)
    assert r["conv_not_started"]
    assert r["signal"] == "none"
    # 還沒能轉換，但 premium 仍照算（之後能轉換時才有意義），不是 null
    assert r["premium"] == pytest.approx(-0.10)


def test_signal_none_when_not_traded():
    r = calc1(cb_close=None)
    assert r["signal"] == "none"


# ── 效能與無重複警示（企劃書驗收數字） ────────────────────────
def test_performance_500_rows_under_10_minutes():
    n = 500
    df = pd.concat([row(cb_id=str(10000 + i), stock_id=str(1000 + i),
                         cb_close=90 + (i % 20), conv_start=STARTED)
                     for i in range(n)], ignore_index=True)
    start = time.perf_counter()
    out = calc_valuation(df)
    elapsed = time.perf_counter() - start
    assert len(out) == n
    assert elapsed < 600  # 企劃書驗收數字：50 檔 10 分鐘內；500 檔也留足餘裕


def test_no_duplicate_signal_per_cb_per_day():
    df = pd.concat([row(cb_id="11111"), row(cb_id="22222")], ignore_index=True)
    out = calc_valuation(df)
    assert not out["cb_id"].duplicated().any()
