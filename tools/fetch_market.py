#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thu thập SỐ LIỆU thị trường -> data/market_raw.json (Gói 1, phần số liệu)

Thay WebSearch bằng nguồn máy đọc được: 8 ô chỉ số từ Yahoo Chart (keyless),
Bitcoin kiểm chéo CoinGecko, USD/VND từ XML chính chủ Vietcombank, vàng SJC từ
JSON chính chủ SJC. Biên tập viên CHÉP NGUYÊN value/change_pct vào bulletin —
validate C6 sẽ đối chiếu từng số với file này, lệch là lỗi (chống chép nhầm).

Nguồn nào chết thì value=null cho ô đó (degrade mềm) — biên tập viên WebSearch
bù cho RIÊNG ô đó. Stooq đã đo là chết từ hạ tầng này (vòng 3) — không dùng.
Vietcombank yêu cầu ≤1 request/5 phút — pipeline chỉ chạy 1 lần/ngày.

Dùng:  python3 tools/fetch_market.py [--out data/market_raw.json]
Exit:  0 = có ít nhất một ô có số · 13 = toàn bộ nguồn chết (WebSearch toàn phần)
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

VN = timezone(timedelta(hours=7))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# (label đúng TILE_ORDER của validate, mã Yahoo, số thập phân, hậu tố hiển thị)
YAHOO_TILES = [
    ("S&P 500", "^GSPC", 1, ""),
    ("Dow Jones", "^DJI", 1, ""),
    ("Nasdaq", "^IXIC", 1, ""),
    ("Dầu WTI", "CL=F", 1, " USD"),
    ("Dầu Brent", "BZ=F", 1, " USD"),
    ("Vàng thế giới", "GC=F", 0, " USD"),
    ("Bitcoin", "BTC-USD", 0, " USD"),
    ("DXY", "DX-Y.NYB", 2, ""),
]
COINGECKO = ("https://api.coingecko.com/api/v3/simple/price"
             "?ids=bitcoin&vs_currencies=usd&include_24hr_change=true")
VCB_XML = "https://portal.vietcombank.com.vn/Usercontrols/TVPortal.TyGia/pXML.aspx"
SJC_JSON = "https://sjc.com.vn/GoldPrice/Services/PriceService.ashx"


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace"), r.status


def fmt_vn(value, decimals):
    """1234567.8 -> '1.234.567,8' (kiểu số Việt Nam, khớp hiển thị hiện có)."""
    txt = ("%%.%df" % decimals) % value
    if "." in txt:
        whole, frac = txt.split(".")
    else:
        whole, frac = txt, ""
    whole = "{:,}".format(int(whole)).replace(",", ".")
    return whole + ("," + frac if frac else "")


def health_row(source, url, status, ok, error):
    return {"source": source, "endpoint": urllib.parse.urlsplit(url).netloc,
            "http_status": status, "ok": ok, "error": error}


