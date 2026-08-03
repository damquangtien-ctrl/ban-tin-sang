#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gửi tin nhắn tóm tắt + link trang qua Telegram và Zalo, có biên nhận và chống gửi trùng.

Nguyên tắc:
  • Chỉ gửi khi publish đã xác nhận thành công (đọc data/publish.json).
  • Mỗi kênh gửi được NHIỀU ĐÍCH: cá nhân và/hoặc nhóm mà bot đang tham gia.
    Khai báo bằng danh sách ngăn cách bởi dấu phẩy trong biến môi trường chat id.
  • Thành công = MỌI đích của CẢ HAI kênh đều giao được.
  • Chống gửi trùng theo (ngày + content_sha + từng đích): đích nào đã giao thì bỏ qua,
    chạy lại chỉ thử lại đích còn thiếu. Thêm đích mới thì chỉ đích mới được gửi.
  • Token đọc từ biến môi trường; token và chat id không bao giờ lọt vào log hay biên nhận
    (biên nhận chỉ lưu mã băm rút gọn của đích).

Biến môi trường:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (một hoặc nhiều id, ngăn cách bằng dấu phẩy)
  ZALO_BOT_TOKEN,     ZALO_CHAT_ID       (id nhóm Zalo bắt đầu bằng "zgr-")
Dùng:  python3 tools/notify.py [--dry-run] [--alert "lý do"]
Exit:  0  mọi đích của cả hai kênh đã giao (gửi mới hoặc đã giao từ lần chạy trước)
       50 ít nhất một đích gửi thất bại
       51 thiếu biến môi trường của ít nhất một kênh
       52 chưa có xác nhận publish thành công → từ chối gửi
"""
import argparse
import hashlib
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
SPLIT_RE = re.compile(r"[,;\n\r\t ]+")


def secret_values():
    """Mọi giá trị cần che: token và từng chat id trong danh sách."""
    out = []
    for token_key, chat_key in ENV_KEYS.values():
        token = os.environ.get(token_key, "").strip()
        if token:
            out.append(token)
        raw = os.environ.get(chat_key, "")
        out.append(raw.strip())
        out.extend(p for p in SPLIT_RE.split(raw) if p)
    return [v for v in out if v and len(v) >= 6]


def scrub(text):
    out = str(text)
    for val in sorted(set(secret_values()), key=len, reverse=True):
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


def target_id(value):
    """Mã băm rút gọn — đủ để nhận diện đích mà không lộ chat id ra repo công khai."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def target_kind(channel, value):
    if channel == "zalo":
        return "group" if value.lower().startswith("zgr-") else "user"
    return "group" if value.startswith("-") else "user"


def channel_config(name):
    token_key, chat_key = ENV_KEYS[name]
    token = os.environ.get(token_key, "").strip()
    targets = [t for t in SPLIT_RE.split(os.environ.get(chat_key, "")) if t]
    return token, targets


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


def send(channel, token, target, body):
    """Trả (ok, message_id, error) — không lộ token/chat id trong bất kỳ giá trị nào."""
    if channel == "telegram":
        url = "https://api.telegram.org/bot%s/sendMessage" % token
        fields = {"chat_id": target, "parse_mode": "HTML",
                  "disable_web_page_preview": "true", "text": body}
    else:
        url = "https://bot-api.zapps.me/bot%s/sendMessage" % token
        fields = {"chat_id": target, "text": body}
    try:
        status, resp = post_form(url, fields)
    except Exception as exc:  # noqa: BLE001
        return False, None, scrub(str(exc))[:120]
    if status != 200 or '"ok":false' in resp.replace(" ", ""):
        return False, None, short_error(status, resp)
    return True, extract_message_id(resp), None


def previous_targets(prev, content_sha, targets, channel):
    """Bản ghi từng đích của lần chạy trước, chỉ tính khi cùng content_sha."""
    out = {}
    if not prev or prev.get("content_sha") != content_sha:
        return out
    rows = prev.get("targets")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("target_id"):
                out[row["target_id"]] = row
        return out
    # Biên nhận kiểu cũ (một đích duy nhất) — chỉ áp cho đích đầu tiên đang cấu hình
    if prev.get("ok") and targets:
        out[target_id(targets[0])] = {
            "target_id": target_id(targets[0]), "kind": target_kind(channel, targets[0]),
            "ok": True, "message_id": prev.get("message_id"),
            "sent_at": prev.get("sent_at"), "content_sha": content_sha}
    return out


