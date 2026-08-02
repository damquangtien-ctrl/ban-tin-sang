#!/usr/bin/env bash
# Kiểm thử phần điều phối của run.sh: thứ tự bước, mã thoát, và độ bền của
# bước ghi biên nhận. Dùng script giả thay cho validate/render/publish/notify
# nên KHÔNG chạm mạng, KHÔNG chạm repo thật.
#
# Dùng:  bash tools/selftest_receipt.sh
# Exit:  0 = tất cả tình huống đạt · 1 = có tình huống sai
set -uo pipefail

TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATE="$(TZ='Asia/Ho_Chi_Minh' date +%F)"
PASS=0
FAIL=0

# Trên Windows, `python3` trên PATH thường là bí danh của Microsoft Store và thoát
# với mã 49 thay vì chạy. Nếu gặp, dựng shim trỏ tới trình thông dịch thật để
# kiểm thử được đúng logic của run.sh. Trên Ubuntu của cloud thì bỏ qua bước này.
if ! python3 -c "print(1)" >/dev/null 2>&1; then
  REALPY=""
  for cand in python py "/c/Users/Admin/AppData/Local/Programs/Python/Python311/python.exe"; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "print(1)" >/dev/null 2>&1; then
      REALPY="$(command -v "$cand")"; break
    fi
  done
  if [ -z "$REALPY" ]; then
    echo "BO QUA: khong tim thay trinh thong dich Python de kiem thu"; exit 0
  fi
  SHIM="$(mktemp -d)"
  printf '#!/usr/bin/env bash\nexec "%s" "$@"\n' "$REALPY" > "$SHIM/python3"
  chmod +x "$SHIM/python3"
  export PATH="$SHIM:$PATH"
  echo "(dung shim python3 -> $REALPY)"
fi

# $1 publish_rc · $2 receipt_rc · $3 notify_rc → in ra đường dẫn workspace giả
setup() {
  local w
  w="$(mktemp -d)"
  mkdir -p "$w/tools" "$w/data" "$w/archive/data"
  cp "$TOOLS/run.sh" "$w/tools/run.sh"
  printf 'import sys\nprint("stub validate")\nsys.exit(0)\n' > "$w/tools/validate.py"
  printf 'import sys\nprint("stub render")\nsys.exit(0)\n' > "$w/tools/render.py"
  {
    printf 'import os, sys\n'
    printf 'root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n'
    printf 'with open(os.path.join(root, "data", "notify_calls"), "a") as fh:\n'
    printf '    fh.write("call\\n")\n'
    printf 'print("stub notify")\n'
    printf 'sys.exit(%s)\n' "$3"
  } > "$w/tools/notify.py"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"\n'
    printf 'echo "call" >> "$ROOT/data/publish_calls"\n'
    printf 'if [ "${1:-publish}" = "receipt" ]; then\n'
    printf '  echo "stub receipt"; exit %s\n' "$2"
    printf 'fi\n'
    printf 'if [ "%s" -ne 0 ]; then echo "stub publish LOI"; exit %s; fi\n' "$1" "$1"
    printf 'printf %s > "$ROOT/data/publish.json"\n' \
           "'"'{"ok":true,"commit":"deadbeefcafe","published_at":"2026-01-01T06:30:00+07:00"}'"'"
    printf 'echo "stub publish"; exit 0\n'
  } > "$w/tools/publish.sh"
  printf '{"content_sha":"stub"}\n' > "$w/archive/data/$DATE.json"
  printf '%s' "$w"
}

count() { [ -f "$1" ] && wc -l < "$1" | tr -d ' ' || echo 0; }

check() { # $1 tên · $2 điều kiện đã đánh giá (0/1) · $3 chi tiết
  if [ "$2" -eq 0 ]; then
    PASS=$((PASS + 1)); printf 'DAT   %-56s %s\n' "$1" "$3"
  else
    FAIL=$((FAIL + 1)); printf 'SAI   %-56s %s\n' "$1" "$3"
  fi
}

