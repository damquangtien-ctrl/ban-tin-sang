#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thu thập tin THÔ từ toàn bộ nguồn -> data/raw_feed.json

Script này KHÔNG chọn lọc, KHÔNG diễn giải. Nó chỉ lấy nguyên trạng:
tiêu đề gốc, URL gốc, thời gian đăng gốc. Claude chọn tin bằng cách
tham chiếu `id` của item — nhờ vậy URL và timestamp không bao giờ bị bịa.

Dùng:  python3 tools/fetch_feeds.py [--hours 24] [--out data/raw_feed.json]
Exit:  0 = OK · 10 = không nguồn nào trả về tin · 11 = lỗi ghi file
"""
import argparse
import concurrent.futures as cf
import gzip
import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

VN = timezone(timedelta(hours=7))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# (tên nguồn hiển thị, handle kênh, số lần phân trang)
TELEGRAM_SOURCES = [
    ("Market News Feed", "marketfeed", 6),
    ("VN Wall Street", "vnwallstreet", 3),
    ("Dubaotiente", "dubaotiente", 2),
]

# (tên nguồn hiển thị, url feed, phân loại: vn | legal | dividend | world)
RSS_SOURCES = [
    ("CafeF", "https://cafef.vn/thi-truong-chung-khoan.rss", "vn"),
    ("CafeF", "https://cafef.vn/doanh-nghiep.rss", "vn"),
    # Bốn feed chuyên mục hẹp (bám sát KQKD / nội bộ / M&A / nhận định)
    ("Vietstock", "https://vietstock.vn/737/doanh-nghiep/hoat-dong-kinh-doanh.rss", "vn"),
    ("Vietstock", "https://vietstock.vn/739/chung-khoan/giao-dich-noi-bo.rss", "vn"),
    ("Vietstock", "https://vietstock.vn/764/doanh-nghiep/tang-von-m-a.rss", "vn"),
    ("Vietstock", "https://vietstock.vn/1636/nhan-dinh-phan-tich/nhan-dinh-thi-truong.rss", "vn"),
    # Ba feed "mới cập nhật" — tươi hơn hẳn và phủ rộng hơn bốn feed trên.
    # /1/ tổng hợp (có bản tin trước giờ giao dịch), /3/ doanh nghiệp, /6/ tài chính-ngân hàng.
    # Các feed này lặp bài trong chính nó; fetch tự khử trùng theo (nguồn + link).
    # KHÔNG lấy /2/ "Hàng hóa": chỉ là tin giá vàng/dầu, đã có ở Phần I số liệu.
    ("Vietstock", "https://vietstock.vn/1/moi-cap-nhat.rss", "vn"),
    ("Vietstock", "https://vietstock.vn/3/moi-cap-nhat.rss", "vn"),
    ("Vietstock", "https://vietstock.vn/6/moi-cap-nhat.rss", "vn"),
    ("VnEconomy", "https://vneconomy.vn/chung-khoan.rss", "vn"),
    ("Tin nhanh Chứng khoán",
     "https://news.google.com/rss/search?q=site:tinnhanhchungkhoan.vn&hl=vi&gl=VN&ceid=VN:vi", "vn"),
    ("Báo Đầu tư",
     "https://news.google.com/rss/search?q=site:baodautu.vn&hl=vi&gl=VN&ceid=VN:vi", "vn"),
    ("MarketTimes", "https://markettimes.vn/rss/tai-chinh", "vn"),
    ("VnBusiness", "https://vnbusiness.vn/rss/feed.rss", "vn"),
    ("Tiền Phong", "https://tienphong.vn/rss/kinh-te-3.rss", "vn"),
    ("Thời báo Ngân hàng",
     "https://news.google.com/rss/search?q=site:thoibaonganhang.vn&hl=vi&gl=VN&ceid=VN:vi", "vn"),
    ("BNEWS", "https://news.google.com/rss/search?q=site:bnews.vn&hl=vi&gl=VN&ceid=VN:vi", "vn"),
    ("Google News Pháp lý",
     "https://news.google.com/rss/search?q=%22kh%E1%BB%9Fi+t%E1%BB%91%22+OR+%22thanh+tra%22+OR+%22b%E1%BA%AFt+t%E1%BA%A1m+giam%22+OR+%22x%E1%BB%AD+ph%E1%BA%A1t%22+doanh+nghi%E1%BB%87p&hl=vi&gl=VN&ceid=VN:vi",
     "legal"),
    ("Google News Pháp lý",
     "https://news.google.com/rss/search?q=%22kh%E1%BB%9Fi+t%E1%BB%91%22+OR+%22thao+t%C3%BAng%22+OR+%22x%E1%BB%AD+ph%E1%BA%A1t%22+%22c%E1%BB%95+phi%E1%BA%BFu%22&hl=vi&gl=VN&ceid=VN:vi",
     "legal"),
    ("VnExpress", "https://vnexpress.net/rss/phap-luat.rss", "legal"),
    ("Báo Thanh tra",
     "https://news.google.com/rss/search?q=site:thanhtra.com.vn&hl=vi&gl=VN&ceid=VN:vi", "legal"),
    ("Vietstock", "https://vietstock.vn/738/doanh-nghiep/co-tuc.rss", "dividend"),
    # Hai feed quốc tế Bloomberg (markets + economics) cùng source "Bloomberg":
    # bài đăng ở cả hai feed có cùng link -> cùng id (slug nguồn + link) -> tự khử
    # trùng chéo ở vòng dedup cuối. Feed trả 301, urlopen mặc định tự theo redirect.
    ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss", "world"),
    ("Bloomberg", "https://feeds.bloomberg.com/economics/news.rss", "world"),
]

# (tên nguồn hiển thị, url news-sitemap, phân loại) — Reuters KHÔNG có RSS công khai,
# chỉ có news-sitemap (định dạng khác RSS, ~5 tin/lần là bình thường).
SITEMAP_SOURCES = [
    ("Reuters", "https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml", "world"),
]

HSX_SOURCES = [
    ("HOSE", "https://api.hsx.vn/n/api/v1/News/NewsByCateFeed/21", "vn"),
    ("HOSE", "https://api.hsx.vn/n/api/v1/News/NewsByCateFeed/11", "vn"),
]

# Tin HOSE cần loại ngay từ khâu thu thập (rác cố định, không phải việc của Claude)
HOSE_DROP = re.compile(r"chứng quyền|FUE[A-Z0-9]*|E1VFVN30|cơ cấu hoán đổi", re.I)

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def log(msg):
    print(msg, flush=True)


def clean_text(raw, limit=280):
    txt = raw.replace("<br/>", " ").replace("<br>", " ").replace("<br />", " ")
    # Bóc thẻ hai lượt: một số feed (HOSE) bọc tiêu đề trong thẻ ĐÃ escape
    # (&lt;span&gt;...), nếu chỉ unescape thì thẻ HTML thật lọt vào tiêu đề.
    txt = html.unescape(TAG_RE.sub(" ", txt))
    txt = TAG_RE.sub(" ", txt)
    txt = WS_RE.sub(" ", txt).strip()
    return txt[:limit]


def keep_item(source, title):
    """Lọc rác cố định ngay từ khâu thu thập, không để Claude phải xử."""
    if source == "HOSE" and HOSE_DROP.search(title):
        return False
    return True


def http_get(url, timeout=45):
    """Trả về (text, http_status). urlopen mặc định tự theo redirect 301/302
    (Bloomberg cần điều này), nên status là của response CUỐI sau redirect."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        status = r.status
        data = r.read()
    # Vài server (tienphong.vn) trả gzip dù ta không gửi Accept-Encoding — nhận
    # diện theo magic bytes 1f 8b và tự giải nén, nếu hỏng thì giữ nguyên data.
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError:
            pass
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return data.decode(enc), status
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace"), status


