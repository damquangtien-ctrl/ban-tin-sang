#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gửi tin nhắn tóm tắt + link trang qua Telegram và Zalo, có biên nhận và chống gửi trùng.

Nguyên tắc:
  • Chỉ gửi khi publish đã xác nhận thành công (đọc data/publish.json).
  • Thành công = CẢ HAI kênh giao được. Một kênh hỏng là cả lượt chạy hỏng.
  • Chống gửi trùng theo (ngày + content_sha): kênh nào đã giao đúng bản nội dung này
    thì bỏ qua; chạy lại chỉ thử lại kênh còn thiếu.
  • Token đọc từ biến môi trường, không bao giờ ghi ra log/biên nhận/repo.

Biến môi trường: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ZALO_BOT_TOKEN, ZALO_CHAT_ID
Dùng:  python3 tools/notify.py [--dry-run] [--alert "lý do"]
Exit:  0  cả hai kênh đã giao (gửi mới hoặc đã giao từ lần chạy trước)
       50 ít nhất một kênh gửi thất bại
       51 thiếu biến môi trường của ít nhất một kênh
       52 chưa có xác nhận publish thành công → từ chối gửi
"""
import argparse
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

VN = timezone(timedelta(hours=7))
PAGE_URL = "https://damquangtien-ctrl.github.io/ban-tin-sang/"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANNELS = ("telegram", "zalo")
ENV_KEYS = {
    "telegram": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
    "zalo": ("ZALO_BOT_TOKEN", "ZALO_CHAT_ID"),
}
TOKEN_RE = re.compile(r"bot[0-9]{6,}:[A-Za-z0-9_\-]+")


def scrub(text):
    """Che mọi chuỗi bí mật trước khi in ra log hoặc ghi biên nhận."""
    out = str(text)
    for keys in ENV_KEYS.values():
        for key in keys:
            val = os.environ.get(key, "").strip()
            if val and len(val) >= 8:
                out = out.replace(val, "***")
    return TOKEN_RE.sub("bot***", out)


def log(msg):
    print(scrub(msg), flush=True)


def now_iso():
    return datetime.now(VN).isoformat(timespec="seconds")


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)


def post_form(url, fields, timeout=45):
    """Gửi POST; KHÔNG bao giờ trả hay ghi lại url (url có chứa token)."""
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")[:4000]


def extract_message_id(body):
    """Lấy DUY NHẤT message_id từ phản hồi; phần còn lại của phản hồi bị bỏ."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None

    def walk(node, depth=0):
        if depth > 4:
            return None
        if isinstance(node, dict):
            for key in ("message_id", "messageId", "msg_id", "messageID"):
                val = node.get(key)
                if isinstance(val, (str, int)):
                    return str(val)
            for val in node.values():
                got = walk(val, depth + 1)
                if got:
                    return got
        elif isinstance(node, list):
            for val in node[:5]:
                got = walk(val, depth + 1)
                if got:
                    return got
        return None

    return walk(data)


def short_error(status, body):
    """Mô tả lỗi ngắn, đã che bí mật, không kèm nguyên văn phản hồi."""
    desc = ""
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            for key in ("description", "message", "error", "error_description"):
                if isinstance(data.get(key), str):
                    desc = data[key][:80]
                    break
    except (ValueError, TypeError):
        pass
    return scrub(("HTTP %s" % status) + (" - %s" % desc if desc else ""))


def channel_config(name):
    token_key, chat_key = ENV_KEYS[name]
    return os.environ.get(token_key, "").strip(), os.environ.get(chat_key, "").strip()


def build_lines(bulletin):
    day = datetime.strptime(bulletin["date"], "%Y-%m-%d").strftime("%d/%m")
    now = datetime.now(VN)
    scheduled = 5 <= now.hour <= 8
    head = ("📈 BẢN TIN SÁNG %s" % day if scheduled
            else "📈 BẢN TIN CẬP NHẬT %s %s" % (now.strftime("%H:%M"), day))

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
    highlights = [(h.get("text", ""), h.get("source", ""))
                  for h in bulletin.get("highlights") or []]
    return head, stats, highlights


def compose(channel, head, stats, highlights):
    if channel == "telegram":
        parts = ["<b>%s</b>" % html.escape(head)]
        if stats:
            parts.append(html.escape(stats))
        if highlights:
            parts += ["", "⭐ <b>Đáng chú ý:</b>"]
            for i, (text, source) in enumerate(highlights, start=1):
                suffix = " (%s)" % html.escape(source) if source else ""
                parts.append("%d. %s%s" % (i, html.escape(text), suffix))
        parts += ["", '👉 <a href="%s">Đọc bản tin đầy đủ</a>' % PAGE_URL]
        return "\n".join(parts)
    parts = [head]
    if stats:
        parts.append(stats)
    if highlights:
        parts += ["", "⭐ Đáng chú ý:"]
        for i, (text, source) in enumerate(highlights, start=1):
            parts.append("%d. %s%s" % (i, text, " (%s)" % source if source else ""))
    parts += ["", "👉 Đọc bản tin đầy đủ: %s" % PAGE_URL]
    return "\n".join(parts)[:1900]


