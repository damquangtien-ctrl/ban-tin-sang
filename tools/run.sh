#!/usr/bin/env bash
# Chạy toàn bộ chặng sau khi Claude đã tạo data/bulletin.json:
#   kiểm định -> render -> xuất bản -> gửi thông báo
# Mỗi chặng ghi log rõ ràng; chặng nào hỏng thì dừng ngay (trừ thông báo).
#
# Dùng:  bash tools/run.sh
# Exit:  0 = trọn vẹn · 20 kiểm định hỏng · 30 render hỏng · 4x publish hỏng
#        5x đã xuất bản nhưng thông báo hỏng
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 42
PY="$(command -v python3 || command -v python)"
LOG="$ROOT/data/pipeline.log"
mkdir -p "$ROOT/data"

step() { printf '\n=== %s ===\n' "$1" | tee -a "$LOG"; }
stamp() { TZ='Asia/Ho_Chi_Minh' date '+%F %T'; }

printf '\n########## CHAY LUC %s ##########\n' "$(stamp)" >> "$LOG"

step "1/4 KIEM DINH"
"$PY" tools/validate.py 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] || { echo "DUNG: kiem dinh that bai (exit $rc)" | tee -a "$LOG"; exit "$rc"; }

step "2/4 RENDER"
"$PY" tools/render.py 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] || { echo "DUNG: render that bai (exit $rc)" | tee -a "$LOG"; exit "$rc"; }

step "3/4 XUAT BAN"
bash tools/publish.sh 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] || { echo "DUNG: xuat ban that bai (exit $rc)" | tee -a "$LOG"; exit "$rc"; }

step "4/4 THONG BAO"
"$PY" tools/notify.py 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
if [ "$rc" -ne 0 ]; then
  echo "CANH BAO: trang da xuat ban nhung thong bao that bai (exit $rc)" | tee -a "$LOG"
  exit "$rc"
fi

echo "" | tee -a "$LOG"
echo "HOAN TAT luc $(stamp)" | tee -a "$LOG"
exit 0
