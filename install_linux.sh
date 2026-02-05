#!/usr/bin/env bash
set -euo pipefail

VERSION="linux-mint-artemis1"

usage() {
  cat <<USAGE
Europa CLI - Sender SMS installer (versao ${VERSION})

Uso:
  ./install_linux.sh [--disable-modemmanager]

Opcoes:
  --disable-modemmanager   Para e desabilita o ModemManager (evita conflito com Gammu)
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

disable_mm=false
if [[ "${1:-}" == "--disable-modemmanager" ]]; then
  disable_mm=true
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "Erro: apt-get nao encontrado. Este instalador foi feito para Linux Mint/Ubuntu." >&2
  exit 1
fi

SUDO=""
if [[ $EUID -ne 0 ]]; then
  SUDO="sudo"
fi

$SUDO apt-get update
$SUDO apt-get install -y \
  gammu \
  psmisc \
  usb-modeswitch \
  python3

if $disable_mm && command -v systemctl >/dev/null 2>&1; then
  $SUDO systemctl stop ModemManager || true
  $SUDO systemctl disable ModemManager || true
fi

echo "Instalacao concluida."
