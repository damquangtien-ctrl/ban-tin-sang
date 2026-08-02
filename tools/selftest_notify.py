#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kiểm thử khâu giao tin nhắn: mã thoát, chống gửi trùng, chốt chặn publish.

Không gọi mạng thật — thay `notify.post_form` bằng bản giả có ghi lại lời gọi.

Dùng:  python3 tools/selftest_notify.py
Exit:  0 = tất cả tình huống đạt · 1 = có tình huống sai
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATE = "2026-01-15"
CONTENT_SHA = "a" * 64
OTHER_SHA = "b" * 64

spec = importlib.util.spec_from_file_location("notify", os.path.join(HERE, "notify.py"))
notify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notify)

CALLS = []
OUTCOME = {"telegram": True, "zalo": True}


def fake_post_form(url, fields, timeout=45):
    channel = "telegram" if "api.telegram.org" in url else "zalo"
    CALLS.append(channel)
    if OUTCOME.get(channel, True):
        return 200, json.dumps({"ok": True, "result": {"message_id": 9000 + len(CALLS)}})
    return 401, json.dumps({"ok": False, "error_code": 401, "description": "Unauthorized"})


notify.post_form = fake_post_form


def make_workspace(publish_ok=True, content_sha=CONTENT_SHA):
    root = tempfile.mkdtemp(prefix="bantin-test-")
    os.makedirs(os.path.join(root, "data"))
    os.makedirs(os.path.join(root, "archive", "data"))
    bulletin = {
        "date": DATE, "edition": "daily",
        "market_data": {
            "tiles": [{"label": "S&P 500", "value": "6.500", "change_pct": 1.0}],
            "domestic": [{"label": "VN-Index", "value": "1.700",
                          "change": "+1,00%", "direction": "up"}],
            "summary": "Tóm tắt thử nghiệm.",
        },
        "world": [], "vietnam": [], "legal": [], "dividends": [],
        "highlights": [{"text": "Tin thử nghiệm số một", "source": "CafeF"}],
    }
    with open(os.path.join(root, "data", "bulletin.json"), "w", encoding="utf-8") as fh:
        json.dump(bulletin, fh, ensure_ascii=False)
    audit = dict(bulletin)
    audit["content_sha"] = content_sha
    with open(os.path.join(root, "archive", "data", "%s.json" % DATE), "w",
              encoding="utf-8") as fh:
        json.dump(audit, fh, ensure_ascii=False)
    if publish_ok:
        with open(os.path.join(root, "data", "publish.json"), "w", encoding="utf-8") as fh:
            json.dump({"ok": True, "commit": "c" * 40,
                       "published_at": "2026-01-15T06:30:00+07:00"}, fh)
    return root


def set_env(telegram=True, zalo=True):
    pairs = {
        "TELEGRAM_BOT_TOKEN": "1234567890:TEST-TELEGRAM-TOKEN" if telegram else "",
        "TELEGRAM_CHAT_ID": "111222333" if telegram else "",
        "ZALO_BOT_TOKEN": "9876543210:TEST-ZALO-TOKEN" if zalo else "",
        "ZALO_CHAT_ID": "zalochat" if zalo else "",
    }
    for key, val in pairs.items():
        if val:
            os.environ[key] = val
        else:
            os.environ.pop(key, None)


def run(root):
    CALLS.clear()
    sys.argv = ["notify.py",
                "--bulletin", os.path.join(root, "data", "bulletin.json"),
                "--publish", os.path.join(root, "data", "publish.json"),
                "--outdir", root]
    return notify.main()


def audit_delivery(root):
    with open(os.path.join(root, "archive", "data", "%s.json" % DATE), encoding="utf-8") as fh:
        return json.load(fh).get("delivery") or {}


results = []


def check(name, ok, detail):
    results.append((name, ok, detail))
    print("%s  %-52s %s" % ("DAT " if ok else "SAI ", name, detail))


def case_both_ok():
    root = make_workspace()
    set_env()
    OUTCOME.update(telegram=True, zalo=True)
    rc = run(root)
    d = audit_delivery(root)
    ok = (rc == 0 and sorted(CALLS) == ["telegram", "zalo"]
          and d["telegram"]["ok"] and d["zalo"]["ok"]
          and d["telegram"]["message_id"] and d["publish"]["commit"] == "c" * 40)
    check("1. Ca hai kenh thanh cong", ok, "exit=%s calls=%s" % (rc, CALLS))
    shutil.rmtree(root, ignore_errors=True)


def case_zalo_fail():
    root = make_workspace()
    set_env()
    OUTCOME.update(telegram=True, zalo=False)
    rc = run(root)
    d = audit_delivery(root)
    ok = rc == 50 and d["telegram"]["ok"] is True and d["zalo"]["ok"] is False
    check("2. Telegram OK, Zalo loi", ok, "exit=%s calls=%s" % (rc, CALLS))
    return root


