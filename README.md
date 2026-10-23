# 可轉債資料整合

每個交易日收盤後抓可轉債、股價、法人資料，串成一張表，算出每檔可轉債的轉換價值和溢價率。

最後更新：2026-10-22（第一版完成，已用華星光三、今國光三實際驗證）

## 檔案

```
fetch.py              每日抓資料 → data/raw/<日期>/*.json（約 1～2 分鐘）
build.py              串表＋計算＋健康檢查 → data/cb.db、data/daily_<日期>.csv
mops_conv_price.py    MOPS 最新轉換價 → data/conv_price_adjustments.csv（每週手動）
check_ready.py        檢查今天的資料出來了沒（決定排程時間用）
discover_endpoints.py 列出櫃買中心 OpenAPI 的債券端點（已用過，留作參考）
config.py             網址、欄位對照、門檻設定
.github/workflows/    每日自動執行 fetch.py、build.py
```

## 資料來源

| 資料 | 來源 | 方式 | 備註 |
| --- | --- | --- | --- |
| 可轉債發行條件 | 櫃買中心 OpenAPI `/v1/bond_ISSBD5_data` | JSON | 只有「發行時」轉換價 |
| 可轉債每日收盤價 | 櫃買中心每日行情檔 `RSta0113.<日期>-C.csv` | CSV（Big5） | OpenAPI 沒有可轉債行情；只取「等價」列 |
| 上市股價 | 證交所每日收盤行情 `MI_INDEX` | JSON，可指定日期 | OpenAPI 的 `STOCK_DAY_ALL` 只有最新一天且更新較晚，已不用 |
| 上櫃股價 | 櫃買中心 OpenAPI `tpex_mainboard_daily_close_quotes` | JSON | 只有最新一天，要當天執行 |
| 三大法人 | 證交所 `T86`＋櫃買中心三大法人買賣明細 | JSON，一次全市場 | 近 5 個交易日共 10 個請求；原本用 FinMind 要 40 分鐘，已改掉 |
| 最新轉換價 | 公開資訊觀測站「最近三個月現況查詢」`ajax_t120sb02` | HTML | 一次請求約 425 檔；每週手動執行一次 |

## 第一次設定（Windows）

需要 **Python 3.12**（3.14 以上 pandas 還沒有安裝檔）。

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install --only-binary=pandas,numpy -r requirements.txt
```

如果 activate 被擋：`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`。

## 每天怎麼跑

**務必用「當天」的日期**，因為上櫃股價只能拿到最新一天。

```powershell
python check_ready.py                  # 確認資料都出來了
python fetch.py 2026-10-21             # 約 1～2 分鐘
python build.py 2026-10-21
```

只想更新價格、不抓法人：`python fetch.py 2026-10-21 --no-inst`。

## 每週一次：更新轉換價

```powershell
python mops_conv_price.py --probe   # 只檢查，不寫檔
python mops_conv_price.py           # 寫入 conv_price_adjustments.csv
python build.py <最近交易日>        # 套用新轉換價
```

公開資訊觀測站的 robots.txt 不允許自動程式抓取，所以這支程式**只在自己電腦手動執行**，每次只送一個請求，不放進 GitHub Actions。

也可以手動在 `data/conv_price_adjustments.csv` 加一行：

```csv
cb_id,conv_price,effective_date
49793,175.7,2026-07-20
```

## 計算方式

- 轉換價值 = 100 ÷ 目前轉換價 × 股價
- 溢價率 = 可轉債收盤價 ÷ 轉換價值 − 1（負數＝折價）
- 目前轉換價：有調整紀錄就用最新一筆，沒有就用發行時轉換價
- 可轉債代號前 4 碼＝股票代號

## 結果表重要欄位（daily_<日期>.csv）

| 欄位 | 意思 |
| --- | --- |
| `cb_close`、`stock_close` | 可轉債、股票收盤價 |
| `conv_price_now` | 計算用的轉換價 |
| `premium` | 溢價率，`-0.02` 代表 −2% |
| `premium_null_reason` | `premium` 是空值時，為什麼（`not_traded`／`no_stock_price`／`no_conv_price`／`exchangeable`），正常有算出來時是 `None` |
| `signal` | 訊號分級：`candidate`（折價且可執行，值得看一眼）／`suspect`（深度折價，多半是轉換價沒更新，別當成機會）／`none`（其他） |
| `conv_price_asof` | 這筆資料用的轉換價調整表版本（`conv_price_adjustments.csv` 當時最新一筆的生效日），方便回頭追某天的 premium 是依據哪個版本算出來的 |
| `inst_net_5d` | 三大法人近 5 日買賣超（張） |
| `traded_today` | 當天有沒有成交（約一半沒有） |
| `conv_not_started` | 轉換期間還沒開始，不能套利 |
| `is_exchangeable` | 交換債（名稱有 E），換的是別家股票，溢價率已清空 |

私募債（代號不是數字）已自動排除。

## 網頁（docs/）

`build.py` 最後會把當天排行榜寫成 `docs/data/latest.json`，近 60 個交易日的溢價率走勢寫成
`docs/data/history.json`，給 `docs/index.html`（純 HTML/CSS/JS，沒有框架、沒有後端）讀取顯示：

- 表格依溢價率排序，可篩選「只看今天有成交」「排除還不能轉換」「排除交換債」
- `signal = candidate` 的列會標綠色「訊號」，`suspect` 標橘色「⚠ 資料可疑」
- 點一列會跳出該檔過去幾天的溢價率走勢圖

用 GitHub Pages（從 `main` 分支的 `/docs` 資料夾）部署，每次 Actions 把新的 JSON push 回 `main` 就會自動重新部署，不需要另外架伺服器。
本機預覽：`python -m http.server --directory docs`。

## 健康檢查

`build.py` 最後會印出檢查結果，✓ 正常、⚠ 要回頭查資料來源：

- 可轉債總數 ≥ 300、有股價 ≥ 95%、有成交 ≥ 15%
- 缺轉換價的檔數、折價超過 5% 的檔數（多半是轉換價沒更新）
- 股價日期和可轉債行情日期不一致時，會跳出 ⚠ 警告

正常的一天（2026-10-21）：389 檔、99.5% 有股價、44% 有成交。

## 自動排程

`.github/workflows/daily.yml` 設定在**每個交易日台灣時間 17:30** 執行 `fetch.py` 和 `build.py`
（2026-10-22 用 `check_ready.py` 確認 17:15 時資料已到齊）。

1. 推上 GitHub（`.gitignore` 會排除 `.venv`、`data/raw`）。不需要任何金鑰。
2. 到 Actions 分頁按 `Run workflow` 手動測一次，確認 GitHub 的國外伺服器連得到證交所和櫃買中心。
3. 每週手動跑完 `mops_conv_price.py` 後，把 `data/conv_price_adjustments.csv` 也推上 GitHub，自動排程才會套用。

cron 用 UTC 時間（台灣時間減 8 小時）；要改時間就改 `daily.yml` 裡的 `cron`。

## 已知限制

- 有成交的可轉債約只有一半，推播時要加成交量條件。
- 報價是收盤後資料，不是即時行情。
- 實際套利還要扣手續費、交易稅、放空股票的成本與限制。本專案結果僅供研究，不構成投資建議。