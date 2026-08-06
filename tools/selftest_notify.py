#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kiểm thử khâu giao tin nhắn: mã thoát, chống gửi trùng, chốt chặn publish,
chịu được tiến trình chết cứng giữa vòng gửi.

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

CALLS = []          # tên kênh của từng lời gọi
CALL_TARGETS = []   # chat_id của từng lời gọi
OUTCOME = {"telegram": True, "zalo": True}
OUTCOME_TARGET = {}  # chat_id -> bool, ưu tiên hơn OUTCOME
CRASH = {"at_call": None}  # lời gọi API thứ N thì chết cứng (giả lập crash giữa vòng gửi)


def fake_post_form(url, fields, timeout=45):
    channel = "telegram" if "api.telegram.org" in url else "zalo"
    chat = fields.get("chat_id", "")
    CALLS.append(channel)
    CALL_TARGETS.append(chat)
    if CRASH["at_call"] is not None and len(CALLS) == CRASH["at_call"]:
        # SystemExit là BaseException → không bị `except Exception` trong notify.send bắt lại,
        # thoát thẳng ra ngoài như tiến trình chết thật giữa chừng.
        raise SystemExit(97)
    ok = OUTCOME_TARGET.get(chat, OUTCOME.get(channel, True))
    if ok:
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


def set_env(telegram=True, zalo=True, zalo_targets="zalouser1"):
    pairs = {
        "TELEGRAM_BOT_TOKEN": "1234567890:TEST-TELEGRAM-TOKEN" if telegram else "",
        "TELEGRAM_CHAT_ID": "111222333" if telegram else "",
        "ZALO_BOT_TOKEN": "9876543210:TEST-ZALO-TOKEN" if zalo else "",
        "ZALO_CHAT_ID": zalo_targets if zalo else "",
    }
    for key, val in pairs.items():
        if val:
            os.environ[key] = val
        else:
            os.environ.pop(key, None)


def run(root):
    CALLS.clear()
    CALL_TARGETS.clear()
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


def case_group_target_fail():
    """Zalo có 2 đích (cá nhân + nhóm); nhóm lỗi → exit 50, chạy lại chỉ thử lại nhóm."""
    root = make_workspace()
    set_env(zalo_targets="zalouser1, zgr-nhomdoctin")
    OUTCOME.update(telegram=True, zalo=True)
    OUTCOME_TARGET.clear()
    OUTCOME_TARGET["zgr-nhomdoctin"] = False
    rc = run(root)
    d = audit_delivery(root)
    kinds = {t["kind"]: t["ok"] for t in d["zalo"]["targets"]}
    first = (rc == 50 and len(d["zalo"]["targets"]) == 2
             and kinds.get("user") is True and kinds.get("group") is False)
    check("10. Zalo gui nhom: nhom loi -> exit 50", first,
          "exit=%s calls=%s" % (rc, len(CALLS)))

    OUTCOME_TARGET.clear()                      # nhóm đã sửa xong
    rc2 = run(root)
    d2 = audit_delivery(root)
    only_group = (rc2 == 0 and CALL_TARGETS == ["zgr-nhomdoctin"]
                  and all(t["ok"] for t in d2["zalo"]["targets"]))
    check("11. Chay lai chi thu lai DICH nhom bi loi", only_group,
          "exit=%s dich_da_goi=%s" % (rc2, CALL_TARGETS))
    shutil.rmtree(root, ignore_errors=True)


def case_new_group_added():
    """Thêm nhóm mới sau khi đã gửi xong → chỉ nhóm mới được gửi."""
    root = make_workspace()
    set_env(zalo_targets="zalouser1")
    OUTCOME.update(telegram=True, zalo=True)
    OUTCOME_TARGET.clear()
    run(root)
    set_env(zalo_targets="zalouser1, zgr-nhommoi")
    rc = run(root)
    d = audit_delivery(root)
    ok = (rc == 0 and CALL_TARGETS == ["zgr-nhommoi"]
          and len(d["zalo"]["targets"]) == 2)
    check("12. Them nhom moi -> chi gui nhom moi", ok,
          "exit=%s dich_da_goi=%s" % (rc, CALL_TARGETS))
    set_env()
    shutil.rmtree(root, ignore_errors=True)


def case_no_chatid_in_receipt():
    """Biên nhận không được chứa chat id thật, chỉ mã băm rút gọn."""
    root = make_workspace()
    set_env(zalo_targets="zalouser1, zgr-nhomdoctin")
    OUTCOME.update(telegram=True, zalo=True)
    OUTCOME_TARGET.clear()
    run(root)
    with open(os.path.join(root, "archive", "data", "%s.json" % DATE), encoding="utf-8") as fh:
        blob = fh.read()
    leaked = [v for v in ("zalouser1", "zgr-nhomdoctin", "111222333") if v in blob]
    check("13. Bien nhan khong chua chat id that", not leaked,
          "chi luu ma bam" if not leaked else "LO: %s" % leaked)
    set_env()
    shutil.rmtree(root, ignore_errors=True)


def case_crash_mid_delivery():
    """Chết cứng ngay lời gọi Zalo (sau khi Telegram đã gửi xong): biên nhận Telegram
    phải ĐÃ nằm trên đĩa ngay lúc đó; chạy lại chỉ gửi Zalo, không gửi trùng Telegram."""
    root = make_workspace()
    set_env()
    OUTCOME.update(telegram=True, zalo=True)
    OUTCOME_TARGET.clear()
    CRASH["at_call"] = 2                     # lời gọi API thứ hai (Zalo) chết giữa chừng
    crashed = False
    try:
        run(root)
    except SystemExit:
        crashed = True
    finally:
        CRASH["at_call"] = None
    d = audit_delivery(root)                 # đọc đĩa NGAY lúc tiến trình vừa chết
    tg = d.get("telegram") or {}
    tg_targets = tg.get("targets") or []
    on_disk = (tg.get("ok") is True and len(tg_targets) == 1
               and tg_targets[0].get("ok") is True and "zalo" not in d)
    rc = run(root)                           # stub hết chết -> chạy lại
    d2 = audit_delivery(root)
    ok = (crashed and on_disk and rc == 0 and CALLS == ["zalo"]
          and d2["telegram"]["message_id"] == tg.get("message_id")
          and d2["zalo"]["ok"] is True)
    check("14. Chet giua vong gui -> Telegram khong gui trung", ok,
          "crash=%s tren_dia=%s exit=%s calls=%s" % (crashed, on_disk, rc, CALLS))
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
    case_group_target_fail()
    case_new_group_added()
    case_no_chatid_in_receipt()
    case_crash_mid_delivery()
    print("-" * 72)
    failed = [n for n, ok, _ in results if not ok]
    print("KET QUA: %d/%d tinh huong dat" % (len(results) - len(failed), len(results)))
    for name in failed:
        print("   SAI: %s" % name)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