echo "KIEM THU DIEU PHOI run.sh"
printf -- '------------------------------------------------------------------------\n'

# 1. Mọi thứ suôn sẻ
W="$(setup 0 0 0)"; bash "$W/tools/run.sh" >/dev/null 2>&1; rc=$?
n=$(count "$W/data/notify_calls")
haslog=1; [ -f "$W/archive/data/$DATE.log" ] && grep -q "HOAN TAT" "$W/archive/data/$DATE.log" && haslog=0
[ "$rc" -eq 0 ] && [ "$n" -eq 1 ] && [ "$haslog" -eq 0 ]; ok=$?
check "1. Tron ven -> exit 0, nhat ky co dong HOAN TAT" $ok "exit=$rc notify=$n log_hoan_tat=$([ $haslog -eq 0 ] && echo co || echo khong)"
rm -rf "$W"

# 2. Hai kênh OK nhưng commit biên nhận hỏng  (yêu cầu chính)
W="$(setup 0 43 0)"; bash "$W/tools/run.sh" >/dev/null 2>&1; rc=$?
n=$(count "$W/data/notify_calls")
warn=1; grep -q "LOI BIEN NHAN" "$W/data/pipeline.log" && warn=0
nores=1; grep -q "KHONG tu dong gui lai trong phien nay" "$W/data/pipeline.log" && nores=0
[ "$rc" -eq 53 ] && [ "$n" -eq 1 ] && [ "$warn" -eq 0 ] && [ "$nores" -eq 0 ]; ok=$?
check "2. Gui OK + receipt hong -> exit 53, KHONG goi API lan hai" $ok \
      "exit=$rc notify=$n canh_bao=$([ $warn -eq 0 ] && echo co || echo khong)"
rm -rf "$W"

# 3. Publish hỏng → không được gọi notify
W="$(setup 43 0 0)"; bash "$W/tools/run.sh" >/dev/null 2>&1; rc=$?
n=$(count "$W/data/notify_calls")
[ "$rc" -eq 43 ] && [ "$n" -eq 0 ]; ok=$?
check "3. Publish hong -> khong goi API nhan tin nao" $ok "exit=$rc notify=$n"
rm -rf "$W"

# 4. Kênh gửi hỏng nhưng biên nhận ghi được → giữ mã lỗi của notify
W="$(setup 0 0 50)"; bash "$W/tools/run.sh" >/dev/null 2>&1; rc=$?
n=$(count "$W/data/notify_calls")
[ "$rc" -eq 50 ] && [ "$n" -eq 1 ]; ok=$?
check "4. Kenh gui hong, receipt OK -> giu exit 50" $ok "exit=$rc notify=$n"
rm -rf "$W"

# 5. Cả gửi hỏng lẫn biên nhận hỏng → 53 (mất trạng thái là nghiêm trọng hơn)
W="$(setup 0 43 50)"; bash "$W/tools/run.sh" >/dev/null 2>&1; rc=$?
[ "$rc" -eq 53 ]; ok=$?
check "5. Gui hong + receipt hong -> exit 53" $ok "exit=$rc"
rm -rf "$W"

# 6. Publish không tạo được bằng chứng → chặn gửi bằng mã 44
W="$(setup 0 0 0)"
printf '#!/usr/bin/env bash\necho "call" >> "$(dirname "$0")/../data/publish_calls"\nif [ "${1:-publish}" = "receipt" ]; then exit 0; fi\necho "publish khong ghi bang chung"; exit 0\n' \
  > "$W/tools/publish.sh"
bash "$W/tools/run.sh" >/dev/null 2>&1; rc=$?
n=$(count "$W/data/notify_calls")
[ "$rc" -eq 44 ] && [ "$n" -eq 0 ]; ok=$?
check "6. Thieu publish.json -> exit 44, khong goi API" $ok "exit=$rc notify=$n"
rm -rf "$W"

printf -- '------------------------------------------------------------------------\n'
printf 'KET QUA: %d/%d tinh huong dat\n' "$PASS" "$((PASS + FAIL))"
[ "$FAIL" -eq 0 ]