def case_telegram_fail():
    root = make_workspace()
    set_env()
    OUTCOME.update(telegram=False, zalo=True)
    rc = run(root)
    d = audit_delivery(root)
    ok = rc == 50 and d["telegram"]["ok"] is False and d["zalo"]["ok"] is True
    check("3. Zalo OK, Telegram loi", ok, "exit=%s calls=%s" % (rc, CALLS))
    shutil.rmtree(root, ignore_errors=True)


def case_missing_env():
    root = make_workspace()
    set_env(telegram=True, zalo=False)
    OUTCOME.update(telegram=True, zalo=True)
    rc = run(root)
    ok = rc == 51 and CALLS == ["telegram"]
    check("4. Thieu bien moi truong (Zalo)", ok, "exit=%s calls=%s" % (rc, CALLS))
    set_env()
    shutil.rmtree(root, ignore_errors=True)


def case_publish_failed():
    root = make_workspace(publish_ok=False)
    set_env()
    OUTCOME.update(telegram=True, zalo=True)
    rc = run(root)
    ok = rc == 52 and CALLS == []
    check("5. Publish loi -> khong goi API nao", ok, "exit=%s calls=%s" % (rc, CALLS))
    shutil.rmtree(root, ignore_errors=True)


def case_rerun_no_duplicate():
    root = make_workspace()
    set_env()
    OUTCOME.update(telegram=True, zalo=True)
    run(root)
    first = audit_delivery(root)
    rc = run(root)                     # chạy lại y hệt
    second = audit_delivery(root)
    ok = (rc == 0 and CALLS == []
          and first["telegram"]["message_id"] == second["telegram"]["message_id"]
          and first["zalo"]["message_id"] == second["zalo"]["message_id"])
    check("6. Chay lai cung content_sha -> khong gui trung", ok,
          "exit=%s calls=%s" % (rc, CALLS))
    return root


def case_retry_only_failed(root):
    """Sau tình huống 2 (Zalo hỏng), chạy lại chỉ được thử lại Zalo."""
    set_env()
    OUTCOME.update(telegram=True, zalo=True)
    rc = run(root)
    d = audit_delivery(root)
    ok = rc == 0 and CALLS == ["zalo"] and d["telegram"]["ok"] and d["zalo"]["ok"]
    check("7. Chay lai chi thu lai kenh that bai", ok, "exit=%s calls=%s" % (rc, CALLS))
    shutil.rmtree(root, ignore_errors=True)


def case_new_content_resends(root):
    """Nội dung mới (content_sha khác) thì phải gửi lại cả hai kênh."""
    audit_path = os.path.join(root, "archive", "data", "%s.json" % DATE)
    with open(audit_path, encoding="utf-8") as fh:
        audit = json.load(fh)
    audit["content_sha"] = OTHER_SHA
    with open(audit_path, "w", encoding="utf-8") as fh:
        json.dump(audit, fh, ensure_ascii=False)
    set_env()
    OUTCOME.update(telegram=True, zalo=True)
    rc = run(root)
    ok = rc == 0 and sorted(CALLS) == ["telegram", "zalo"]
    check("8. Noi dung moi -> gui lai ca hai kenh", ok, "exit=%s calls=%s" % (rc, CALLS))
    shutil.rmtree(root, ignore_errors=True)


def case_no_token_in_receipt():
    root = make_workspace()
    set_env()
    OUTCOME.update(telegram=True, zalo=False)
    run(root)
    blob = ""
    for path in (os.path.join(root, "archive", "data", "%s.json" % DATE),
                 os.path.join(root, "data", "delivery-%s.json" % DATE)):
        with open(path, encoding="utf-8") as fh:
            blob += fh.read()
    leaked = [k for k in ("TEST-TELEGRAM-TOKEN", "TEST-ZALO-TOKEN") if k in blob]
    check("9. Bien nhan khong chua token", not leaked,
          "khong tim thay chuoi bi mat" if not leaked else "LO: %s" % leaked)
    shutil.rmtree(root, ignore_errors=True)


def main():
    print("KIEM THU KHAU GIAO TIN NHAN\n" + "-" * 72)
    case_both_ok()
    root2 = case_zalo_fail()
    case_telegram_fail()
    case_missing_env()
    case_publish_failed()
    root6 = case_rerun_no_duplicate()
    case_retry_only_failed(root2)
    case_new_content_resends(root6)
    case_no_token_in_receipt()
    print("-" * 72)
    failed = [n for n, ok, _ in results if not ok]
    print("KET QUA: %d/%d tinh huong dat" % (len(results) - len(failed), len(results)))
    for name in failed:
        print("   SAI: %s" % name)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