def short_endpoint(url):
    """Rút gọn endpoint còn host+path để ghi vào sources_health."""
    p = urllib.parse.urlsplit(url)
    return p.netloc + p.path


def stamp_kind(items, kind):
    """Gắn source_kind bắt buộc cho mọi item (hợp đồng Gói 4): web | telegram | api."""
    for item in items:
        item["source_kind"] = kind
    return items


def health_row(source, url, status, items, error):
    """Một dòng telemetry sources_health cho mỗi endpoint đã fetch.
    http_status = int (kể cả 4xx/5xx) hoặc None nếu lỗi mạng trước khi có response."""
    newest = max((i["published_at"] for i in items if i["published_at"]), default=None)
    return {
        "source": source,
        "endpoint": short_endpoint(url),
        "http_status": status,
        "items": len(items),
        "newest_at": newest,
        "error": error,
    }


def parse_date(value):
    """Trả về ISO 8601 theo giờ VN, hoặc None nếu không đọc được."""
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=VN)
            return dt.astimezone(VN).isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    iso = value.replace("Z", "+00:00")
    for candidate in (iso, iso[:19], iso[:10]):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=VN)
            return dt.astimezone(VN).isoformat()
        except ValueError:
            continue
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})[ T]*(\d{1,2}):(\d{2})", value)
    if m:
        d, mo, y, h, mi = (int(x) for x in m.groups())
        try:
            return datetime(y, mo, d, h, mi, tzinfo=VN).isoformat()
        except ValueError:
            return None
    return None


