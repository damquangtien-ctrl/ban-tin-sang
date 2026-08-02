#!/usr/bin/env bash
# Xuất bản bản tin lên GitHub Pages bằng git (đường xuất bản DUY NHẤT).
# Phiên cloud của routine đã được gắn repo sẵn nên git push hoạt động không cần token.
#
# Dùng:  bash tools/publish.sh
# Exit:  0 = đã đẩy lên main · 40 = thiếu file · 41 = index khác archive
#        42 = repo sai/không phải git · 43 = push thất bại
set -uo pipefail

REPO_EXPECTED="damquangtien-ctrl/ban-tin-sang"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 42
DATE="$(TZ='Asia/Ho_Chi_Minh' date +%F)"
ARCHIVE="archive/${DATE}.html"

log() { printf '%s\n' "$*"; }

# 1. Kiểm tra đang ở đúng repo
remote="$(git config --get remote.origin.url 2>/dev/null || true)"
case "$remote" in
  *"$REPO_EXPECTED"*) : ;;
  "") log "LOI: thu muc lam viec khong phai git repo (remote trong)"; exit 42 ;;
  *) log "LOI: remote la '$remote', khong phai $REPO_EXPECTED"; exit 42 ;;
esac

# 2. File bắt buộc
[ -f index.html ] || { log "LOI: thieu index.html"; exit 40; }
[ -f "$ARCHIVE" ] || { log "LOI: thieu $ARCHIVE"; exit 40; }

# 3. index.html và bản lưu trữ trong ngày phải giống hệt nhau
a="$(sha256sum index.html | cut -d' ' -f1)"
b="$(sha256sum "$ARCHIVE" | cut -d' ' -f1)"
if [ "$a" != "$b" ]; then
  log "LOI: index.html khac $ARCHIVE (sha $a vs $b)"
  exit 41
fi

# 4. Trang phải có nội dung thật, không phải khung rỗng
if ! grep -q 'class="dateline"' index.html; then
  log "LOI: index.html khong co dong dateline - nghi ngo render hong"
  exit 41
fi
if grep -qiE 'lorem ipsum|dữ liệu mẫu|Bản xem thử' index.html; then
  log "LOI: index.html con noi dung mau"
  exit 41
fi

# 5. Commit + push
git add -A index.html archive || { log "LOI: git add that bai"; exit 43; }
if git diff --cached --quiet; then
  log "Khong co thay doi de commit (noi dung trung ban da xuat ban)."
  exit 0
fi
git -c user.name='BanTinBot' -c user.email='bot@bantin.local' \
    commit -q -m "Ban tin ${DATE}" || { log "LOI: git commit that bai"; exit 43; }

if ! git push -q origin HEAD:main; then
  log "Push that bai, thu dong bo lai roi push lan 2..."
  git pull --rebase -q origin main || { log "LOI: rebase that bai"; exit 43; }
  git push -q origin HEAD:main || { log "LOI: push that bai lan 2"; exit 43; }
fi

log "Da xuat ban ${DATE}: index.html + ${ARCHIVE} (sha ${a:0:12})"
exit 0
