#!/usr/bin/env python3
"""ドローダウンチェッカー用データ取得スクリプト。

Yahoo Finance chart API（登録不要）から11指数の日次終値を取得し、
海外指数はドル円で円換算して、ページ埋め込み用の data.json を出力する。
AIは介さない（数値はAPIから直接取得する）。

出力 data.json:
{
  "updated": "2026-11-21",          # 最新データ日（米国市場の最終取引日）
  "indices": [
    {"id": "sp500", "name": "S&P 500", "daily": [["2026-11-20", 12345.6], ...],
     "weekly": [...]},
    ...
  ]
}
daily  = 直近約400日（年初来・1年表示用）
weekly = 10年分を週次サンプル（3年・5年・最長表示用）
"""
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range=10y&interval=1d"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

# id, 表示名, Yahooシンボル, 円換算するか
INDICES = [
    ("sp500",    "S&P 500",   "%5EGSPC",   True),
    ("nasdaq100", "NASDAQ 100", "%5ENDX",   True),
    ("nikkei",   "日経平均",   "%5EN225",   False),
    ("topix",    "TOPIX",     "1306.T",    False),
    ("orukan",   "オルカン（全世界）", "ACWI", True),
    ("kokusai",  "MSCIコクサイ", "TOK",    True),
    ("schd",     "SCHD",      "SCHD",      True),
    ("sox",      "SOX（半導体）", "%5ESOX",  True),
    ("fang",     "FANG+",     "%5ENYFANG", True),
    ("vti",      "VTI（全米株式）", "VTI",  True),
    ("gold",     "金（ゴールド）", "GLD",   True),
]
FX_SYMBOL = "JPY%3DX"  # ドル円


def fetch_closes(symbol: str) -> list[tuple[str, float]]:
    """(日付ISO, 終値) のリストを日付昇順で返す。"""
    url = API.format(sym=symbol)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as res:
        data = json.load(res)
    result = data["chart"]["result"][0]
    stamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    out = []
    for ts, close in zip(stamps, closes):
        if close is None:
            continue
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        out.append((day, float(close)))
    out.sort()
    return out


def clean_series(series: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """周囲と比べてだけ大きく外れた異常値（誤データ）を取り除く。

    例：1306.T で 2026-03-30〜31 の2日間だけ価格が約1/10になった誤配信があった。
    前後2点ずつの中央値から25%以上ずれ、かつ近傍の過半が中央値付近に
    そろっている場合に、その点を除去する（連続した誤データにも対応）。
    """
    n = len(series)
    if n < 5:
        return series
    closes = [c for _, c in series]
    out = []
    removed = 0
    for i, (day, close) in enumerate(series):
        neigh = closes[max(0, i - 2):i] + closes[i + 1:i + 3]
        srt = sorted(neigh)
        ref = srt[len(srt) // 2]
        near = sum(1 for v in neigh if abs(v / ref - 1) < 0.15)
        if abs(close / ref - 1) > 0.25 and near >= (len(neigh) + 1) // 2:
            removed += 1
            continue
        out.append((day, close))
    if removed:
        print(f"  異常値を{removed}件除去", file=sys.stderr)
    return out


def to_jpy(series: list[tuple[str, float]], fx: dict[str, float]) -> list[tuple[str, float]]:
    """ドル建て終値を、その日以前で直近のドル円レートで円換算する。"""
    fx_days = sorted(fx)
    out = []
    j = 0
    rate = None
    for day, close in series:
        while j < len(fx_days) and fx_days[j] <= day:
            rate = fx[fx_days[j]]
            j += 1
        if rate is None:
            continue
        out.append((day, close * rate))
    return out


def slim(close: float) -> float:
    """埋め込みデータ軽量化のための丸め。下落率の精度に影響しない範囲。"""
    if close >= 1000:
        return round(close)
    return round(close, 1)


def weekly_sample(series: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """週次サンプル：ISO週ごとに最後の取引日を残す。"""
    by_week = {}
    for day, close in series:
        y, m, d = (int(x) for x in day.split("-"))
        iso = datetime(y, m, d).isocalendar()
        by_week[(iso[0], iso[1])] = (day, close)
    return [by_week[k] for k in sorted(by_week)]


def data_hash(indices: list) -> int:
    """データ破損検出用のハッシュ。ページ側JSの computeHash と同一アルゴリズム。"""
    acc = 0
    mod = 1000000007
    for idx in indices:
        for key in ("daily", "weekly"):
            for day, val in idx[key]:
                acc = (acc * 31 + int(round(val * 10))) % mod
                for ch in day:
                    acc = (acc * 31 + ord(ch)) % mod
    return acc


def main() -> int:
    print("ドル円を取得中...", file=sys.stderr)
    fx_series = clean_series(fetch_closes(FX_SYMBOL))
    fx = dict(fx_series)
    if len(fx) < 100:
        print("ドル円データが不足しています", file=sys.stderr)
        return 1

    cutoff_daily = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d")
    indices = []
    latest_days = []
    for idx_id, name, symbol, use_fx in INDICES:
        print(f"{name} を取得中...", file=sys.stderr)
        series = clean_series(fetch_closes(symbol))
        if len(series) < 100:
            print(f"{name} のデータが不足しています（{len(series)}件）", file=sys.stderr)
            return 1
        if use_fx:
            series = to_jpy(series, fx)
        daily = [(d, slim(c)) for d, c in series if d >= cutoff_daily]
        weekly = [(d, slim(c)) for d, c in weekly_sample(series)]
        latest_days.append(series[-1][0])
        indices.append({"id": idx_id, "name": name, "daily": daily, "weekly": weekly})
        print(f"  {len(series)}件, 最新 {series[-1][0]}", file=sys.stderr)
        time.sleep(1)

    payload = {"updated": max(latest_days), "indices": indices, "hash": data_hash(indices)}
    out_path = sys.argv[1] if len(sys.argv) > 1 else "data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"書き出しました: {out_path}（基準日 {payload['updated']}）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
