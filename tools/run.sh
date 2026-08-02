#!/usr/bin/env bash
# Chạy toàn bộ chặng sau khi Claude đã tạo data/bulletin.json:
#   kiểm định → render → xuất bản → gửi thông báo → ghi biên nhận
#
# Thứ tự là bắt buộc: KHÔNG gọi notify khi publish chưa thành công.
# notify.py tự chống gửi trùng theo (ngày + content_sha) nên chạy lại an toàn.
# Biên nhận (audit + nhật ký) được ghi bằng ĐÚNG MỘT commit bổ sung; nếu commit đó
# hỏng thì trạng thái chống-gửi-trùng chưa vào repo → trả 53, KHÔNG gửi lại lần hai.
#
# Dùng:  bash tools/run.sh
# Exit:  0 trọn vẹn · 20 kiểm định · 30 render · 4x xuất bản
#        50/51/52 giao tin nhắn · 53 đã giao nhưng chưa lưu được biên nhận
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 42
PY="$(command -v python3 || command -v python)"
LOG="$ROOT/data/pipeline.log"
DATE="$(TZ='Asia/Ho_Chi_Minh' date +%F)"
LOGDST="archive/data/${DATE}.log"
mkdir -p "$ROOT/data"

step()  { printf '\n=== %s ===\n' "$1" | tee -a "$LOG"; }
stamp() { TZ='Asia/Ho_Chi_Minh' date '+%F %T'; }

printf '\n########## CHAY LUC %s ##########\n' "$(stamp)" >> "$LOG"
# publish.json là bằng chứng của LƯỢT CHẠY NÀY - xoá dấu vết lượt trước
rm -f "$ROOT/data/publish.json"

step "1/5 KIEM DINH"
"$PY" tools/validate.py 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] || { echo "DUNG: kiem dinh that bai (exit $rc)" | tee -a "$LOG"; exit "$rc"; }

step "2/5 RENDER"
"$PY" tools/render.py 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] || { echo "DUNG: render that bai (exit $rc)" | tee -a "$LOG"; exit "$rc"; }

step "3/5 XUAT BAN"
bash tools/publish.sh 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
if [ "$rc" -ne 0 ]; then
  echo "DUNG: xuat ban that bai (exit $rc) - KHONG gui tin nhan" | tee -a "$LOG"
  exit "$rc"
fi
if [ ! -s "$ROOT/data/publish.json" ]; then
  echo "DUNG: khong co data/publish.json - KHONG gui tin nhan" | tee -a "$LOG"
  exit 44
fi

step "4/5 GUI TIN NHAN"
"$PY" tools/notify.py 2>&1 | tee -a "$LOG"
notify_rc=${PIPESTATUS[0]}

# Kết luận về nội dung + giao tin nhắn được ghi TRƯỚC khi chụp nhật ký vào repo,
# để bản lưu trong repo là nhật ký hoàn chỉnh chứ không phải ảnh chụp giữa chừng.
if [ "$notify_rc" -eq 0 ]; then
  echo "HOAN TAT luc $(stamp) - trang da len, ca hai kenh da giao" | tee -a "$LOG"
else
  echo "KET THUC luc $(stamp) - trang DA xuat ban nhung giao tin nhan chua tron ven (exit $notify_rc)" \
    | tee -a "$LOG"
fi

step "5/5 GHI BIEN NHAN"
mkdir -p "$ROOT/archive/data"
tail -n 80 "$LOG" > "$ROOT/$LOGDST" 2>/dev/null || true
bash tools/publish.sh receipt 2>&1 | tee -a "$LOG"
receipt_rc=${PIPESTATUS[0]}

if [ "$receipt_rc" -ne 0 ]; then
  {
    echo ""
    echo "LOI BIEN NHAN (exit $receipt_rc): tin nhan DA GIAO nhung trang thai chong-gui-trung"
    echo "  CHUA duoc luu vao repo. Phien sau se khong biet la da gui va co the gui lai ca hai kenh."
    echo "  KHONG tu dong gui lai trong phien nay - notify.py da hoan tat, chay lai la gui trung."
    echo "  Xu ly: chay lai DUNG lenh 'bash tools/publish.sh receipt'; TUYET DOI khong chay lai tools/run.sh."
  } | tee -a "$LOG"
  exit 53
fi

if [ "$notify_rc" -ne 0 ]; then
  exit "$notify_rc"
fi
exit 0
