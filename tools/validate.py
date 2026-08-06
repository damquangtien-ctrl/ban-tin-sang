#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kiểm định bulletin.json trước khi render. Không đạt là KHÔNG xuất bản.

Kiểm tra bắt buộc:
  C1  Cấu trúc theo schema.json (kiểu, trường bắt buộc, min/max)
  C2  Tham chiếu: mọi ref phải tồn tại trong raw_feed.json
  C3  Timestamp: tin phải có thời gian đăng và nằm trong cửa sổ độ tươi
  C4  URL: hợp lệ, đúng host cho phép, không placeholder
  C5  Placeholder: không còn chữ mẫu/TODO/[...] trong mọi trường text
  C6  Số lượng: 8 ô chỉ số đúng thứ tự, thế giới <=30, pháp lý <=10
  C7  Trùng sự kiện: trùng URL hoặc tiêu đề gần giống nhau
  C8  An toàn HTML: text không được chứa thẻ HTML thô
  C9  Nguồn: tên báo nằm trong danh sách hợp lệ, thứ tự khối đúng quy ước

Dùng:  python3 tools/validate.py [--bulletin data/bulletin.json] [--raw data/raw_feed.json]
Exit:  0 = đạt · 20 = có lỗi · 21 = thiếu file đầu vào
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

VN = timezone(timedelta(hours=7))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TILE_ORDER = ["S&P 500", "Dow Jones", "Nasdaq", "Dầu WTI", "Dầu Brent",
              "Vàng thế giới", "Bitcoin", "DXY"]

# Thứ tự khối báo trong nước (báo phổ biến trước) - dùng cho cả phần III và IV
SOURCE_ORDER = ["CafeF", "Vietstock", "VnEconomy", "Tin nhanh Chứng khoán", "Báo Đầu tư",
                "MarketTimes", "Bloomberg Businessweek VN", "VnBusiness", "Tiền Phong",
                "Thời báo Ngân hàng", "BNEWS", "HOSE"]
# Báo chỉ xuất hiện ở phần pháp lý, xếp sau các báo trên
LEGAL_EXTRA_SOURCES = ["VnExpress", "Báo Thanh tra", "Google News Pháp lý",
                       "Thanh Niên", "Tuổi Trẻ", "Dân Trí", "Thương Trường"]
WORLD_SOURCES = ["Bloomberg", "Reuters", "Bloomberg Businessweek VN",
                 "Market News Feed", "VN Wall Street", "Dubaotiente"]

ALLOWED_HOSTS = {
    "cafef.vn", "vietstock.vn", "vneconomy.vn", "markettimes.vn", "vnbusiness.vn",
    "tienphong.vn", "vnexpress.net", "thanhtra.com.vn", "baodautu.vn",
    "tinnhanhchungkhoan.vn", "thoibaonganhang.vn", "bnews.vn", "news.google.com",
    "api.hsx.vn", "www.hsx.vn", "hsx.vn", "t.me", "thanhnien.vn", "tuoitre.vn",
    "dantri.com.vn", "thuongtruong.com.vn",
    # Gói 4: nguồn thế giới có link bài gốc
    "bloomberg.com", "www.bloomberg.com", "reuters.com", "www.reuters.com",
    # Bloomberg Businessweek VN — bản dịch Bloomberg có bản quyền, link tiếng Việt
    "bbw.vn", "www.bbw.vn",
}

PLACEHOLDER_RE = re.compile(
    r"(lorem ipsum|\bTODO\b|\bTBD\b|\bXXX\b|example\.com|\[tiêu đề|\[số\]|\[nguồn\]|"
    r"\[giờ\]|\[link|\{\{|\}\}|placeholder|dữ liệu mẫu|công ty [XYZW]\b|doanh nghiệp Z\b)",
    re.I)
HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
STOPWORDS = {"của", "và", "các", "trong", "cho", "với", "được", "một", "này", "đã",
             "là", "có", "từ", "tại", "về", "the", "a", "of", "to", "in", "on", "for"}

errors = []
warnings = []