def send(channel, body):
    """Trả về (ok, message_id, error) — không lộ token trong bất kỳ giá trị nào."""
    token, chat = channel_config(channel)
    if channel == "telegram":
        url = "https://api.telegram.org/bot%s/sendMessage" % token
        fields = {"chat_id": chat, "parse_mode": "HTML",
                  "disable_web_page_preview": "true", "text": body}
    else:
        url = "https://bot-api.zapps.me/bot%s/sendMessage" % token
        fields = {"chat_id": chat, "text": body}
    try:
        status, resp = post_form(url, fields)
    except Exception as exc:  # noqa: BLE001
        return False, None, scrub(str(exc))[:120]
    ok = status == 200 and '"ok":false' not in resp.replace(" ", "")
    if not ok:
        return False, None, short_error(status, resp)
    return True, extract_message_id(resp), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bulletin", default=os.path.join(ROOT, "data", "bulletin.json"))
    ap.add_argument("--publish", default=os.path.join(ROOT, "data", "publish.json"))
    ap.add_argument("--outdir", default=ROOT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--alert", default=None,
                    help="gửi cảnh báo sự cố (bỏ qua kiểm tra publish và chống trùng)")
    args = ap.parse_args()

    # --- Chế độ cảnh báo sự cố: gửi thẳng, không biên nhận, không idempotency ---
    if args.alert:
        head, stats, highlights = "⚠️ BẢN TIN SÁNG GẶP SỰ CỐ", scrub(args.alert)[:300], []
        rc = 0
        for channel in CHANNELS:
            token, chat = channel_config(channel)
            if not (token and chat):
                log("   %s: BO QUA (thieu bien moi truong)" % channel)
                rc = 51
                continue
            ok, _, err = send(channel, compose(channel, head, stats, highlights))
            log("   %s: %s" % (channel, "OK" if ok else "LOI %s" % err))
            if not ok and rc == 0:
                rc = 50
        return rc

    bulletin = load_json(args.bulletin)
    if not bulletin:
        log("LOI: thieu hoac hong %s" % args.bulletin)
        return 50
    date = bulletin["date"]
    audit_path = os.path.join(args.outdir, "archive", "data", "%s.json" % date)
    audit = load_json(audit_path) or {}
    content_sha = audit.get("content_sha")
    if not content_sha:
        log("LOI: %s thieu content_sha - chay lai tools/render.py" % audit_path)
        return 50

    # --- Chốt chặn: chưa publish thì tuyệt đối không gửi ---
    pub = load_json(args.publish) or {}
    if not (pub.get("ok") and pub.get("commit")):
        log("TU CHOI GUI: chua co xac nhan publish thanh cong (data/publish.json)")
        return 52

    delivery = audit.get("delivery") or {}
    delivery["publish"] = {"ok": True, "commit": pub["commit"],
                           "published_at": pub.get("published_at") or now_iso()}

    head, stats, highlights = build_lines(bulletin)
    log("Giao nhan: %s | content_sha %s | commit %s"
        % (head, content_sha[:12], pub["commit"][:12]))

    statuses = {}
    for channel in CHANNELS:
        prev = delivery.get(channel) or {}
        if prev.get("ok") and prev.get("content_sha") == content_sha:
            statuses[channel] = "already"
            log("   %-8s DA GUI truoc do cho dung ban noi dung nay (message_id=%s) - bo qua"
                % (channel, prev.get("message_id")))
            continue
        token, chat = channel_config(channel)
        if not (token and chat):
            statuses[channel] = "unconfigured"
            missing = " / ".join(k for k in ENV_KEYS[channel] if not os.environ.get(k, "").strip())
            log("   %-8s THIEU CAU HINH: %s" % (channel, missing))
            delivery[channel] = {"ok": False, "message_id": None, "sent_at": None,
                                 "content_sha": content_sha, "error": "thieu bien moi truong"}
            continue
        body = compose(channel, head, stats, highlights)
        if args.dry_run:
            statuses[channel] = "dry"
            log("   %-8s DRY-RUN, %d ky tu, khong gui that" % (channel, len(body)))
            continue
        ok, message_id, err = send(channel, body)
        statuses[channel] = "sent" if ok else "failed"
        log("   %-8s %s" % (channel, "OK message_id=%s" % message_id if ok else "THAT BAI: %s" % err))
        delivery[channel] = {"ok": bool(ok), "message_id": message_id,
                             "sent_at": now_iso() if ok else None,
                             "content_sha": content_sha}
        if not ok:
            delivery[channel]["error"] = err

    if args.dry_run:
        return 0

    audit["delivery"] = delivery
    save_json(audit_path, audit)
    save_json(os.path.join(args.outdir, "data", "delivery-%s.json" % date),
              {"date": date, "content_sha": content_sha, "recorded_at": now_iso(),
               "delivery": delivery})
    log("   Bien nhan: %s" % audit_path)

    if any(s == "unconfigured" for s in statuses.values()):
        log("KET QUA: thieu cau hinh kenh -> exit 51")
        return 51
    if any(s == "failed" for s in statuses.values()):
        log("KET QUA: co kenh that bai -> exit 50")
        return 50
    log("KET QUA: ca hai kenh da giao (%s)"
        % ", ".join("%s=%s" % (c, statuses[c]) for c in CHANNELS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
