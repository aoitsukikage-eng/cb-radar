"""檢查今天的資料出來了沒，用來決定自動排程要設幾點。

用法：python check_ready.py            # 檢查今天
      python check_ready.py 2026-10-21 # 檢查指定日期

建議找一個交易日，在 16:30、17:00、17:30、18:00 各跑一次，
看每個來源從幾點開始顯示 ✓，排程就設在全部 ✓ 之後再多留 15 分鐘。
"""
import datetime as dt
import sys

import requests

import config as C

HEADERS = {"User-Agent": "ntpu-gdg-cb-project"}


def check_cb_csv(d):
    url = C.CB_QUOTES_URL.format(y=d.strftime("%Y"), ym=d.strftime("%Y%m"), ymd=d.strftime("%Y%m%d"))
    r = requests.get(url, timeout=30, headers=HEADERS)
    return r.status_code == 200 and b"BODY" in r.content, f"HTTP {r.status_code}"


def check_twse(d):
    j = requests.get(C.TWSE_DAILY_URL.format(ymd=d.strftime("%Y%m%d")), timeout=30, headers=HEADERS).json()
    return j.get("stat") == "OK", j.get("stat", "")[:20]


def check_tpex(d):
    data = requests.get(C.TPEX_BASE + C.TPEX_STOCK_PATH, timeout=30, headers=HEADERS).json()
    got = str(data[0].get("Date", "")) if data else ""
    want = f"{d.year - 1911}{d:%m%d}"
    return got == want, f"最新日期 {got}"


def main():
    d = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today()
    print(f"{dt.datetime.now():%H:%M} 檢查 {d} 的資料：")
    ok_all = True
    for name, fn in [("可轉債行情 CSV（櫃買）", check_cb_csv),
                     ("上市股價（證交所）", check_twse),
                     ("上櫃股價（櫃買 OpenAPI）", check_tpex)]:
        try:
            ok, note = fn(d)
        except Exception as e:
            ok, note = False, f"錯誤：{str(e)[:40]}"
        ok_all &= ok
        print(f"  {'✓' if ok else '✗'} {name}：{'已產生' if ok else '還沒有'}（{note}）")
    print("全部到齊，可以執行 fetch.py。" if ok_all else "還有資料沒出來，晚一點再試。")


if __name__ == "__main__":
    main()