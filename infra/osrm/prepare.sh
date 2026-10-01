#!/usr/bin/env bash
# Chuẩn bị dữ liệu OSRM cho Việt Nam (chạy một lần, chạy lại khi muốn cập nhật bản đồ).
#
#   ./infra/osrm/prepare.sh            # car + foot
#   ./infra/osrm/prepare.sh car        # chỉ car
#
# Cần Docker. Bước extract cho Việt Nam tốn khoảng 4–8 GB RAM -> nâng memory của
# Docker Desktop (Settings > Resources) nếu bị kill giữa chừng.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATA="$ROOT/data/osrm"
IMAGE="${OSRM_IMAGE:-ghcr.io/project-osrm/osrm-backend:latest}"
PBF_URL="${OSRM_PBF_URL:-https://download.geofabrik.de/asia/vietnam-latest.osm.pbf}"
PBF="$DATA/vietnam-latest.osm.pbf"

profiles=("$@")
[ ${#profiles[@]} -eq 0 ] && profiles=(car foot)

for p in "${profiles[@]}"; do
  case "$p" in
    car|foot) ;;
    *) echo "Profile không hỗ trợ: $p (chỉ car | foot)" >&2; exit 1 ;;
  esac
done

mkdir -p "$DATA"

if [ ! -f "$PBF" ]; then
  echo "==> Tải bản đồ OSM Việt Nam từ $PBF_URL"
  curl -fL --retry 3 -o "$PBF.part" "$PBF_URL"
  mv "$PBF.part" "$PBF"
else
  echo "==> Đã có $PBF (xoá file này nếu muốn tải bản mới)"
fi

for p in "${profiles[@]}"; do
  out="$DATA/$p"
  echo "==> Xử lý profile $p -> $out"
  rm -rf "$out"
  mkdir -p "$out"
  # osrm-extract ghi kết quả cạnh file .pbf, nên đặt một bản (hard link) vào thư mục profile.
  ln "$PBF" "$out/vietnam-latest.osm.pbf" 2>/dev/null || cp "$PBF" "$out/vietnam-latest.osm.pbf"

  docker run --rm -t -v "$out:/data" "$IMAGE" osrm-extract -p "/opt/$p.lua" /data/vietnam-latest.osm.pbf
  docker run --rm -t -v "$out:/data" "$IMAGE" osrm-partition /data/vietnam-latest.osrm
  docker run --rm -t -v "$out:/data" "$IMAGE" osrm-customize /data/vietnam-latest.osrm

  rm -f "$out/vietnam-latest.osm.pbf"
done

echo "==> Xong. Chạy: docker compose --profile routing up -d"