def err(code, msg):
    errors.append("[%s] %s" % (code, msg))


def warn(code, msg):
    warnings.append("[%s] %s" % (code, msg))


# ---------- C1: schema ----------
def check_type(value, spec, path):
    types = spec.get("type")
    if types is None:
        return True
    if isinstance(types, str):
        types = [types]
    ok = False
    for t in types:
        if t == "object" and isinstance(value, dict):
            ok = True
        elif t == "array" and isinstance(value, list):
            ok = True
        elif t == "string" and isinstance(value, str):
            ok = True
        elif t == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            ok = True
        elif t == "integer" and isinstance(value, int) and not isinstance(value, bool):
            ok = True
        elif t == "boolean" and isinstance(value, bool):
            ok = True
        elif t == "null" and value is None:
            ok = True
    if not ok:
        err("C1", "%s: sai kiểu, cần %s" % (path, "/".join(types)))
    return ok


def validate_schema(node, spec, path, root_spec):
    if "$ref" in spec:
        ref = spec["$ref"].lstrip("#/").split("/")
        target = root_spec
        for part in ref:
            target = target[part]
        return validate_schema(node, target, path, root_spec)
    if not check_type(node, spec, path):
        return
    if isinstance(node, dict):
        for key in spec.get("required", []):
            if key not in node:
                err("C1", "%s: thiếu trường bắt buộc '%s'" % (path, key))
        props = spec.get("properties", {})
        if spec.get("additionalProperties") is False:
            for key in node:
                if key not in props:
                    err("C1", "%s: trường lạ '%s'" % (path, key))
        for key, sub in props.items():
            if key in node:
                validate_schema(node[key], sub, "%s.%s" % (path, key), root_spec)
    elif isinstance(node, list):
        if "minItems" in spec and len(node) < spec["minItems"]:
            err("C1", "%s: cần tối thiểu %d phần tử, có %d" % (path, spec["minItems"], len(node)))
        if "maxItems" in spec and len(node) > spec["maxItems"]:
            err("C1", "%s: vượt trần %d phần tử (có %d)" % (path, spec["maxItems"], len(node)))
        if "items" in spec:
            for idx, sub in enumerate(node):
                validate_schema(sub, spec["items"], "%s[%d]" % (path, idx), root_spec)
    elif isinstance(node, str):
        if "minLength" in spec and len(node) < spec["minLength"]:
            err("C1", "%s: chuỗi quá ngắn (%d < %d)" % (path, len(node), spec["minLength"]))
        if "maxLength" in spec and len(node) > spec["maxLength"]:
            err("C1", "%s: chuỗi quá dài (%d > %d)" % (path, len(node), spec["maxLength"]))
        if "pattern" in spec and not re.search(spec["pattern"], node):
            err("C1", "%s: không khớp định dạng %s" % (path, spec["pattern"]))
        if "enum" in spec and node not in spec["enum"]:
            err("C1", "%s: giá trị '%s' ngoài danh sách %s" % (path, node, spec["enum"]))
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        if "minimum" in spec and node < spec["minimum"]:
            err("C1", "%s: %s nhỏ hơn mức tối thiểu %s" % (path, node, spec["minimum"]))
        if "maximum" in spec and node > spec["maximum"]:
            err("C1", "%s: %s lớn hơn mức tối đa %s" % (path, node, spec["maximum"]))


def strip_comments(node):
    """Bỏ mọi khoá bắt đầu bằng '_' (ghi chú của bản nháp) trước khi kiểm định."""
    if isinstance(node, dict):
        found = [k for k in node if k.startswith("_")]
        for key in found:
            node.pop(key)
        for value in node.values():
            found += strip_comments(value)
        return found
    if isinstance(node, list):
        found = []
        for value in node:
            found += strip_comments(value)
        return found
    return []