def fetch_yahoo(symbol):
    """Trả (giá mới nhất, đóng cửa phiên trước) từ mảng close 5 ngày —
    đúng cho cả chỉ số (nghỉ cuối tuần) lẫn BTC (chạy 24/7)."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s"
           "?interval=1d&range=5d" % urllib.parse.quote(symbol))
    text, status = http_get(url)
    data = json.loads(text)
    result = data["chart"]["result"][0]
    closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
    meta = result.get("meta") or {}
    price = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
    prev = closes[-2] if len(closes) >= 2 else meta.get("chartPreviousClose")
    return price, prev, status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "market_raw.json"))
    args = ap.parse_args()
    now = datetime.now(VN)
    tiles, health = [], []

    for label, symbol, decimals, suffix in YAHOO_TILES:
        row = {"label": label, "value": None, "value_raw": None,
               "change_pct": None, "source": "Yahoo %s" % symbol}
        status, err = None, None
        try:
            price, prev, status = fetch_yahoo(symbol)
            if price:
                row["value_raw"] = round(float(price), 4)
                row["value"] = fmt_vn(price, decimals) + suffix
                if prev:
                    row["change_pct"] = round((price / prev - 1.0) * 100, 2)
        except Exception as exc:  # noqa: BLE001 - nguồn chết thì ô null, không dừng
            if isinstance(exc, urllib.error.HTTPError):
                status = exc.code
            err = str(exc)[:120]
        tiles.append(row)
        health.append(health_row("Yahoo " + symbol,
                                 "https://query1.finance.yahoo.com/", status,
                                 row["value"] is not None, err))

    # Kiểm chéo Bitcoin bằng CoinGecko; Yahoo chết thì CoinGecko thế chỗ luôn.
    status, err, gecko = None, None, None
    try:
        text, status = http_get(COINGECKO)
        gecko = json.loads(text)["bitcoin"]
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, urllib.error.HTTPError):
            status = exc.code
        err = str(exc)[:120]
    btc = next(t for t in tiles if t["label"] == "Bitcoin")
    if gecko and gecko.get("usd"):
        if btc["value_raw"] is None:
            btc["value_raw"] = float(gecko["usd"])
            btc["value"] = fmt_vn(gecko["usd"], 0) + " USD"
            btc["change_pct"] = round(gecko.get("usd_24h_change") or 0, 2)
            btc["source"] = "CoinGecko"
        elif abs(btc["value_raw"] - gecko["usd"]) / gecko["usd"] > 0.03:
            err = "Yahoo lệch CoinGecko >3%% (%s vs %s)" % (btc["value_raw"], gecko["usd"])
    health.append(health_row("CoinGecko BTC", COINGECKO, status,
                             bool(gecko and gecko.get("usd")), err))

    # USD/VND — XML chính chủ Vietcombank (giá Chuyển khoản + Bán ra).
    usd, status, err = None, None, None
    try:
        text, status = http_get(VCB_XML)
        m = re.search(r'CurrencyCode="USD"[^>]*Transfer="([\d.,]+)"[^>]*Sell="([\d.,]+)"', text)
        if m:
            transfer = float(m.group(1).replace(",", ""))
            sell = float(m.group(2).replace(",", ""))
            usd = {"transfer_raw": transfer, "sell_raw": sell,
                   "value": fmt_vn(sell, 0),
                   "display": "Mua CK %s · Bán %s" % (fmt_vn(transfer, 0), fmt_vn(sell, 0)),
                   "source": "Vietcombank"}
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, urllib.error.HTTPError):
            status = exc.code
        err = str(exc)[:120]
    health.append(health_row("Vietcombank USD/VND", VCB_XML, status, usd is not None, err))

    # Vàng SJC — JSON chính chủ, lấy dòng "Vàng SJC 1L" chi nhánh Hồ Chí Minh.
    sjc, status, err = None, None, None
    try:
        text, status = http_get(SJC_JSON)
        rows = json.loads(text).get("data") or []
        row = next((r for r in rows if "SJC 1L" in (r.get("TypeName") or "")
                    and "Chí Minh" in (r.get("BranchName") or "")), rows[0] if rows else None)
        if row:
            buy = float(str(row["Buy"]).replace(",", "")) / 1000.0    # nghìn/lượng -> triệu
            sell = float(str(row["Sell"]).replace(",", "")) / 1000.0
            sjc = {"buy_trieu": round(buy, 1), "sell_trieu": round(sell, 1),
                   "display": "%s – %s triệu/lượng"
                              % (fmt_vn(buy, 1), fmt_vn(sell, 1)),
                   "source": "SJC"}
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, urllib.error.HTTPError):
            status = exc.code
        err = str(exc)[:120]
    health.append(health_row("SJC vàng", SJC_JSON, status, sjc is not None, err))

    got = sum(1 for t in tiles if t["value"] is not None)
    payload = {
        "fetched_at": now.isoformat(),
        "tiles": tiles,
        "usd_vnd": usd,
        "sjc": sjc,
        "sources_health": health,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    print("So lieu: %d/8 o chi so · USD/VND %s · SJC %s -> %s"
          % (got, "OK" if usd else "CHET", "OK" if sjc else "CHET", args.out))
    for t in tiles:
        print("   %-14s %-14s %s" % (t["label"], t["value"] or "—",
                                     ("%+.2f%%" % t["change_pct"]) if t["change_pct"] is not None else ""))
    if usd:
        print("   USD/VND        %s" % usd["display"])
    if sjc:
        print("   Vang SJC       %s" % sjc["display"])
    for h in health:
        if h["error"]:
            print("   LOI: %s: %s" % (h["source"], h["error"]))
    return 0 if got else 13


if __name__ == "__main__":
    sys.exit(main())
