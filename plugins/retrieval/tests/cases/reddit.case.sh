# reddit: 公开 API 对 datacenter IP 全 403 → 必走 arctic-shift 存档 API
ARCHIVE="$(katana_config_get reddit_archive_api "https://arctic-shift.photon-reddit.com" "")"
PROXY="$(katana_config_get web_proxy "" "")"
OUT="$(curl -s ${PROXY:+--proxy "$PROXY"} -m 30 "$ARCHIVE/api/posts/ids?ids=1tdbh4t" 2>/dev/null)"
assert_contains "reddit" "$OUT" "Sporebattyl"
