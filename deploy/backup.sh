#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_DIR="${1:-${SOURCE_DIR}/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ ! -f "${SOURCE_DIR}/.env" ]]; then
  echo "Missing ${SOURCE_DIR}/.env" >&2
  exit 1
fi

mkdir -p "${DEST_DIR}"
ARCHIVE="${DEST_DIR}/bili-agent-${STAMP}.tar.gz"

tar --exclude='data/audio' --exclude='data/media' \
  -czf "${ARCHIVE}" \
  -C "${SOURCE_DIR}" .env data

chmod 600 "${ARCHIVE}"
echo "Created ${ARCHIVE}"
