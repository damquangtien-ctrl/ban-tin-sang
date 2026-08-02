#!/usr/bin/env bash
# Xuất bản bản tin lên GitHub Pages bằng git (đường xuất bản DUY NHẤT).
# Phiên cloud của routine đã được gắn repo sẵn nên git push hoạt động không cần token.
#
# Dùng:  bash tools/publish.sh            đẩy trang + vết kiểm toán, ghi data/publish.json
#        bash tools/publish.sh receipt    chỉ commit biên nhận giao hàng (1 commit bổ sung)
# Exit:  0 = xong · 40 thiếu file · 41 index khác archive/nội dung mẫu
#        42 sai repo · 43 push thất bại
set -uo pipefail

REPO_EXPECTED="damquangtien-ctrl/ban-tin-sang"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 42
DATE="$(TZ='Asia/Ho_Chi_Minh' date +%F)"
ARCHIVE="archive/${DATE}.html"
AUDIT="archive/data/${DATE}.json"
MODE="${1:-publish}"

log() { printf '%s\n' "$*"; }
now_iso() { TZ='Asia/Ho_Chi_Minh' date '+%Y-%m-%dT%H:%M:%S%:z'; }

remote="$(git config --get remote.origin.url 2>/dev/null || true)"
case "$remote" in
  *"$REPO_EXPECTED"*) : ;;
  "") log "LOI: thu muc lam viec khong phai git repo"; exit 42 ;;
  *)  log "LOI: remote khong phai $REPO_EXPECTED"; exit 42 ;;
esac

push_now() {
  if ! git push -q origin HEAD:main; then
    log "Push that bai, dong bo lai roi thu lan 2..."
    git pull --rebase -q origin main || { log "LOI: rebase that bai"; return 1; }
    git push -q origin HEAD:main || { log "LOI: push that bai lan 2"; return 1; }
  fi
  return 0
}

# ---------- Chế độ biên nhận: chỉ commit file audit, KHÔNG gửi lại tin nhắn ----------
if [ "$MODE" = "receipt" ]; then
  [ -f "$AUDIT" ] || { log "LOI: thieu $AUDIT"; exit 40; }
  git add -- "$AUDIT" || { log "LOI: git add that bai"; exit 43; }
  if git diff --cached --quiet; then
    log "Bien nhan khong doi, khong can commit."
    exit 0
  fi
  git -c user.name='BanTinBot' -c user.email='bot@bantin.local' \
      commit -q -m "Bien nhan giao hang ${DATE}" || { log "LOI: commit that bai"; exit 43; }
  push_now || exit 43
  log "Da ghi bien nhan giao hang ${DATE} ($(git rev-parse HEAD))"
  exit 0
fi

# ---------- Chế độ xuất bản ----------
[ -f index.html ] || { log "LOI: thieu index.html"; exit 40; }
[ -f "$ARCHIVE" ] || { log "LOI: thieu $ARCHIVE"; exit 40; }
[ -f "$AUDIT" ]   || { log "LOI: thieu $AUDIT"; exit 40; }

a="$(sha256sum index.html | cut -d' ' -f1)"
b="$(sha256sum "$ARCHIVE" | cut -d' ' -f1)"
if [ "$a" != "$b" ]; then
  log "LOI: index.html khac $ARCHIVE (sha ${a:0:16} vs ${b:0:16})"
  exit 41
fi
grep -q 'class="dateline"' index.html || { log "LOI: index.html khong co dateline"; exit 41; }
if grep -qiE 'lorem ipsum|dữ liệu mẫu|Bản xem thử' index.html; then
  log "LOI: index.html con noi dung mau"; exit 41
fi

git add -A index.html archive || { log "LOI: git add that bai"; exit 43; }
if git diff --cached --quiet; then
  log "Noi dung trung ban da xuat ban, khong tao commit moi."
else
  git -c user.name='BanTinBot' -c user.email='bot@bantin.local' \
      commit -q -m "Ban tin ${DATE}" || { log "LOI: git commit that bai"; exit 43; }
  push_now || exit 43
fi

COMMIT="$(git rev-parse HEAD)"
mkdir -p data
cat > data/publish.json <<EOF
{
 "ok": true,
 "commit": "${COMMIT}",
 "published_at": "$(now_iso)",
 "index_sha256": "${a}",
 "archive_sha256": "${b}",
 "archive_path": "${ARCHIVE}",
 "audit_path": "${AUDIT}"
}
EOF

log "Da xuat ban ${DATE}: commit ${COMMIT:0:12} · sha noi dung ${a:0:16}"
exit 0
