#!/usr/bin/env python3
"""世界の株価・最新終値一覧用データ取得スクリプト。

Yahoo Finance chart API（登録不要）から世界主要20指数の
日次終値（6か月）と5分足を取得し、world.json を出力する。

表示仕様（2026-11-27 案1）：全指数とも「直近の完了した取引日」の
セット（終値・前日比・その日の5分足）で統一する。取引中の当日は
まだ終わっていないため表示しない。これにより、数値と5分足と
前日終値ラインの日付がずれることがなくなる。

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
INTRA_API = "https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=5m"
INTRA_LONG_API = "https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range=1mo&interval=5m"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
JST = timezone(timedelta(hours=9))

# (Yahooシンボル, 指数名, 地域ラベル) — 順序固定
# 2026-11-27 読者関心ベースで20指数に再編（Yahoo!ファイナンス主要指数と
# 楽天証券の一覧を参考に、重複する豪州全普通株・NIFTY 50・ラッセル2000・
# TSX総合を外し、SOX・VIX・CSI 300を追加）
INDICES = [
    ("^N225",     "日経平均",             "日本"),
    ("NIY=F",     "日経先物",             "日本"),
    ("^DJI",      "ダウ平均",             "米国"),
    ("^IXIC",     "NASDAQ総合",           "米国"),
    ("^GSPC",     "S&P 500",              "米国"),
    ("^NDX",      "NASDAQ 100",           "米国"),
    ("^SOX",      "SOX（半導体）",        "米国"),
    ("^VIX",      "VIX（恐怖指数）",      "米国"),
    ("^FTSE",     "FTSE 100",             "英国"),
    ("^GDAXI",    "DAX",                  "ドイツ"),
    ("^FCHI",     "CAC 40",               "フランス"),
    ("^STOXX50E", "ユーロ・ストックス50", "欧州"),
    ("000001.SS", "上海総合",             "中国"),
    ("399300.SZ", "CSI 300",              "中国"),
    ("^HSI",      "ハンセン",             "香港"),
    ("^KS11",     "KOSPI",                "韓国"),
    ("^TWII",     "台湾加権",             "台湾"),
    ("^BSESN",    "ムンバイSENSEX",       "インド"),
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
    """日次終値 [[日付, 終値], ...] を現地日付昇順で返す。

    Yahooの日次系列は最新の取引日のバーが None のまま返ることがあり、
    そのままだと最新終値が1日遅れになる。meta の現在値
    （regularMarketPrice / regularMarketTime）で最新日を補完する。
    取引中の当日の補完エントリは、main 側で「完了した取引日」までに
    切り詰めるため、ここではそのまま含めてよい。
    """
    result = fetch_chart(DAILY_API, symbol)
    meta = result.get("meta", {})
    gmtoffset = meta.get("gmtoffset", 0) or 0
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

    series = [[day, by_date[day]] for day in sorted(by_date)]

    rmp = meta.get("regularMarketPrice")
    rmt = meta.get("regularMarketTime")
    if rmp is not None and rmt:
        live_day = datetime.fromtimestamp(int(rmt), tz=market_tz).strftime("%Y-%m-%d")
        live_val = round(float(rmp), 2)
        if not series or live_day > series[-1][0]:
            series.append([live_day, live_val])
        elif live_day == series[-1][0]:
            series[-1] = [live_day, live_val]

    return series


def fetch_intra_days(symbol: str, url_template: str = INTRA_API) -> tuple[dict, str]:
    """5分足を日別にまとめ ({日付: {"points", "end", "close"}}, 現地の今日) を返す。

    points は [["HH:MM", 値], ...]、end はその日の最後のポイント時刻、
    close はその日の最後の値。
    """
    result = fetch_chart(url_template, symbol)
    gmtoffset = result.get("meta", {}).get("gmtoffset", 0) or 0
    market_tz = timezone(timedelta(seconds=int(gmtoffset)))
    market_today = datetime.now(market_tz).strftime("%Y-%m-%d")
    stamps = result.get("timestamp") or []
    quotes = result.get("indicators", {}).get("quote") or [{}]
    closes = quotes[0].get("close") or []

    days: dict[str, dict] = {}
    for ts, close in zip(stamps, closes):
        if close is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=market_tz)
        day = dt.strftime("%Y-%m-%d")
        hm = dt.strftime("%H:%M")
        val = round(float(close), 2)
        entry = days.setdefault(day, {"points": [], "end": hm, "close": val})
        entry["points"].append([hm, val])
        entry["end"] = hm
        entry["close"] = val
    return days, market_today


def fetch_daily_from_intra(symbol: str) -> dict:
    """1か月分の5分足から日次終値 {日付: 終値} を再構成する。

    CSI 300 のように日次APIが1件しか返さない指数のための予備手段。
    """
    days, _ = fetch_intra_days(symbol, INTRA_LONG_API)
    return {day: entry["close"] for day, entry in days.items()}


def hhmm_minus(hhmm: str, minutes: int) -> str:
    """\"HH:MM\" から minutes 分引いた \"HH:MM\" を返す。"""
    dt = datetime.strptime(hhmm, "%H:%M") - timedelta(minutes=minutes)
    return dt.strftime("%H:%M")


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

            days: dict = {}
            market_today = None
            try:
                days, market_today = fetch_intra_days(symbol)
            except Exception as intra_exc:
                print(f"  5分足取得失敗（日次だけで続行）: {intra_exc}", file=sys.stderr)

            # 前回保存した world.json の系列で、今回欠けている過去日を
            # 補う。Yahooの日次バーは翌日になっても None のままのことが
            # あり（日経平均で実測）、前回までに確定した終値が唯一の
            # 頼りになる。現地の今日より前の日だけを対象にする（保存時
            # に取引中だった当日の値は混ぜない）。
            old_entry = prev_map.get(symbol)
            if old_entry and isinstance(old_entry.get("series"), list):
                have = {day for day, _ in series}
                merged = 0
                for day, val in old_entry["series"]:
                    if day in have:
                        continue
                    if market_today and day >= market_today:
                        continue
                    series.append([day, val])
                    have.add(day)
                    merged += 1
                if merged:
                    series.sort(key=lambda p: p[0])
                    print(f"  前回データから{merged}日分を補完しました", file=sys.stderr)

            # 日次系列が極端に短い指数（CSI 300 等）は、1か月分の
            # 5分足から再構成した日次終値で補う
            if len(series) < 30:
                try:
                    rebuilt = fetch_daily_from_intra(symbol)
                    have = {day for day, _ in series}
                    for day in sorted(rebuilt):
                        if day not in have:
                            series.append([day, rebuilt[day]])
                    series.sort(key=lambda p: p[0])
                    print(f"  日次を5分足から補完しました（{len(series)}件）", file=sys.stderr)
                except Exception as fb_exc:
                    print(f"  日次補完に失敗: {fb_exc}", file=sys.stderr)

            # 日次系列で終値が未反映（None）のまま欠けている日を、
            # 5分足のその日の最終値で補完する（先物は日付の区切りが
            # 取引日とずれるため対象外）
            if days and not symbol.endswith("=F"):
                have = {day for day, _ in series}
                for day in sorted(days):
                    if day not in have and series and series[0][0] < day < series[-1][0]:
                        series.append([day, days[day]["close"]])
                        have.add(day)
                series.sort(key=lambda p: p[0])

            if len(series) < 2:
                raise ValueError(f"日次データが不足しています（{len(series)}件）")

            # 表示する取引日 D = 直近の「完了した」取引日。
            # 完了の判定：現地の今日より前の日は完了。当日は、その日の
            # 5分足の最終時刻が、過去の取引日の最終時刻（の10分前）まで
            # 達していれば完了（短縮取引日は翌日に完了扱いになる）。
            target = None
            if days and market_today:
                past_ends = [e["end"] for d, e in days.items() if d < market_today]
                ref_end = max(past_ends) if past_ends else None

                def is_complete(day: str) -> bool:
                    if day < market_today:
                        return True
                    if day > market_today or ref_end is None:
                        return False
                    return days[day]["end"] >= hhmm_minus(ref_end, 10)

                complete_days = [d for d in days if is_complete(d)]
                if complete_days:
                    target = max(complete_days)
            if target is None:
                # 5分足が無い・判定できない場合は日次系列の最終日を使う
                target = series[-1][0]

            # 表示用系列は D までに切り詰める（取引中の当日を出さない）
            display = [p for p in series if p[0] <= target]
            if not display or display[-1][0] != target:
                if target in days:
                    display.append([target, days[target]["close"]])
                    display.sort(key=lambda p: p[0])
            if len(display) < 2:
                raise ValueError(f"表示データが不足しています（{len(display)}件）")

            last = display[-1]
            prev = display[-2]
            chg = round(last[1] - prev[1], 2)
            pct = round((chg / prev[1]) * 100, 2) if prev[1] else 0.0

            intra = None
            if target in days:
                intra = {"date": target, "points": days[target]["points"]}

            indices.append({
                "name": name,
                "region": region,
                "symbol": symbol,
                "last": last,
                "prev": prev,
                "chg": chg,
                "pct": pct,
                "series": display,
                "intra": intra,
                "intraPrev": prev,
            })
            success_count += 1
            print(f"  OK: 表示日 {last[0]} {last[1]} (前日比 {chg:+.2f}, {pct:+.2f}%)", file=sys.stderr)
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