def make_id(prefix, key):
    return "%s:%s" % (prefix, hashlib.sha1(key.encode("utf-8")).hexdigest()[:10])


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "src"


def field(block, tag):
    m = re.search(r"<%s[^>]*>(.*?)</%s>" % (tag, tag), block, re.S | re.I)
    if not m:
        return ""
    val = m.group(1).strip()
    cd = re.match(r"^<!\[CDATA\[(.*?)\]\]>$", val, re.S)
    if cd:
        val = cd.group(1).strip()
    return val


def parse_rss(text, source, category):
    items = []
    blocks = re.findall(r"<item\b(.*?)</item>", text, re.S | re.I)
    if not blocks:
        blocks = re.findall(r"<entry\b(.*?)</entry>", text, re.S | re.I)
    for blk in blocks:
        title = clean_text(field(blk, "title"), 300)
        link = field(blk, "link") or field(blk, "guid") or field(blk, "id")
        if not link:
            m = re.search(r'<link[^>]*href="([^"]+)"', blk, re.I)
            link = m.group(1) if m else ""
        link = html.unescape(clean_text(link, 600))
        # a10:updated là dạng Atom mà feed api.hsx.vn dùng — thiếu nó thì mọi tin
        # HOSE không có giờ đăng và bị loại khỏi bản tin.
        published = parse_date(field(blk, "pubDate") or field(blk, "published")
                               or field(blk, "updated") or field(blk, "a10:updated")
                               or field(blk, "dc:date"))
        if not title or not link.startswith("http"):
            continue
        if not keep_item(source, title):
            continue
        items.append({
            "id": make_id(slug(source), link),
            "source": source,
            "category": category,
            "title": title,
            "url": link,
            "published_at": published,
        })
    return items


def parse_news_sitemap(text, source, category):
    """Đọc news-sitemap của Reuters — KHÔNG phải RSS: mỗi block <url> có
    <loc> (permalink), <news:title> (CDATA, field() đã bóc) và
    <news:publication_date> (ISO, parse_date hiện có đọc được)."""
    items = []
    for blk in re.findall(r"<url\b[^>]*>(.*?)</url>", text, re.S | re.I):
        title = clean_text(field(blk, "news:title"), 300)
        link = html.unescape(clean_text(field(blk, "loc"), 600))
        published = parse_date(field(blk, "news:publication_date"))
        if not title or not link.startswith("http"):
            continue
        if not keep_item(source, title):
            continue
        items.append({
            "id": make_id(slug(source), link),
            "source": source,
            "category": category,
            "title": title,
            "url": link,
            "published_at": published,
        })
    return items


def parse_hsx(text, source, category):
    items = []
    payload = None
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
    if payload is None:
        return parse_rss(text, source, category)
    rows = payload if isinstance(payload, list) else None
    if rows is None:
        for key in ("data", "items", "rows", "result", "value"):
            val = payload.get(key) if isinstance(payload, dict) else None
            if isinstance(val, list):
                rows = val
                break
            if isinstance(val, dict):
                for k2 in ("data", "items", "rows"):
                    if isinstance(val.get(k2), list):
                        rows = val[k2]
                        break
            if rows:
                break
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        title = ""
        url = ""
        published = None
        for k, v in row.items():
            kl = k.lower()
            if not isinstance(v, str):
                continue
            if not title and ("title" in kl or "subject" in kl or kl == "name"):
                title = clean_text(v, 300)
            elif not url and ("url" in kl or "link" in kl) and v.startswith("http"):
                url = v
            elif published is None and ("date" in kl or "time" in kl):
                published = parse_date(v)
        if not title or not keep_item(source, title):
            continue
        if not url:
            url = "https://www.hsx.vn/Modules/Cms/Web/NewsList"
        items.append({
            "id": make_id(slug(source), title),
            "source": source,
            "category": category,
            "title": title,
            "url": url,
            "published_at": published,
        })
    return items


