#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The TarsOS Authors
# TarsOS: envia o rootfs-overlay para o Echo pelo SSH (sem regravar a imagem).
#
# Uso:  tools/deploy.sh [--all] [--no-restart] [--audio] [IP]
#   (padrão)      envia só usr/local/** e etc/init.d/** (código e serviços)
#   --all         envia o overlay inteiro (hostname, motd, local.d, conf do shairport...)
#   --no-restart  não reinicia nada
#   --audio       reinicia também o lsaudiod (corta o som por um instante)
# Antes de enviar, confere a sintaxe dos .py e dos scripts sh. Serviços novos entram no runlevel padrão.
# Variáveis: TARSOS_HOST (padrão tarsos.local), TARSOS_KEY (padrão keys/tarsos_ed25519 ou keys/luizinhoshowos_ed25519).
set -e
cd "$(dirname "$0")/.."
ALL=0; RESTART=1; AUDIO=0; IP=${TARSOS_HOST:-tarsos.local}
for a in "$@"; do
  case "$a" in
    --all) ALL=1 ;; --no-restart) RESTART=0 ;; --audio) AUDIO=1 ;; *) IP=$a ;;
  esac
done
OV=rootfs-overlay
for f in $(find $OV -name '*.py'); do python3 -m py_compile "$f" || { echo "erro de sintaxe em $f"; exit 1; }; done
find $OV -name __pycache__ -prune -exec rm -rf {} +
for f in $(grep -rl '^#!/bin/sh\|^#!/sbin/openrc-run' $OV); do sh -n "$f" || { echo "erro de sintaxe em $f"; exit 1; }; done
SPLASH=$OV/usr/share/luizinhoshowos/splash.raw
[ -f $SPLASH ] || { mkdir -p $(dirname $SPLASH); python3 tools/make_splash.py $SPLASH 270 || echo "(sem tela de boas-vindas: instale o Pillow no computador, pip install pillow)"; }
if [ $ALL = 1 ]; then SRC="."; else SRC="usr/local etc/init.d"; fi
KEY=${TARSOS_KEY:-$(ls keys/tarsos_ed25519 keys/luizinhoshowos_ed25519 2>/dev/null | head -1)}
[ -n "$KEY" ] || { echo "sem chave SSH: crie com  ssh-keygen -t ed25519 -f keys/tarsos_ed25519  (veja docs/INSTALL.md)"; exit 1; }
SSH="ssh -i $KEY -o UserKnownHostsFile=keys/known_hosts -o StrictHostKeyChecking=accept-new root@$IP"
COPYFILE_DISABLE=1 tar -C $OV --exclude .DS_Store --uid 0 --gid 0 --uname root --gname root -cf - $SRC | $SSH 'tar -C / -xf -'
$SSH 'for s in lsaudiod lsdesk lsjack lspiperd airplay airplay-meta spotify-api; do [ -x /etc/init.d/$s ] && { rc-update show default | grep -q " $s " || rc-update add $s default; }; done' >/dev/null
echo "enviado ($([ $ALL = 1 ] && echo overlay inteiro || echo código e serviços)) para $IP"
if [ $RESTART = 1 ]; then
  $SSH "rc-service lsjack restart; [ $AUDIO = 1 ] && rc-service lsaudiod restart; rc-service lsdesk restart" 2>&1 | sed 's/\x1b\[[0-9;]*[a-zA-Z]//g' | grep -E "Starting|erro|fail"
fi