def iter_news(bulletin):
    """Duyệt mọi tin: (đường dẫn, item, tên nguồn, phần)."""
    for idx, item in enumerate(bulletin.get("world") or []):
        yield "world[%d]" % idx, item, None, "world"
    for section in ("vietnam", "legal"):
        for bi, block in enumerate(bulletin.get(section) or []):
            if not isinstance(block, dict):
                continue
            for ii, item in enumerate(block.get("items") or []):
                yield ("%s[%d].items[%d]" % (section, bi, ii), item,
                       block.get("source"), section)


def norm_tokens(title):
    txt = re.sub(r"[^\w\sÀ-ỹ]", " ", (title or "").lower())
    return {t for t in txt.split() if len(t) > 2 and t not in STOPWORDS}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bulletin", default=os.path.join(ROOT, "data", "bulletin.json"))
    ap.add_argument("--raw", default=os.path.join(ROOT, "data", "raw_feed.json"))
    ap.add_argument("--schema", default=os.path.join(ROOT, "tools", "schema.json"))
    args = ap.parse_args()

    for path in (args.bulletin, args.raw, args.schema):
        if not os.path.isfile(path):
            print("LOI: thiếu file %s" % path)
            return 21
    with open(args.bulletin, encoding="utf-8") as fh:
        bulletin = json.load(fh)
    with open(args.raw, encoding="utf-8") as fh:
        raw = json.load(fh)
    with open(args.schema, encoding="utf-8") as fh:
        schema = json.load(fh)

    index = {item["id"]: item for item in raw.get("items", [])}
    sunday = bulletin.get("edition") == "sunday"

    leftovers = strip_comments(bulletin)
    if leftovers:
        warn("C1", "còn %d khoá ghi chú của bản nháp (%s), đã bỏ qua khi render"
             % (len(leftovers), ", ".join(sorted(set(leftovers))[:4])))

    # C1
    validate_schema(bulletin, schema, "bulletin", schema)

    # C6 - ô chỉ số
    tiles = (bulletin.get("market_data") or {}).get("tiles") or []
    for pos, expected in enumerate(TILE_ORDER):
        if pos >= len(tiles):
            err("C6", "thiếu ô chỉ số '%s' (vị trí %d)" % (expected, pos + 1))
        elif tiles[pos].get("label") != expected:
            err("C6", "ô chỉ số vị trí %d phải là '%s', đang là '%s'"
                % (pos + 1, expected, tiles[pos].get("label")))
    legal_total = sum(len(b.get("items") or []) for b in (bulletin.get("legal") or []))
    if legal_total > 10:
        err("C6", "phần pháp lý %d tin, vượt trần 10" % legal_total)
    if not sunday and not (bulletin.get("vietnam") or []):
        err("C6", "phần Việt Nam trống (chỉ ấn bản Chủ Nhật mới được trống)")

    # C9 - nguồn và thứ tự khối
    for section, extra in (("vietnam", []), ("legal", LEGAL_EXTRA_SOURCES)):
        allowed = SOURCE_ORDER + extra
        ranks = []
        for bi, block in enumerate(bulletin.get(section) or []):
            name = block.get("source") if isinstance(block, dict) else None
            if name not in allowed:
                err("C9", "%s[%d]: nguồn '%s' không nằm trong danh sách hợp lệ"
                    % (section, bi, name))
                continue
            ranks.append((allowed.index(name), name))
        for i in range(1, len(ranks)):
            if ranks[i][0] < ranks[i - 1][0]:
                err("C9", "%s: khối '%s' phải đứng trước '%s' theo thứ tự báo phổ biến"
                    % (section, ranks[i][1], ranks[i - 1][1]))
                break
    for idx, item in enumerate(bulletin.get("world") or []):
        src = index.get(item.get("ref"), {}).get("source")
        if src and src not in WORLD_SOURCES:
            warn("C9", "world[%d]: tin lấy từ nguồn trong nước '%s'" % (idx, src))

    # C2, C3, C4, C7, C8 trên từng tin
    cutoff = None
    try:
        cutoff = datetime.fromisoformat(raw["cutoff"])
    except (KeyError, ValueError):
        warn("C3", "raw_feed.json thiếu mốc cutoff, bỏ qua kiểm tra độ tươi")
    # Gói 1 (Codex 4.3): giờ đăng "đến từ tương lai" là dữ liệu hỏng (đồng hồ nguồn
    # sai hoặc feed ghi giờ dự kiến) — quá 30 phút so với lúc kiểm định thì chặn.
    future_gate = datetime.now(VN) + timedelta(minutes=30)
    try:
        fetched = datetime.fromisoformat(raw["fetched_at"])
        if datetime.now(VN) - fetched > timedelta(hours=12):
            warn("C3", "raw_feed.json thu thập lúc %s — quá cũ so với lúc kiểm định, "
                 "cửa sổ độ tươi vẫn neo theo lúc fetch" % raw["fetched_at"][:16])
    except (KeyError, ValueError):
        pass
    seen_urls, titles = {}, []

    for path, item, block_source, section in iter_news(bulletin):
        if not isinstance(item, dict):
            continue
        ref, title = item.get("ref"), item.get("title") or ""
        src = index.get(ref)
        if src is None:
            err("C2", "%s: ref '%s' không có trong raw_feed.json (tin bịa hoặc sai id)"
                % (path, ref))
            continue
        if block_source and src["source"] != block_source and src["source"] != "Google News Pháp lý":
            err("C9", "%s: tin thuộc nguồn '%s' nhưng đặt trong khối '%s'"
                % (path, src["source"], block_source))
        # C3
        published = src.get("published_at")
        if not published:
            err("C3", "%s: tin không có thời gian đăng, không đủ điều kiện lên bản tin" % path)
        else:
            pub_dt = None
            try:
                pub_dt = datetime.fromisoformat(published)
            except ValueError:
                err("C3", "%s: thời gian đăng '%s' không hợp lệ" % (path, published))
            if pub_dt is not None:
                if cutoff and pub_dt < cutoff:
                    err("C3", "%s: tin đăng %s, cũ hơn mốc độ tươi %s"
                        % (path, published[:16], raw["cutoff"][:16]))
                if pub_dt > future_gate:
                    err("C3", "%s: giờ đăng %s ở TƯƠNG LAI so với lúc kiểm định"
                        % (path, published[:16]))
        # C4
        url = src.get("url") or ""
        if section == "world":
            # Gói 4: tin thế giới nguồn web (Bloomberg/Reuters...) được renderer gắn link
            # nên phải kiểm URL: bắt buộc https + host thuộc allowlist. Nguồn telegram
            # hoặc raw_feed cũ chưa có source_kind thì bỏ qua như trước (tương thích ngược).
            if src.get("source_kind") == "web":
                if not re.match(r"^https://", url):
                    err("C4", "%s: URL không hợp lệ '%s' (nguồn web phải https)"
                        % (path, url[:60]))
                else:
                    host = url.split("/")[2].lower().split(":")[0]
                    base = host[4:] if host.startswith("www.") else host
                    if base not in ALLOWED_HOSTS and host not in ALLOWED_HOSTS:
                        err("C4", "%s: URL trỏ tới host lạ '%s'" % (path, host))
        else:
            if not re.match(r"^https?://", url):
                err("C4", "%s: URL không hợp lệ '%s'" % (path, url[:60]))
            else:
                host = url.split("/")[2].lower().split(":")[0]
                base = host[4:] if host.startswith("www.") else host
                if base not in ALLOWED_HOSTS and host not in ALLOWED_HOSTS:
                    err("C4", "%s: URL trỏ tới host lạ '%s'" % (path, host))
            if re.search(r"(^#|example\.com|localhost|/#$)", url):
                err("C4", "%s: URL placeholder '%s'" % (path, url[:60]))
        # C5, C8
        if PLACEHOLDER_RE.search(title):
            err("C5", "%s: tiêu đề còn chữ mẫu/placeholder: %s" % (path, title[:70]))
        if HTML_TAG_RE.search(title):
            err("C8", "%s: tiêu đề chứa thẻ HTML thô: %s" % (path, title[:70]))
        # C7
        if url and not url.startswith("https://t.me/"):
            if url in seen_urls:
                err("C7", "%s: trùng URL với %s (cùng một bài)" % (path, seen_urls[url]))
            else:
                seen_urls[url] = path
        titles.append((path, section, norm_tokens(title)))

    # C7 - trùng sự kiện theo độ giống tiêu đề
    for i in range(len(titles)):
        for j in range(i + 1, len(titles)):
            (p1, s1, t1), (p2, s2, t2) = titles[i], titles[j]
            if not t1 or not t2 or len(t1) < 4 or len(t2) < 4:
                continue
            if (s1 == "world") != (s2 == "world"):
                continue  # khác ngôn ngữ, không so
            inter = len(t1 & t2)
            jac = inter / float(len(t1 | t2))
            # Ngưỡng khớp với make_draft.py: >=0.85 script đã tự loại nên còn sót là lỗi;
            # 0.62-0.85 là vùng xám, chỉ cảnh báo để Claude tự quyết.
            if jac >= 0.85:
                err("C7", "%s và %s nhiều khả năng cùng một sự kiện (giống %.0f%%)"
                    % (p1, p2, jac * 100))
            elif jac >= 0.62:
                warn("C7", "%s và %s có thể trùng sự kiện (giống %.0f%%)"
                     % (p1, p2, jac * 100))

    # C5, C8 trên các trường text còn lại
    md = bulletin.get("market_data") or {}
    text_fields = [("market_data.summary", md.get("summary") or "")]
    for i, tile in enumerate(md.get("tiles") or []):
        text_fields.append(("tiles[%d].value" % i, tile.get("value") or ""))
    for i, row in enumerate(md.get("domestic") or []):
        text_fields.append(("domestic[%d].value" % i, row.get("value") or ""))
        text_fields.append(("domestic[%d].change" % i, row.get("change") or ""))
    for i, d in enumerate(bulletin.get("dividends") or []):
        text_fields.append(("dividends[%d].event" % i, d.get("event") or ""))
    for i, h in enumerate(bulletin.get("highlights") or []):
        text_fields.append(("highlights[%d].text" % i, h.get("text") or ""))
    for path, value in text_fields:
        if PLACEHOLDER_RE.search(value):
            err("C5", "%s: còn chữ mẫu/placeholder: %s" % (path, value[:70]))
        if HTML_TAG_RE.search(value):
            err("C8", "%s: chứa thẻ HTML thô: %s" % (path, value[:70]))

    # Lịch chốt quyền: ngày ĐKCC phải từ hôm nay tới +10 ngày
    try:
        today = datetime.strptime(bulletin.get("date", ""), "%Y-%m-%d").date()
    except ValueError:
        today = None
    if today:
        for i, d in enumerate(bulletin.get("dividends") or []):
            try:
                rec = datetime.strptime(d.get("record_date", ""), "%Y-%m-%d").date()
            except ValueError:
                err("C3", "dividends[%d]: ngày ĐKCC không hợp lệ" % i)
                continue
            if not (today <= rec <= today + timedelta(days=10)):
                warn("C3", "dividends[%d]: ĐKCC %s ngoài khoảng hôm nay→+10 ngày"
                     % (i, d.get("record_date")))

    world_n = len(bulletin.get("world") or [])
    vn_n = sum(len(b.get("items") or []) for b in (bulletin.get("vietnam") or []))
    print("KIEM DINH bulletin.json (%s, ban %s)" % (bulletin.get("date"), bulletin.get("edition")))
    print("   the gioi %d tin · Viet Nam %d tin / %d bao · phap ly %d tin · chot quyen %d ma"
          % (world_n, vn_n, len(bulletin.get("vietnam") or []), legal_total,
             len(bulletin.get("dividends") or [])))
    for w in warnings:
        print("   CANH BAO %s" % w)
    if errors:
        print("   THAT BAI: %d loi" % len(errors))
        for e in errors:
            print("      %s" % e)
        return 20
    print("   DAT: khong loi, %d canh bao" % len(warnings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
