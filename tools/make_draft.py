#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dựng bản nháp data/draft.json từ raw_feed.json để Claude biên tập.

Script làm phần cơ học: lọc độ tươi, bỏ tin không có giờ đăng, tách nhóm
pháp lý, gom theo báo đúng thứ tự, gắn sẵn ref. Claude chỉ việc: xoá tin
không đạt, sửa/dịch tiêu đề, điền số liệu + tin nổi bật + lịch chốt quyền.
Nhờ vậy Claude không bao giờ phải tự gõ ref/URL/giờ.

Dùng:  python3 tools/make_draft.py [--hours 24]
Exit:  0 = OK · 12 = không đủ tin để dựng nháp
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

VN = timezone(timedelta(hours=7))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SOURCE_ORDER = ["CafeF", "Vietstock", "VnEconomy", "Tin nhanh Chứng khoán", "Báo Đầu tư",
                "MarketTimes", "Bloomberg Businessweek VN", "VnBusiness", "Tiền Phong",
                "Thời báo Ngân hàng", "BNEWS", "HOSE"]
# Thứ tự THẮNG khi trùng tin thế giới: nguồn chính chủ có permalink đứng trước
# nguồn tiếp sóng (marketfeed vốn tiếp sóng headline Bloomberg).
WORLD_ORDER = ["Bloomberg", "Reuters", "Market News Feed", "VN Wall Street", "Dubaotiente"]
LEGAL_EXTRA = ["VnExpress", "Báo Thanh tra", "Google News Pháp lý"]
TILE_ORDER = ["S&P 500", "Dow Jones", "Nasdaq", "Dầu WTI", "Dầu Brent",
              "Vàng thế giới", "Bitcoin", "DXY"]

LEGAL_RE = re.compile(
    r"khởi tố|bắt tạm giam|bắt giam|thanh tra|điều tra|xử phạt|phạt tiền|truy tố|"
    r"vi phạm công bố|thao túng|lừa đảo|kết luận thanh tra|đình chỉ|cưỡng chế thuế|"
    r"khám xét|tạm giữ hình sự|UBCKNN xử", re.I)
DIVIDEND_RE = re.compile(r"cổ tức|chốt quyền|ĐKCC|ngày đăng ký cuối cùng", re.I)
NOISE_RE = re.compile(
    r"^(ảnh|video|infographic|emagazine|photo)\b|giải trí|showbiz|sao việt|"
    r"tử vi|bóng đá|thời trang|làm đẹp|du lịch hè", re.I)
# Tin THUẦN GIÁ hàng hoá/tỷ giá: đã nằm ở Phần I số liệu nên không vào Phần III.
# Chỉ khớp tin bản thân nó là bản tin giá, không đụng tin ngành (vd. giá thép HRC).
PRICE_TICK_RE = re.compile(
    r"(giá vàng|vàng thế giới|vàng sjc|giá dầu|dầu wti|dầu brent|giá xăng|giá bạc)", re.I)

PER_SOURCE_CAP = 25
WORLD_CAP = 60
LEGAL_CAP = 30
TITLE_MAX = 280
DUP_DROP = 0.85   # giống từ mức này trở lên: script tự loại
DUP_FLAG = 0.62   # vùng xám: giữ lại nhưng đánh dấu cho Claude quyết
STOPWORDS = {"của", "và", "các", "trong", "cho", "với", "được", "một", "này", "đã",
             "là", "có", "từ", "tại", "về", "the", "a", "of", "to", "in", "on", "for"}


def tokens(title):
    txt = re.sub(r"[^\w\sÀ-ỹ]", " ", (title or "").lower())
    return {t for t in txt.split() if len(t) > 2 and t not in STOPWORDS}


