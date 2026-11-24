#!/usr/bin/env python3
"""Generate funds.json for the NAV forecast checker according to spec-fetch-funds.md."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 30
JST = dt.timezone(dt.timedelta(hours=9))

FUNDS: list[dict[str, str]] = [
    {
        "id": "emaxis-orukan",
        "name": "eMAXIS Slim 全世界株式（オール・カントリー）",
        "company": "三菱UFJアセットマネジメント",
        "code": "0331418A",
        "proxy": "ACWI",
        "category": "全世界株式",
        "color": "#b07cc6",
        "title_key": "eMAXIS Slim 全世界株式",
    },
    {
        "id": "rakuten-vt",
        "name": "楽天・全世界株式インデックス・ファンド",
        "company": "楽天投信投資顧問",
        "code": "9I311179",
        "proxy": "VT",
        "category": "全世界株式",
        "color": "#d1608f",
        "title_key": "楽天・全世界株式",
    },
    {
        "id": "sbi-yukidaruma",
        "name": "SBI・全世界株式インデックス・ファンド",
        "company": "SBIアセットマネジメント",
        "code": "8931217C",
        "proxy": "VT",
        "category": "全世界株式",
        "color": "#5c7cfa",
        "title_key": "SBI・全世界株式",
    },
    {
        "id": "emaxis-sp500",
        "name": "eMAXIS Slim 米国株式（S&P500）",
        "company": "三菱UFJアセットマネジメント",
        "code": "03311187",
        "proxy": "%5ESP500TR",
        "category": "米国株式",
        "color": "#e56b4e",
        "title_key": "eMAXIS Slim 米国株式",
    },
    {
        "id": "sbi-v-sp500",
        "name": "SBI・V・S&P500インデックス・ファンド",
        "company": "SBIアセットマネジメント",
        "code": "89311199",
        "proxy": "%5ESP500TR",
        "category": "米国株式",
        "color": "#3b82c4",
        "title_key": "SBI・V・S&P500",
    },
    {
        "id": "rakuten-vti",
        "name": "楽天・全米株式インデックス・ファンド",
        "company": "楽天投信投資顧問",
        "code": "9I312179",
        "proxy": "VTI",
        "category": "米国株式",
        "color": "#a1887f",
        "title_key": "楽天・全米株式",
    },
    {
        "id": "emaxis-kokusai",
        "name": "eMAXIS Slim 先進国株式インデックス",
        "company": "三菱UFJアセットマネジメント",
        "code": "03319172",
        "proxy": "TOK",
        "category": "先進国株式",
        "color": "#4db6ac",
        "title_key": "eMAXIS Slim 先進国株式",
    },
    {
        "id": "ifree-nasdaq",
        "name": "iFreeNEXT NASDAQ100インデックス",
        "company": "大和アセットマネジメント",
        "code": "04317188",
        "proxy": "%5ENDX",
        "category": "NASDAQ100",
        "color": "#e8a13a",
        "title_key": "iFreeNEXT NASDAQ100",
    },
]

PROXY_SYMBOLS: list[str] = ["ACWI", "VT", "%5ESP500TR", "VTI", "TOK", "%5ENDX"]
FX_SYMBOL = "JPY%3DX"

JP_HOLIDAYS: set[str] = {
    # 2026
    "2026-01-01",
    "2026-01-12",
    "2026-02-11",
    "2026-02-23",
    "2026-03-20",
    "2026-04-29",
    "2026-05-03",
    "2026-05-04",
    "2026-05-05",
    "2026-05-06",
    "2026-07-20",
    "2026-08-11",
    "2026-09-21",
    "2026-09-22",
    "2026-09-23",
    "2026-10-12",
    "2026-11-03",
    "2026-11-23",
    "2026-12-31",
    # 2027
    "2027-01-01",
    "2027-01-02",
    "2027-01-03",
    "2027-01-11",
    "2027-02-11",
    "2027-02-23",
    "2027-04-29",
    "2027-05-03",
    "2027-05-04",
    "2027-05-05",
    "2027-07-19",
    "2027-08-11",
    "2027-09-20",
    "2027-09-23",
    "2027-10-11",
    "2027-11-03",
    "2027-11-23",
    "2027-12-31",
}

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
DATE_RE = re.compile(r"(20\d{2})/(\d{1,2})/(\d{1,2})")
NAV_RE = re.compile(r"\d{2},\d{3}")


def http_get_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def fetch_chart_closes(symbol: str) -> dict[str, float]:
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?range=3mo&interval=1d"
    raw = http_get_text(url)
    payload = json.loads(raw)
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(f"Yahoo chart API error for {symbol}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"Empty chart result for {symbol}")
    result = results[0]
    timestamps = result.get("timestamp") or []
    quotes = (result.get("indicators") or {}).get("quote") or []
    if not quotes:
        raise RuntimeError(f"Missing quote indicators for {symbol}")
    closes = quotes[0].get("close") or []

    series: dict[str, float] = {}
    for ts, close in zip(timestamps, closes):
        if ts is None or close is None:
            continue
        date_str = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%d")
        series[date_str] = float(close)
    if not series:
        raise RuntimeError(f"No valid close prices for {symbol}")
    return series


def normalize_for_title_match(text: str) -> str:
    return re.sub(r"\s+", "", html.unescape(text))


def fetch_fund_history(code: str, title_key: str) -> list[list[Any]]:
    url = f"https://finance.yahoo.co.jp/quote/{code}/history"
    page = http_get_text(url)

    title_match = TITLE_RE.search(page)
    if not title_match:
        raise RuntimeError(f"Missing <title> for {code}")
    title_text = html.unescape(title_match.group(1))
    if normalize_for_title_match(title_key) not in normalize_for_title_match(title_text):
        raise RuntimeError(
            f"Title mismatch for {code}: expected '{title_key}' in '{title_text}'"
        )

    by_date: dict[str, int] = {}
    for dm in DATE_RE.finditer(page):
        y, m, d = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
        date_str = f"{y:04d}-{m:02d}-{d:02d}"
        if date_str in by_date:
            continue
        snippet = page[dm.end() : dm.end() + 300]
        pm = NAV_RE.search(snippet)
        if not pm:
            continue
        nav = int(pm.group(0).replace(",", ""))
        by_date[date_str] = nav

    if not by_date:
        raise RuntimeError(f"No NAV entries extracted for {code}")

    sorted_items = sorted(by_date.items(), key=lambda kv: kv[0], reverse=True)
    return [[d, v] for d, v in sorted_items[:25]]


def next_jp_business_day(date_str: str) -> str:
    d = dt.date.fromisoformat(date_str) + dt.timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in JP_HOLIDAYS:
        d += dt.timedelta(days=1)
    return d.isoformat()


def find_pending_date(s_t: str, target_date: str) -> str | None:
    d = dt.date.fromisoformat(s_t) + dt.timedelta(days=1)
    end = dt.date.fromisoformat(target_date)
    while d < end:
        if d.weekday() < 5:
            return d.isoformat()
        d += dt.timedelta(days=1)
    return None


def latest_date_before(sorted_dates: list[str], bound: str) -> str | None:
    for d in reversed(sorted_dates):
        if d < bound:
            return d
    return None


def djb2_hash(text: str) -> int:
    h = 5381
    for ch in text:
        h = ((h * 33) ^ ord(ch)) & 0xFFFFFFFF
    return h


def parse_args(argv: list[str]) -> argparse.Namespace:
    default_output = Path(__file__).resolve().parent / "funds.json"
    parser = argparse.ArgumentParser(
        description="Fetch Japanese mutual fund NAV anchors and forecast values."
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=str(default_output),
        help=f"Output JSON path (default: {default_output})",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    output_path = Path(args.output)

    # 1. Fetch FX and proxy series (any failure is a fatal error -> exit 1)
    try:
        fx_series = fetch_chart_closes(FX_SYMBOL)
        yen_series_by_proxy: dict[str, dict[str, float]] = {}
        for sym in PROXY_SYMBOLS:
            proxy_series = fetch_chart_closes(sym)
            common_dates = sorted(set(proxy_series.keys()) & set(fx_series.keys()))
            if not common_dates:
                raise RuntimeError(f"No overlapping dates between {sym} and {FX_SYMBOL}")
            yen_series_by_proxy[sym] = {
                d: proxy_series[d] * fx_series[d] for d in common_dates
            }
    except Exception as exc:
        print(f"ERROR: Failed to fetch market/FX data: {exc}", file=sys.stderr)
        return 1

    # 2. Fetch each fund and compute forecast + self-verification
    fund_entries: list[dict[str, Any]] = []
    success_count = 0
    top_anchor_date: str | None = None
    top_target_date: str | None = None

    for spec in FUNDS:
        try:
            history = fetch_fund_history(spec["code"], spec["title_key"])
            anchor_date, anchor = history[0][0], history[0][1]

            yen_series = yen_series_by_proxy[spec["proxy"]]
            sorted_dates = sorted(yen_series.keys())
            s_a = latest_date_before(sorted_dates, anchor_date)
            if s_a is None:
                raise RuntimeError(
                    f"No proxy session before anchor_date {anchor_date} for {spec['id']}"
                )
            s_t = sorted_dates[-1]

            ratio = yen_series[s_t] / yen_series[s_a]
            forecast = round(anchor * ratio)
            chg = round((ratio - 1.0) * 100.0, 2)
            target_date = next_jp_business_day(anchor_date)
            pending_date = find_pending_date(s_t, target_date)

            # Self-verification using the 2nd history item
            self_verify_str = "N/A"
            if len(history) >= 2:
                prev_date, prev_nav = history[1][0], history[1][1]
                s_prev = latest_date_before(sorted_dates, prev_date)
                if s_prev is not None:
                    v_ratio = yen_series[s_a] / yen_series[s_prev]
                    v_est = round(prev_nav * v_ratio)
                    v_err_pct = (v_est - anchor) / anchor * 100.0
                    self_verify_str = f"{v_err_pct:+.2f}% (推計 {v_est:,} vs 実績 {anchor:,})"

            entry: dict[str, Any] = {
                "id": spec["id"],
                "name": spec["name"],
                "company": spec["company"],
                "code": spec["code"],
                "category": spec["category"],
                "color": spec["color"],
                "anchor": anchor,
                "anchor_date": anchor_date,
                "target_date": target_date,
                "forecast": forecast,
                "chg": chg,
                "reflected_date": s_t,
                "pending_date": pending_date,
                "history": history,
            }
            fund_entries.append(entry)
            success_count += 1
            if top_anchor_date is None:
                top_anchor_date = anchor_date
                top_target_date = target_date

            print(
                f"{spec['name']} | "
                f"アンカー: {anchor:,} ({anchor_date}) | "
                f"予測: {forecast:,} ({chg:+.2f}%, target={target_date}, reflected={s_t}, pending={pending_date}) | "
                f"自己検証誤差: {self_verify_str}"
            )
        except Exception as exc:
            fund_entries.append(
                {
                    "id": spec["id"],
                    "name": spec["name"],
                    "error": True,
                }
            )
            print(f"{spec['name']} | ERROR: {exc}")

    if success_count == 0 or top_anchor_date is None or top_target_date is None:
        print("ERROR: All funds failed; funds.json was not written.", file=sys.stderr)
        return 1

    # 3. Compute djb2 hash and write JSON
    hash_parts: list[str] = []
    for f in fund_entries:
        if f.get("error"):
            hash_parts.append(f"{f['id']}:error")
        else:
            hash_parts.append(f"{f['id']}:{f['anchor']}:{f['forecast']}")
    hash_input = f"{top_anchor_date}|{top_target_date}|{';'.join(hash_parts)}"

    payload: dict[str, Any] = {
        "generated_at": dt.datetime.now(JST).replace(microsecond=0).isoformat(),
        "anchor_date": top_anchor_date,
        "target_date": top_target_date,
        "funds": fund_entries,
        "hash": djb2_hash(hash_input),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
