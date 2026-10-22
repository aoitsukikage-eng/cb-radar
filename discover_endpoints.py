"""步驟 1：從櫃買中心 swagger 文件找出可轉債相關端點，只要跑一次。

用法：python discover_endpoints.py
找到後把路徑填進 config.py 的 CB_TERMS_PATH、CB_QUOTES_PATH。
"""
import requests

from config import CB_KEYWORDS, TPEX_SWAGGER


def main():
    spec = requests.get(TPEX_SWAGGER, timeout=30).json()
    hits = []
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            text = f"{path} {op.get('summary', '')} {op.get('description', '')}"
            if any(k.lower() in text.lower() for k in CB_KEYWORDS):
                hits.append((path, method.upper(), op.get("summary", "")))
    if not hits:
        print("沒找到關鍵字，請改 config.CB_KEYWORDS 再試。")
    for path, method, summary in hits:
        print(f"{method:5} {path:60} {summary}")


if __name__ == "__main__":
    main()