def dedup(entries, kept, flags, label):
    """Loại tin trùng rõ ràng; ghi lại cặp nghi ngờ vào flags."""
    out = []
    for entry in entries:
        tk = tokens(entry["title"])
        if len(tk) < 4:
            out.append(entry)
            continue
        dropped = False
        for prev_tk, prev in kept:
            if not prev_tk:
                continue
            jac = len(tk & prev_tk) / float(len(tk | prev_tk))
            if jac >= DUP_DROP:
                dropped = True
                break
            if jac >= DUP_FLAG:
                flags.append("%s: '%s…' có thể trùng với '%s…' (%.0f%%)"
                             % (label, entry["title"][:48], prev["title"][:48], jac * 100))
        if dropped:
            continue
        kept.append((tk, entry))
        out.append(entry)
    return out


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def fresh(item, cutoff):
    ts = item.get("published_at")
    if not ts:
        return False
    try:
        return datetime.fromisoformat(ts) >= cutoff
    except ValueError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=os.path.join(ROOT, "data", "raw_feed.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "draft.json"))
    ap.add_argument("--hours", type=int, default=None,
                    help="ghi đè cửa sổ độ tươi (mặc định lấy theo raw_feed.json)")
    args = ap.parse_args()

    if not os.path.isfile(args.raw):
        print("LOI: thieu %s — chay tools/fetch_feeds.py truoc" % args.raw)
        return 12
    raw = load(args.raw)
    now = datetime.now(VN)
    hours = args.hours or raw.get("freshness_hours", 24)
    cutoff = now - timedelta(hours=hours)

    world, legal_pool, vn_pool, dividends = [], [], {}, []
    for item in raw.get("items", []):
        if not fresh(item, cutoff):
            continue
        title = item.get("title") or ""
        if NOISE_RE.search(title):
            continue
        if item.get("category") != "world" and PRICE_TICK_RE.search(title):
            continue
        if len(title) > TITLE_MAX:
            title = title[:TITLE_MAX].rsplit(" ", 1)[0] + "…"
        entry = {"ref": item["id"], "title": title,
                 "_source": item["source"], "_at": item["published_at"],
                 "_url": item.get("url", "")}
        if item.get("category") == "world":
            world.append(entry)
        elif item.get("category") == "dividend" or DIVIDEND_RE.search(title):
            dividends.append(entry)
            if item.get("category") != "dividend":
                vn_pool.setdefault(item["source"], []).append(entry)
        elif item.get("category") == "legal" or LEGAL_RE.search(title):
            legal_pool.append(entry)
        else:
            vn_pool.setdefault(item["source"], []).append(entry)

    flags = []
    # Khử trùng thế giới với ưu tiên nguồn: xếp (nguồn chính chủ trước, mới trước)
    # để bản Bloomberg/Reuters thắng bản tiếp sóng khi cùng một tin; hiển thị
    # cuối cùng vẫn mới → cũ.
    def world_rank(entry):
        src = entry["_source"]
        return WORLD_ORDER.index(src) if src in WORLD_ORDER else len(WORLD_ORDER)

    world.sort(key=lambda e: e["_at"], reverse=True)
    world.sort(key=world_rank)
    world = dedup(world, [], flags, "thế giới")
    world.sort(key=lambda e: e["_at"], reverse=True)

    # Khử trùng chéo: pháp lý xét trước (tin pháp lý phải nằm ở phần IV),
    # sau đó tới tin trong nước, ưu tiên báo đứng trước trong SOURCE_ORDER.
    def rank(entry, order):
        src = entry["_source"]
        return (order.index(src) if src in order else len(order), )

    legal_order = SOURCE_ORDER + LEGAL_EXTRA
    legal_pool.sort(key=lambda e: e["_at"], reverse=True)      # mới trước
    legal_pool.sort(key=lambda e: rank(e, legal_order))        # rồi báo phổ biến trước
    kept_pairs = []
    legal_pool = dedup(legal_pool, kept_pairs, flags, "pháp lý")[:LEGAL_CAP]

    legal_blocks = {}
    for entry in sorted(legal_pool, key=lambda e: e["_at"], reverse=True):
        legal_blocks.setdefault(entry["_source"], []).append(entry)

    def ordered(blocks, extra):
        order = SOURCE_ORDER + extra
        known = [(order.index(s), s) for s in blocks if s in order]
        unknown = sorted(s for s in blocks if s not in order)
        out = []
        for _, name in sorted(known):
            out.append({"source": name, "items": blocks[name]})
        for name in unknown:
            out.append({"source": name, "items": blocks[name]})
        return out

    vn_flat = []
    for source in SOURCE_ORDER:
        entries = vn_pool.get(source) or []
        entries.sort(key=lambda e: e["_at"], reverse=True)
        vn_flat.extend(entries[:PER_SOURCE_CAP])
    for source in sorted(s for s in vn_pool if s not in SOURCE_ORDER):
        vn_pool[source].sort(key=lambda e: e["_at"], reverse=True)
        vn_flat.extend(vn_pool[source][:PER_SOURCE_CAP])
    vn_flat = dedup(vn_flat, kept_pairs, flags, "trong nước")

    vn_blocks = {}
    for entry in vn_flat:
        vn_blocks.setdefault(entry["_source"], []).append(entry)
    for entries in vn_blocks.values():
        entries.sort(key=lambda e: e["_at"], reverse=True)

    draft = {
        "date": now.strftime("%Y-%m-%d"),
        "edition": "sunday" if now.weekday() == 6 else "daily",
        "market_data": {
            "tiles": [{"label": lb, "value": "", "change_pct": None} for lb in TILE_ORDER],
            "domestic": [{"label": lb, "value": "", "change": "", "direction": "flat"}
                         for lb in ["VN-Index", "HNX-Index", "Thanh khoản HOSE",
                                    "Khối ngoại (HOSE)", "Vàng SJC", "USD/VND (VCB bán)"]],
            "summary": "",
        },
        "world": world[:WORLD_CAP],
        "vietnam": ordered(vn_blocks, []),
        "legal": ordered(legal_blocks, LEGAL_EXTRA),
        "dividends": [],
        "highlights": [],
        "_huong_dan": [
            "Đây là BẢN NHÁP do script dựng. Hãy biên tập rồi lưu thành data/bulletin.json.",
            "Giữ nguyên 'ref' của tin nào chọn — KHÔNG tự tạo ref, KHÔNG tự viết URL.",
            "Xoá các khoá bắt đầu bằng '_' (_source/_at/_url) trong bản cuối.",
            "world: giữ tối đa 30 tin nóng nhất, dịch tiêu đề sang tiếng Việt, đặt translated=true.",
            "world hạn mức mềm sau khử trùng: Bloomberg ~5 + Reuters ~5 + Market News Feed ~8-10 + VNWS ~5-8 + DBT ~5-8; ngày đặc biệt được vượt.",
            "world: nguồn Bloomberg/Reuters sẽ TỰ ĐỘNG có link bài gốc khi render; kênh Telegram không link — bạn không phải làm gì về link.",
            "Bloomberg Businessweek VN (khối báo Việt Nam): tiêu đề ĐÃ là tiếng Việt (bản dịch có bản quyền). Bài phân tích thị trường/kinh tế VN giữ ở khối báo này; bài dịch Bloomberg về thế giới (không dính thị trường VN) CHUYỂN ref sang world (translated=false, giữ nguyên tiêu đề) — link bbw.vn tự gắn.",
            "CẢNH GIÁC trùng khác ngôn ngữ: máy KHÔNG bắt được trùng giữa tin Bloomberg tiếng Anh và bản dịch bbw.vn tiếng Việt — cùng sự kiện thì ưu tiên giữ bản bbw.vn (độc giả đọc được tiếng Việt), bỏ bản tiếng Anh.",
            "vietnam: xoá tin PR/vụn, ưu tiên doanh nghiệp lớn, bỏ khối báo không còn tin.",
            "legal: giữ tối đa 10 tin, ưu tiên doanh nghiệp lớn và vụ án lớn.",
            "Khử trùng: một sự kiện chỉ giữ ở báo đứng trước trong thứ tự, xoá ở báo sau.",
            "Điền market_data (8 ô đúng thứ tự, đúng số liệu tra được), highlights 3-5 tin.",
            "dividends: điền ticker/event/record_date từ các tin gợi ý bên dưới.",
        ],
        "_goi_y_chot_quyen": [
            {"ref": e["ref"], "title": e["title"], "_at": e["_at"]} for e in dividends[:25]
        ],
        "_can_kiem_tra_trung": flags[:40],
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(draft, fh, ensure_ascii=False, indent=1)

    vn_n = sum(len(b["items"]) for b in draft["vietnam"])
    legal_n = sum(len(b["items"]) for b in draft["legal"])
    print("Ban nhap: %s (%s) -> %s" % (draft["date"], draft["edition"], args.out))
    print("   ung vien: the gioi %d · Viet Nam %d tin / %d bao · phap ly %d tin / %d bao"
          % (len(draft["world"]), vn_n, len(draft["vietnam"]), legal_n, len(draft["legal"])))
    print("   goi y chot quyen: %d tin" % len(draft["_goi_y_chot_quyen"]))
    if not (draft["world"] or vn_n):
        print("   CANH BAO: khong co ung vien nao trong cua so %dh" % hours)
        return 12
    return 0


if __name__ == "__main__":
    sys.exit(main())
