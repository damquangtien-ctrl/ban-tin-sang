#!/usr/bin/env bash
# Chạy toàn bộ chặng sau khi Claude đã tạo data/bulletin.json:
#   kiểm định → render → xuất bản → gửi thông báo → ghi biên nhận
#
# Thứ tự là bắt buộc: KHÔNG gọi notify khi publish chưa thành công.
# notify.py tự chống gửi trùng theo (ngày + content_sha) nên chạy lại an toàn.
#
# Dùng:  bash tools/run.sh
# Exit:  0 trọn vẹn · 20 kiểm định · 30 render · 4x xuất bản · 5x giao tin nhắn
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 42
PY="$(command -v python3 || command -v python)"
LOG="$ROOT/data/pipeline.log"
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

step "5/5 GHI BIEN NHAN"
bash tools/publish.sh receipt 2>&1 | tee -a "$LOG"
receipt_rc=${PIPESTATUS[0]}
[ "$receipt_rc" -eq 0 ] || echo "CANH BAO: khong ghi duoc bien nhan (exit $receipt_rc)" | tee -a "$LOG"

if [ "$notify_rc" -ne 0 ]; then
  echo "" | tee -a "$LOG"
  echo "KET THUC: trang DA xuat ban nhung giao tin nhan chua tron ven (exit $notify_rc)" | tee -a "$LOG"
  exit "$notify_rc"
fi

echo "" | tee -a "$LOG"
echo "HOAN TAT luc $(stamp) - trang da len, ca hai kenh da giao" | tee -a "$LOG"
exit 0
