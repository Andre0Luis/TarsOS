#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The TarsOS Authors
# Empacota o TarsOS para release (roda no CI e na sua máquina):
#
#   tools/build_release.sh [--edge] [saida]
#
#   (padrão)  versão estável: o número do arquivo VERSION          ex.: 0.2.0
#   --edge    versão contínua, a cada merge na main:                ex.: 0.2.0-edge.abc1234
#   saida     pasta dos arquivos (padrão: dist)
#
# Gera, em <saida>/:
#   tarsos-<versão>.tar.gz          pacote completo (overlay carimbado + splash gerada + tools + build + docs):
#                                   descompacte e rode  tools/deploy.sh --all
#   tarsos-overlay-<versão>.tar.gz  só o sistema de arquivos do Echo, para  tar -C / -xzf -  (veja docs/INSTALL.md)
#   MANIFEST.txt                    versão, commit e SHA-256 de cada arquivo do overlay
#   SHA256SUMS                      soma de verificação dos arquivos acima
#
# Não é imagem de boot: o kernel e o desbloqueio vêm do postmarketOS e do amonet (veja o README).
# Precisa de: bash, tar, gzip, python3 e Pillow (para gerar a tela de boas-vindas).
set -euo pipefail
cd "$(dirname "$0")/.."

CHANNEL=stable
OUT=dist
for a in "$@"; do
  case "$a" in
    --edge) CHANNEL=edge ;;
    -*) echo "opção desconhecida: $a" >&2; exit 2 ;;
    *) OUT=$a ;;
  esac
done

BASE=$(tr -d '[:space:]' < VERSION)
[[ "$BASE" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "VERSION inválida: '$BASE' (use MAJOR.MINOR.PATCH)" >&2; exit 1; }
SHA=${GITHUB_SHA:-$(git rev-parse HEAD 2>/dev/null || echo desconhecido)}
SHORT=${SHA:0:7}
BUILD_DATE=$(date -u +%Y-%m-%d)
LABEL=$BASE
[ "$CHANNEL" = edge ] && LABEL="$BASE-edge.$SHORT"

command -v python3 >/dev/null || { echo "python3 não encontrado" >&2; exit 1; }
python3 -c 'import PIL' 2>/dev/null || { echo "Pillow não encontrado: pip install pillow" >&2; exit 1; }

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
PKG="tarsos-$LABEL"
mkdir -p "$STAGE/$PKG" "$OUT"

# --- árvore do pacote (sem testes nem CI)
cp -R rootfs-overlay tools build docs "$STAGE/$PKG/"
cp LICENSE NOTICE README.md VERSION CONTRIBUTING.md SECURITY.md "$STAGE/$PKG/"
find "$STAGE/$PKG" \( -name __pycache__ -o -name .DS_Store \) -prune -exec rm -rf {} +

# --- carimba a versão no arquivo de identidade (lido pelo app Sobre e pelo comando showos)
REL="$STAGE/$PKG/rootfs-overlay/etc/luizinhoshowos-release"
CODENAME=$(sed -n 's/^VERSION_CODENAME="\(.*\)"/\1/p' "$REL")
python3 - "$REL" "$LABEL" "$SHA" "$BUILD_DATE" "$CODENAME" <<'PY'
import re, sys
path, label, sha, date, codename = sys.argv[1:6]
s = open(path, encoding='utf-8').read()
s = re.sub(r'^VERSION=.*$', 'VERSION="%s"' % label, s, flags=re.M)
s = re.sub(r'^RELEASE_DATE=.*$', 'RELEASE_DATE="%s"' % date, s, flags=re.M)
s = re.sub(r'^PRETTY_NAME=.*$', 'PRETTY_NAME="TarsOS %s (%s)"' % (label, codename), s, flags=re.M)
s = s.rstrip('\n') + '\nBUILD_COMMIT="%s"\n' % sha
open(path, 'w', encoding='utf-8').write(s)
PY

# --- tela de boas-vindas (gerada, não versionada)
SPLASH_DIR="$STAGE/$PKG/rootfs-overlay/usr/share/luizinhoshowos"
mkdir -p "$SPLASH_DIR"
python3 - "$SPLASH_DIR/splash.raw" "$LABEL" <<'PY'
import re, subprocess, sys
out, label = sys.argv[1:3]
src = open('tools/make_splash.py', encoding='utf-8').read()
src = re.sub(r"NAME, VERSION, CODENAME, DATE = ([^,]+), '[^']*', ([^,]+), ([^\n]+)",
             lambda m: "NAME, VERSION, CODENAME, DATE = %s, '%s', %s, %s" % (m.group(1), label, m.group(2), m.group(3)), src, count=1)
open('/tmp/tarsos_make_splash.py', 'w', encoding='utf-8').write(src)
subprocess.run([sys.executable, '/tmp/tarsos_make_splash.py', out, '270'], check=True)
PY
rm -f "$SPLASH_DIR/splash.raw.png"

# --- tar reproduzível (GNU tar ou bsdtar)
make_tar() {   # make_tar <arquivo.tar.gz> <diretório-base> <itens...>
  local file=$1 base=$2; shift 2
  if tar --version 2>/dev/null | grep -q 'GNU tar'; then
    tar -C "$base" --sort=name --owner=0 --group=0 --numeric-owner --mtime="$BUILD_DATE 00:00:00Z" -cf - "$@" | gzip -n -9 > "$file"
  else
    COPYFILE_DISABLE=1 tar -C "$base" --uid 0 --gid 0 --uname root --gname root -cf - "$@" | gzip -n -9 > "$file"
  fi
}
make_tar "$OUT/tarsos-$LABEL.tar.gz" "$STAGE" "$PKG"
make_tar "$OUT/tarsos-overlay-$LABEL.tar.gz" "$STAGE/$PKG/rootfs-overlay" etc usr

# --- manifesto e somas
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }
{
  echo "TarsOS $LABEL"
  echo "canal:   $CHANNEL"
  echo "commit:  $SHA"
  echo "data:    $BUILD_DATE"
  echo
  echo "# sha256  arquivo (rootfs-overlay carimbado)"
  (cd "$STAGE/$PKG/rootfs-overlay" && find etc usr -type f | LC_ALL=C sort | while read -r f; do sha256 "$f"; done)
} > "$OUT/MANIFEST.txt"
(cd "$OUT" && sha256 "tarsos-$LABEL.tar.gz" "tarsos-overlay-$LABEL.tar.gz" MANIFEST.txt > SHA256SUMS)

echo "TarsOS $LABEL ($CHANNEL) em $OUT/:"
ls -l "$OUT" | sed -n '2,$p'
echo "label=$LABEL" > "$OUT/.label"