def deliver_channel(channel, body, content_sha, prev, dry_run):
    """Gửi tới mọi đích của một kênh. Trả (trạng thái, bản ghi kênh)."""
    token, targets = channel_config(channel)
    if not (token and targets):
        missing = []
        token_key, chat_key = ENV_KEYS[channel]
        if not token:
            missing.append(token_key)
        if not targets:
            missing.append(chat_key)
        log("   %-8s THIEU CAU HINH: %s" % (channel, " / ".join(missing)))
        return "unconfigured", {"ok": False, "message_id": None, "sent_at": None,
                                "content_sha": content_sha, "targets": [],
                                "error": "thieu bien moi truong"}

    done = previous_targets(prev, content_sha, targets, channel)
    records, sent, failed, skipped = [], 0, 0, 0
    for target in targets:
        tid = target_id(target)
        kind = target_kind(channel, target)
        old = done.get(tid)
        if old and old.get("ok"):
            records.append(dict(old, kind=kind))
            skipped += 1
            log("   %-8s %s/%s DA GUI truoc do (message_id=%s) - bo qua"
                % (channel, kind, tid, old.get("message_id")))
            continue
        if dry_run:
            log("   %-8s %s/%s DRY-RUN, %d ky tu" % (channel, kind, tid, len(body)))
            records.append({"target_id": tid, "kind": kind, "ok": True,
                            "message_id": None, "sent_at": None, "content_sha": content_sha})
            continue
        ok, message_id, err = send(channel, token, target, body)
        if ok:
            sent += 1
            log("   %-8s %s/%s OK message_id=%s" % (channel, kind, tid, message_id))
        else:
            failed += 1
            log("   %-8s %s/%s THAT BAI: %s" % (channel, kind, tid, err))
        row = {"target_id": tid, "kind": kind, "ok": bool(ok), "message_id": message_id,
               "sent_at": now_iso() if ok else None, "content_sha": content_sha}
        if not ok:
            row["error"] = err
        records.append(row)

    all_ok = bool(records) and all(r.get("ok") for r in records)
    first_id = next((r.get("message_id") for r in records if r.get("ok") and r.get("message_id")),
                    None)
    first_at = next((r.get("sent_at") for r in records if r.get("ok") and r.get("sent_at")), None)
    summary = {"ok": all_ok, "message_id": first_id, "sent_at": first_at,
               "content_sha": content_sha, "targets": records}
    if not all_ok:
        summary["error"] = "%d/%d dich that bai" % (failed, len(records))
    status = "failed" if failed else ("already" if skipped == len(records) else "sent")
    return status, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bulletin", default=os.path.join(ROOT, "data", "bulletin.json"))
    ap.add_argument("--publish", default=os.path.join(ROOT, "data", "publish.json"))
    ap.add_argument("--outdir", default=ROOT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--alert", default=None,
                    help="gửi cảnh báo sự cố (bỏ qua kiểm tra publish và chống trùng)")
    args = ap.parse_args()

    if args.alert:
        head, stats = "⚠️ BẢN TIN SÁNG GẶP SỰ CỐ", scrub(args.alert)[:300]
        rc = 0
        for channel in CHANNELS:
            token, targets = channel_config(channel)
            if not (token and targets):
                log("   %s: BO QUA (thieu bien moi truong)" % channel)
                rc = 51
                continue
            body = compose(channel, head, stats, [])
            for target in targets:
                ok, _, err = send(channel, token, target, body)
                log("   %s %s/%s: %s" % (channel, target_kind(channel, target),
                                         target_id(target), "OK" if ok else "LOI %s" % err))
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
        body = compose(channel, head, stats, highlights)
        status, summary = deliver_channel(channel, body, content_sha,
                                          delivery.get(channel), args.dry_run)
        statuses[channel] = status
        if not args.dry_run:
            delivery[channel] = summary

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
        log("KET QUA: co dich that bai -> exit 50")
        return 50
    total = sum(len(delivery[c].get("targets") or []) for c in CHANNELS)
    log("KET QUA: %d dich da giao (%s)"
        % (total, ", ".join("%s=%s" % (c, statuses[c]) for c in CHANNELS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
