#!/usr/bin/env python3
"""世界の株価・最新終値一覧用データ取得スクリプト。

Yahoo Finance chart API（登録不要）から世界主要21指数の
日次終値（6か月）と当日5分足（1日）を取得し、world.json を出力する。

使い方:
  python3 fetch_world.py world.json
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DAILY_API = "https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range=6mo&interval=1d"
INTRA_API = "https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=5m"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
JST = timezone(timedelta(hours=9))

# (Yahooシンボル, 指数名, 地域ラベル) — 順序固定
INDICES = [
    ("^N225",     "日経平均",             "日本"),
    ("NIY=F",     "日経先物",             "日本"),
    ("^DJI",      "ダウ平均",             "米国"),
    ("^IXIC",     "NASDAQ総合",           "米国"),
    ("^GSPC",     "S&P 500",              "米国"),
    ("^NDX",      "NASDAQ 100",           "米国"),
    ("^RUT",      "ラッセル2000",         "米国"),
    ("^GSPTSE",   "TSX総合",              "カナダ"),
    ("^FTSE",     "FTSE 100",             "英国"),
    ("^GDAXI",    "DAX",                  "ドイツ"),
    ("^FCHI",     "CAC 40",               "フランス"),
    ("^STOXX50E", "ユーロ・ストックス50", "欧州"),
    ("000001.SS", "上海総合",             "中国"),
    ("^HSI",      "ハンセン",             "香港"),
    ("^BSESN",    "ムンバイSENSEX",       "インド"),
    ("^KS11",     "KOSPI",                "韓国"),
    ("^TWII",     "台湾加権",             "台湾"),
    ("^NSEI",     "NIFTY 50",             "インド"),
    ("^AORD",     "豪州全普通株",         "豪州"),
    ("^AXJO",     "ASX 200",              "豪州"),
    ("^BVSP",     "ボベスパ",             "ブラジル"),
]


def fetch_chart(url_template: str, symbol: str) -> dict:
    """Yahoo Finance chart API から JSON を取得して result[0] を返す。"""
    encoded = urllib.parse.quote(symbol, safe="")
    url = url_template.format(sym=encoded)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as res:
        data = json.load(res)
    results = data.get("chart", {}).get("result")
    if not results:
        raise ValueError(f"Empty chart result for {symbol}")
    return results[0]


def fetch_daily(symbol: str) -> list[list]:
    """日次終値 [[日付, 終値], ...] を現地日付昇順で返す。"""
    result = fetch_chart(DAILY_API, symbol)
    gmtoffset = result.get("meta", {}).get("gmtoffset", 0) or 0
    market_tz = timezone(timedelta(seconds=int(gmtoffset)))
    stamps = result.get("timestamp") or []
    quotes = result.get("indicators", {}).get("quote") or [{}]
    closes = quotes[0].get("close") or []

    by_date = {}
    for ts, close in zip(stamps, closes):
        if close is None:
            continue
        day = datetime.fromtimestamp(ts, tz=market_tz).strftime("%Y-%m-%d")
        by_date[day] = round(float(close), 2)

    return [[day, by_date[day]] for day in sorted(by_date)]


def fetch_intra(symbol: str) -> dict | None:
    """当日5分足 {"date": 日付, "points": [["HH:MM", 終値], ...]} を返す。"""
    result = fetch_chart(INTRA_API, symbol)
    gmtoffset = result.get("meta", {}).get("gmtoffset", 0) or 0
    market_tz = timezone(timedelta(seconds=int(gmtoffset)))
    stamps = result.get("timestamp") or []
    quotes = result.get("indicators", {}).get("quote") or [{}]
    closes = quotes[0].get("close") or []

    raw = []
    for ts, close in zip(stamps, closes):
        if close is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=market_tz)
        raw.append((dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M"), round(float(close), 2)))

    if not raw:
        return None

    latest_date = max(d for d, _, _ in raw)
    points = [[hm, c] for d, hm, c in raw if d == latest_date]
    if not points:
        return None
    return {"date": latest_date, "points": points}


def load_previous(out_path: str) -> dict[str, dict]:
    """既存の出力ファイルがあれば symbol -> エントリ の辞書を返す。"""
    if not os.path.exists(out_path):
        return {}
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            old = json.load(f)
        items = old.get("indices", [])
        return {item["symbol"]: item for item in items if isinstance(item, dict) and "symbol" in item}
    except Exception as exc:
        print(f"既存ファイルの読み込みに失敗しました（無視します）: {exc}", file=sys.stderr)
        return {}


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else "world.json"
    prev_map = load_previous(out_path)

    indices = []
    success_count = 0

    for i, (symbol, name, region) in enumerate(INDICES):
        if i > 0:
            time.sleep(1)
        print(f"[{i + 1}/{len(INDICES)}] {name} ({symbol}) を取得中...", file=sys.stderr)
        try:
            series = fetch_daily(symbol)
            if len(series) < 2:
                raise ValueError(f"日次データが不足しています（{len(series)}件）")

            last = series[-1]
            prev = series[-2]
            chg = round(last[1] - prev[1], 2)
            pct = round((chg / prev[1]) * 100, 2) if prev[1] else 0.0

            intra = None
            try:
                intra = fetch_intra(symbol)
            except Exception as intra_exc:
                print(f"  5分足取得失敗（intra=null で続行）: {intra_exc}", file=sys.stderr)

            indices.append({
                "name": name,
                "region": region,
                "symbol": symbol,
                "last": last,
                "prev": prev,
                "chg": chg,
                "pct": pct,
                "series": series,
                "intra": intra,
            })
            success_count += 1
            print(f"  OK: {last[0]} {last[1]} (前日比 {chg:+.2f}, {pct:+.2f}%)", file=sys.stderr)
        except Exception as exc:
            print(f"  取得失敗: {exc}", file=sys.stderr)
            if symbol in prev_map:
                print(f"  前回のデータを再利用します: {symbol}", file=sys.stderr)
                indices.append(prev_map[symbol])

    if success_count == 0:
        print("全指数の取得に失敗しました。", file=sys.stderr)
        return 1

    payload = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "indices": indices,
    }

    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    print(f"書き出しました: {out_path}（成功 {success_count}/{len(INDICES)}）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
