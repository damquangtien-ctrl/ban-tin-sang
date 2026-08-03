#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dò chat_id của các cuộc trò chuyện mà bot Zalo đang tham gia.

Zalo Bot API không có lệnh "liệt kê nhóm của bot", chỉ có getUpdates dạng chờ tin
nhắn mới. Vì vậy: chạy script này, rồi trong lúc nó đang chờ, hãy nhắn một câu bất kỳ
(hoặc @mention bot) trong TỪNG nhóm cần lấy id. Mỗi cuộc trò chuyện có tin nhắn mới
sẽ hiện ra một dòng.

Chạy (đặt token vào biến môi trường, KHÔNG gõ token thẳng vào lệnh có lưu lịch sử):
    ZALO_BOT_TOKEN=... python3 tools/zalo_chat_ids.py --timeout 180

Kết quả in ra chat_id thật để dán vào biến môi trường ZALO_CHAT_ID (ngăn cách bằng
dấu phẩy). Đừng đăng kết quả này lên nơi công khai.
Exit: 0 = tìm được ít nhất một cuộc trò chuyện · 1 = không có · 2 = thiếu token
"""
import argparse
import json
import os
import sys
import time
import urllib.request

API = "https://bot-api.zapps.me/bot%s/getUpdates"


def walk_chats(node, found, depth=0):
    """Nhặt mọi cặp (chat_id, chat_type) trong phản hồi, bất kể cấu trúc lồng nhau."""
    if depth > 6:
        return
    if isinstance(node, dict):
        chat = node.get("chat")
        if isinstance(chat, dict) and chat.get("id"):
            found[str(chat["id"])] = str(chat.get("chat_type") or chat.get("type") or "?")
        cid = node.get("chat_id")
        if isinstance(cid, (str, int)) and str(cid) not in found:
            found[str(cid)] = str(node.get("chat_type") or "?")
        for val in node.values():
            walk_chats(val, found, depth + 1)
    elif isinstance(node, list):
        for val in node:
            walk_chats(val, found, depth + 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=180, help="tổng thời gian chờ (giây)")
    args = ap.parse_args()

    token = os.environ.get("ZALO_BOT_TOKEN", "").strip()
    if not token:
        print("LOI: chua dat bien moi truong ZALO_BOT_TOKEN")
        return 2

    print("Dang cho tin nhan moi trong %d giay..." % args.timeout)
    print("Hay nhan mot cau bat ky trong TUNG nhom can lay id (va ca chat rieng voi bot).")
    print("-" * 68)

    found, deadline, offset = {}, time.time() + args.timeout, None
    while time.time() < deadline:
        url = API % token
        if offset is not None:
            url += "?offset=%d" % offset
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=min(60, max(5, int(deadline - time.time())))) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 - long-polling hết giờ là chuyện thường
            continue
        before = len(found)
        walk_chats(payload, found)
        for key in ("result", "updates", "data"):
            rows = payload.get(key) if isinstance(payload, dict) else None
            if isinstance(rows, list):
                ids = [r.get("update_id") for r in rows
                       if isinstance(r, dict) and isinstance(r.get("update_id"), int)]
                if ids:
                    offset = max(ids) + 1
        for chat_id, kind in list(found.items())[before:]:
            label = "NHOM " if chat_id.lower().startswith("zgr-") else "CA NHAN"
            print("  %s  %-24s (chat_type=%s)" % (label, chat_id, kind))

    print("-" * 68)
    if not found:
        print("Khong nhan duoc tin nhan nao. Kiem tra: bot con trong nhom khong,")
        print("va da nhan tin trong nhom TRONG LUC script dang chay chua.")
        return 1
    print("Tim thay %d cuoc tro chuyen. Dan vao bien moi truong ZALO_CHAT_ID:" % len(found))
    print("  ZALO_CHAT_ID=%s" % ",".join(found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
