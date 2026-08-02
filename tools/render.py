#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render bulletin.json + template.html -> index.html và archive/<ngày>.html

Script chịu trách nhiệm: đánh số, sắp xếp mới->cũ, escape HTML, gắn URL và giờ
đăng lấy từ raw_feed.json. Claude không can thiệp vào khâu này.

Dùng:  python3 tools/render.py [--bulletin ...] [--raw ...] [--template ...]
Exit:  0 = OK · 30 = lỗi render · 31 = thiếu file/marker
"""
import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

VN = timezone(timedelta(hours=7))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
MARKERS = ["TITLE", "DATELINE", "TILES", "DOMESTIC", "SUMMARY", "WORLD",
           "VIETNAM", "LEGAL", "DIVIDENDS", "PREVLINK"]


def esc(text):
    return html.escape(str(text if text is not None else ""), quote=True)


def fmt_pct(value):
    if value is None:
        return "delta flat", "• 0,00%"
    txt = ("%.2f" % abs(value)).replace(".", ",")
    if value > 0:
        return "delta up", "▲ +%s%%" % txt
    if value < 0:
        return "delta down", "▼ −%s%%" % txt
    return "delta flat", "• 0,00%"


def fmt_time(published_at, bulletin_date):
    """Giờ hiển thị: 'HH:MM' nếu cùng ngày, ngược lại 'HH:MM DD/MM'."""
    if not published_at:
        return ""
    try:
        dt = datetime.fromisoformat(published_at).astimezone(VN)
    except ValueError:
        return ""
    if dt.strftime("%Y-%m-%d") == bulletin_date:
        return dt.strftime("%H:%M")
    return dt.strftime("%H:%M %d/%m")


def replace_block(doc, name, content):
    pattern = re.compile(r"(<!-- BEGIN:%s -->)(.*?)(<!-- END:%s -->)" % (name, name), re.S)
    if not pattern.search(doc):
        raise KeyError("template thiếu marker %s" % name)
    return pattern.sub(lambda m: m.group(1) + "\n" + content + "\n" + m.group(3), doc, count=1)


def news_li(number, title, url, time_text, legal=False):
    label = ("🚨 " if legal else "") + esc(title)
    body = '<a href="%s">%s</a>' % (esc(url), label) if url else label
    meta = '<div class="meta">%s</div>' % esc(time_text) if time_text else ""
    return ('<li%s><span class="n">%d.</span><div class="t">%s%s</div></li>'
            % (' class="legal"' if legal else "", number, body, meta))


def render_source_blocks(blocks, index, bulletin_date, legal=False):
    out = []
    for block in blocks or []:
        items = []
        for item in block.get("items") or []:
            src = index.get(item.get("ref"))
            if not src:
                continue
            items.append((src.get("published_at") or "", item, src))
        items.sort(key=lambda pair: pair[0], reverse=True)
        if not items:
            continue
        out.append('<h3 class="src">%s</h3>' % esc(block.get("source")))
        out.append('<ol class="news">')
        for pos, (_, item, src) in enumerate(items, start=1):
            out.append("  " + news_li(pos, item.get("title"), src.get("url"),
                                      fmt_time(src.get("published_at"), bulletin_date), legal))
        out.append("</ol>")
    return "\n".join(out) if out else '<div class="notice">Không có tin mới đạt tiêu chí.</div>'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bulletin", default=os.path.join(ROOT, "data", "bulletin.json"))
    ap.add_argument("--raw", default=os.path.join(ROOT, "data", "raw_feed.json"))
    ap.add_argument("--template", default=os.path.join(ROOT, "template.html"))
    ap.add_argument("--outdir", default=ROOT)
    args = ap.parse_args()

    for path in (args.bulletin, args.raw, args.template):
        if not os.path.isfile(path):
            print("LOI: thiếu file %s" % path)
            return 31
    with open(args.bulletin, encoding="utf-8") as fh:
        bulletin = json.load(fh)
    with open(args.raw, encoding="utf-8") as fh:
        raw = json.load(fh)
    with open(args.template, encoding="utf-8") as fh:
        doc = fh.read()

    index = {item["id"]: item for item in raw.get("items", [])}
    date_str = bulletin["date"]
    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    updated = datetime.now(VN).strftime("%H:%M")
    pretty = "%s, %s" % (WEEKDAYS[day.weekday()], day.strftime("%d/%m/%Y"))
    sunday = bulletin.get("edition") == "sunday"
    md = bulletin.get("market_data") or {}

    try:
        doc = replace_block(doc, "TITLE", "Bản tin Chứng khoán Sáng — %s" % day.strftime("%d/%m/%Y"))
        doc = replace_block(doc, "DATELINE",
                            '%s · <span class="updated">cập nhật %s</span>' % (pretty, updated))

        if sunday:
            notice = ('<div class="notice">Ấn bản Chủ Nhật rút gọn — chỉ tin thế giới.</div>')
            doc = replace_block(doc, "TILES", "")
            doc = replace_block(doc, "DOMESTIC", "")
            doc = replace_block(doc, "SUMMARY", notice)
            doc = replace_block(doc, "VIETNAM", notice)
            doc = replace_block(doc, "LEGAL", notice)
            doc = replace_block(doc, "DIVIDENDS", "")
        else:
            tiles = []
            for tile in md.get("tiles") or []:
                cls, text = fmt_pct(tile.get("change_pct"))
                tiles.append(
                    '<div class="tile"><div class="label">%s</div>'
                    '<div class="value">%s</div><div class="%s">%s</div></div>'
                    % (esc(tile.get("label")), esc(tile.get("value")), cls, text))
            doc = replace_block(doc, "TILES", "\n".join(tiles))

            rows = []
            for row in md.get("domestic") or []:
                cls = {"up": "num chg-up", "down": "num chg-down"}.get(
                    row.get("direction"), "num")
                arrow = {"up": "▲ ", "down": "▼ "}.get(row.get("direction"), "")
                change = row.get("change") or ""
                rows.append('<tr><td>%s</td><td class="num">%s</td><td class="%s">%s</td></tr>'
                            % (esc(row.get("label")), esc(row.get("value")), cls,
                               (arrow + esc(change)) if change else ""))
            doc = replace_block(doc, "DOMESTIC", "\n".join(rows))
            doc = replace_block(doc, "SUMMARY", "<p>%s</p>" % esc(md.get("summary")))
            doc = replace_block(doc, "VIETNAM",
                                render_source_blocks(bulletin.get("vietnam"), index, date_str))
            doc = replace_block(doc, "LEGAL",
                                render_source_blocks(bulletin.get("legal"), index, date_str,
                                                     legal=True))
            divs = []
            for item in bulletin.get("dividends") or []:
                rec = datetime.strptime(item["record_date"], "%Y-%m-%d").strftime("%d/%m")
                divs.append('<tr><td><b>%s</b></td><td>%s</td><td class="num">%s</td></tr>'
                            % (esc(item["ticker"]), esc(item["event"]), rec))
            doc = replace_block(doc, "DIVIDENDS", "\n".join(divs) if divs else
                                '<tr><td colspan="3">Không có mã nào chốt quyền trong 10 ngày tới.</td></tr>')

        world_items = []
        for item in bulletin.get("world") or []:
            src = index.get(item.get("ref"))
            if not src:
                continue
            world_items.append((src.get("published_at") or "", item, src))
        world_items.sort(key=lambda pair: pair[0], reverse=True)
        world_html = ['<ol class="news">']
        for pos, (_, item, src) in enumerate(world_items, start=1):
            title = item.get("title")
            if item.get("translated"):
                title = "%s (dịch)" % title
            meta = "%s · %s" % (src.get("source"), fmt_time(src.get("published_at"), date_str))
            world_html.append(
                '  <li><span class="n">%d.</span><div class="t">%s<div class="meta">%s</div>'
                "</div></li>" % (pos, esc(title), esc(meta.strip(" ·"))))
        world_html.append("</ol>")
        doc = replace_block(doc, "WORLD", "\n".join(world_html))

        prev = day - timedelta(days=1)
        prev_file = os.path.join(args.outdir, "archive", "%s.html" % prev.isoformat())
        if os.path.isfile(prev_file):
            link = ('<a class="prev-link" href="archive/%s.html">← Bản tin hôm trước</a>'
                    % prev.isoformat())
        else:
            link = '<a class="prev-link" href="archive/">← Kho bản tin cũ</a>'
        doc = replace_block(doc, "PREVLINK", link)
    except (KeyError, ValueError, TypeError) as exc:
        print("LOI render: %s" % exc)
        return 30

    index_path = os.path.join(args.outdir, "index.html")
    archive_path = os.path.join(args.outdir, "archive", "%s.html" % date_str)
    os.makedirs(os.path.dirname(archive_path), exist_ok=True)
    for path in (index_path, archive_path):
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(doc)

    audit_path = os.path.join(args.outdir, "archive", "data", "%s.json" % date_str)
    os.makedirs(os.path.dirname(audit_path), exist_ok=True)
    with open(audit_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(bulletin, fh, ensure_ascii=False, indent=1)

    vn_n = sum(len(b.get("items") or []) for b in (bulletin.get("vietnam") or []))
    legal_n = sum(len(b.get("items") or []) for b in (bulletin.get("legal") or []))
    print("Render xong: %s · the gioi %d · Viet Nam %d · phap ly %d"
          % (date_str, len(world_items), vn_n, legal_n))
    print("   %s" % index_path)
    print("   %s" % archive_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
