#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gửi tin nhắn tóm tắt + link trang qua Telegram và Zalo.

Token KHÔNG nằm trong mã nguồn và KHÔNG nằm trong prompt. Script đọc từ
biến môi trường của environment cloud:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ZALO_BOT_TOKEN, ZALO_CHAT_ID
Thiếu biến nào thì bỏ qua kênh đó và báo rõ trong log.

Dùng:  python3 tools/notify.py [--bulletin data/bulletin.json] [--dry-run]
Exit:  0 = ít nhất 1 kênh gửi được · 50 = không kênh nào gửi được
       51 = thiếu toàn bộ cấu hình token
"""
import argparse
import html
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime

PAGE_URL = "https://damquangtien-ctrl.github.io/ban-tin-sang/"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log(msg):
    print(msg, flush=True)


def post_form(url, fields, timeout=45):
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")[:200]


def build_lines(bulletin):
    """Trả về (dòng tiêu đề, dòng số liệu, danh sách tin nổi bật)."""
    day = datetime.strptime(bulletin["date"], "%Y-%m-%d").strftime("%d/%m")
    now = datetime.now().strftime("%H:%M")
    scheduled = 5 <= datetime.now().hour <= 8
    head = ("📈 BẢN TIN SÁNG %s" % day if scheduled
            else "📈 BẢN TIN CẬP NHẬT %s %s" % (now, day))

    md = bulletin.get("market_data") or {}
    picks, tiles = [], {t.get("label"): t for t in md.get("tiles") or []}
    for row in md.get("domestic") or []:
        if row.get("label") == "VN-Index":
            arrow = {"up": "▲", "down": "▼"}.get(row.get("direction"), "•")
            picks.append("VN-Index %s %s" % (arrow, row.get("change") or row.get("value")))
            break
    for label, short in (("S&P 500", "S&P 500"), ("Vàng thế giới", "Vàng TG")):
        tile = tiles.get(label)
        if tile and tile.get("change_pct") is not None:
            pct = tile["change_pct"]
            arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "•")
            picks.append("%s %s %s%.2f%%" % (short, arrow, "+" if pct > 0 else "", pct))
    stats = " · ".join(picks).replace(".", ",") if picks else ""

    highlights = [(h.get("text", ""), h.get("source", "")) for h in bulletin.get("highlights") or []]
    return head, stats, highlights


def send_telegram(head, stats, highlights, dry):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (token and chat):
        log("   Telegram: BO QUA (thieu TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)")
        return None
    parts = ["<b>%s</b>" % html.escape(head)]
    if stats:
        parts.append(html.escape(stats))
    if highlights:
        parts.append("")
        parts.append("⭐ <b>Đáng chú ý:</b>")
        for i, (text, source) in enumerate(highlights, start=1):
            suffix = " (%s)" % html.escape(source) if source else ""
            parts.append("%d. %s%s" % (i, html.escape(text), suffix))
    parts.append("")
    parts.append('👉 <a href="%s">Đọc bản tin đầy đủ</a>' % PAGE_URL)
    body = "\n".join(parts)
    if dry:
        log("   Telegram (dry-run):\n%s" % body)
        return True
    try:
        status, resp = post_form(
            "https://api.telegram.org/bot%s/sendMessage" % token,
            {"chat_id": chat, "parse_mode": "HTML",
             "disable_web_page_preview": "true", "text": body})
        ok = status == 200 and '"ok":true' in resp
        log("   Telegram: %s (HTTP %s)" % ("OK" if ok else "LOI " + resp, status))
        return ok
    except Exception as exc:  # noqa: BLE001
        log("   Telegram: LOI %s" % str(exc)[:150])
        return False


def send_zalo(head, stats, highlights, dry):
    token = os.environ.get("ZALO_BOT_TOKEN", "").strip()
    chat = os.environ.get("ZALO_CHAT_ID", "").strip()
    if not (token and chat):
        log("   Zalo: BO QUA (thieu ZALO_BOT_TOKEN/ZALO_CHAT_ID)")
        return None
    parts = [head]
    if stats:
        parts.append(stats)
    if highlights:
        parts.append("")
        parts.append("⭐ Đáng chú ý:")
        for i, (text, source) in enumerate(highlights, start=1):
            parts.append("%d. %s%s" % (i, text, " (%s)" % source if source else ""))
    parts.append("")
    parts.append("👉 Đọc bản tin đầy đủ: %s" % PAGE_URL)
    body = "\n".join(parts)[:1900]
    if dry:
        log("   Zalo (dry-run):\n%s" % body)
        return True
    try:
        status, resp = post_form(
            "https://bot-api.zapps.me/bot%s/sendMessage" % token,
            {"chat_id": chat, "text": body})
        ok = status == 200
        log("   Zalo: %s (HTTP %s)" % ("OK" if ok else "LOI " + resp, status))
        return ok
    except Exception as exc:  # noqa: BLE001
        log("   Zalo: LOI %s" % str(exc)[:150])
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bulletin", default=os.path.join(ROOT, "data", "bulletin.json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(args.bulletin):
        log("LOI: thieu %s" % args.bulletin)
        return 50
    with open(args.bulletin, encoding="utf-8") as fh:
        bulletin = json.load(fh)

    head, stats, highlights = build_lines(bulletin)
    log("Gui thong bao: %s" % head)
    results = [send_telegram(head, stats, highlights, args.dry_run),
               send_zalo(head, stats, highlights, args.dry_run)]

    if all(r is None for r in results):
        log("LOI: chua cau hinh kenh nao (dat bien moi truong trong Environment settings)")
        return 51
    return 0 if any(r is True for r in results) else 50


if __name__ == "__main__":
    sys.exit(main())