def parse_telegram(text, source, handle):
    items = []
    chunks = text.split('<div class="tgme_widget_message_wrap')[1:]
    for blk in chunks:
        mid = re.search(r'data-post="[^"/]+/(\d+)"', blk)
        mtime = re.search(r'<time[^>]+datetime="([^"]+)"', blk)
        mtext = re.search(r'js-message_text[^>]*>(.*?)</div>', blk, re.S)
        if not mtext:
            mtext = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', blk, re.S)
        if not (mid and mtext):
            continue
        body = clean_text(mtext.group(1), 400)
        if len(body) < 15:
            continue
        items.append({
            "id": "tg-%s:%s" % (handle, mid.group(1)),
            "source": source,
            "category": "world",
            "title": body,
            "url": "https://t.me/s/%s" % handle,
            "published_at": parse_date(mtime.group(1)) if mtime else None,
            "post_id": int(mid.group(1)),
        })
    return items


def fetch_http_source(entry, parser, kind):
    """Fetch một endpoint HTTP rồi parse; trả (items, health_row, error).
    Dùng chung cho RSS (web), news-sitemap (web) và HSX (api)."""
    source, url, category = entry
    status, err = None, None
    try:
        text, status = http_get(url)
        got = parser(text, source, category)
    except Exception as exc:  # noqa: BLE001 - nguồn lỗi thì bỏ qua, không dừng pipeline
        if isinstance(exc, urllib.error.HTTPError):
            status = exc.code
        got, err = [], "%s (%s): %s" % (source, url[:60], str(exc)[:120])
    stamp_kind(got, kind)
    return got, health_row(source, url, status, got, err), err


def fetch_telegram_source(entry):
    source, handle, pages = entry
    base = "https://t.me/s/%s" % handle
    collected, errors, before, status = [], None, None, None
    try:
        for _ in range(max(1, pages)):
            url = base
            if before:
                url += "?before=%d" % before
            text, status = http_get(url)
            batch = parse_telegram(text, source, handle)
            if not batch:
                break
            collected.extend(batch)
            before = min(i["post_id"] for i in batch)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, urllib.error.HTTPError):
            status = exc.code
        errors = "%s (t.me/%s): %s" % (source, handle, str(exc)[:120])
    for item in collected:
        item.pop("post_id", None)
    stamp_kind(collected, "telegram")
    return collected, health_row(source, base, status, collected, errors), errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24,
                    help="cửa sổ độ tươi ghi vào metadata (24 thường, 48 cho T7/CN/T2)")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "raw_feed.json"))
    args = ap.parse_args()

    now = datetime.now(VN)
    items, errors, sources_health = [], [], []

    jobs = []
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        for entry in RSS_SOURCES:
            jobs.append(pool.submit(fetch_http_source, entry, parse_rss, "web"))
        for entry in SITEMAP_SOURCES:
            jobs.append(pool.submit(fetch_http_source, entry, parse_news_sitemap, "web"))
        for entry in HSX_SOURCES:
            jobs.append(pool.submit(fetch_http_source, entry, parse_hsx, "api"))
        for entry in TELEGRAM_SOURCES:
            jobs.append(pool.submit(fetch_telegram_source, entry))
        for job in jobs:
            got, health, err = job.result()
            items.extend(got)
            sources_health.append(health)
            if err:
                errors.append(err)

    seen, unique = set(), []
    for item in items:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        unique.append(item)
    unique.sort(key=lambda i: i["published_at"] or "", reverse=True)

    per_source = {}
    for item in unique:
        per_source[item["source"]] = per_source.get(item["source"], 0) + 1

    payload = {
        "fetched_at": now.isoformat(),
        "freshness_hours": args.hours,
        "cutoff": (now - timedelta(hours=args.hours)).isoformat(),
        "counts": {"total": len(unique), "per_source": per_source},
        # Telemetry mỗi endpoint (hợp đồng Gói 4 mục 6) — nguồn lỗi vẫn có dòng riêng
        "sources_health": sources_health,
        "errors": errors,
        "items": unique,
    }

    try:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
    except OSError as exc:
        log("LOI ghi file: %s" % exc)
        return 11

    log("Thu thap xong: %d tin tu %d nguon, %d nguon loi -> %s"
        % (len(unique), len(per_source), len(errors), args.out))
    for src in sorted(per_source, key=per_source.get, reverse=True):
        log("   %-26s %d" % (src, per_source[src]))
    for err in errors:
        log("   LOI: %s" % err)

    return 0 if unique else 10


if __name__ == "__main__":
    sys.exit(main())
