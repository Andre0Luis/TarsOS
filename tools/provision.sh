#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The TarsOS Authors
# TarsOS: prepara um Echo Show 5 que já roda postmarketOS (primeiro boot feito).
# Rode NO Echo, como root, com internet:   sh provision.sh [--yes] [--no-models]
# Ou do computador:                         ssh root@IP 'sh -s' < tools/provision.sh
#
#  1. instala os pacotes (apk) e o tinytuya (pip);
#  2. baixa os modelos de voz (Vosk pt-BR para a palavra de ativação e Piper pt_BR para a voz local);
#  3. cria as pastas de dados.
# Cada download é anunciado com tamanho e origem e pede confirmação (use --yes para aceitar tudo).
# Depois, do computador:  tools/deploy.sh --all   (envia o TarsOS)  e  tools/set_gemini_key.sh  (a chave do Gemini).
set -e
YES=0; MODELS=1
for a in "$@"; do case "$a" in --yes) YES=1 ;; --no-models) MODELS=0 ;; esac; done

ask() {                                   # ask "pergunta"  -> 0 se sim
  [ $YES = 1 ] && return 0
  printf '%s [s/N] ' "$1"; read -r r </dev/tty || return 1
  case "$r" in s|S|y|Y) return 0 ;; *) return 1 ;; esac
}

echo "== 1/3 pacotes (apk)"
grep -q 'edge/testing' /etc/apk/repositories || echo "aviso: o repositório edge/testing não está em /etc/apk/repositories; a voz local (libpiper) precisa dele."
apk update
apk add python3 py3-pillow py3-numpy py3-evdev py3-pyte py3-requests py3-pychromecast py3-zeroconf \
        py3-cryptography py3-colorama py3-pip sox ffmpeg espeak-ng shairport-sync vosk-api alsa-utils \
        font-dejavu tzdata nmap curl avahi i2c-tools iw htop git nano openssh bash bash-completion
apk add github-cli || echo "(github-cli opcional: não instalou)"
apk add libpiper || echo "(libpiper indisponível: a voz local ficará desligada)"
# tinytuya só para tomadas/lâmpadas Tuya; --no-deps porque as dependências já vieram do apk
pip install --break-system-packages --no-deps tinytuya || echo "(tinytuya opcional: não instalou)"

echo "== 2/3 pastas de dados"
mkdir -p /var/lib/luizinhoshowos /etc/luizinhoshowos /opt/vosk-models /opt/piper-voices /var/log
chmod 700 /etc/luizinhoshowos

if [ $MODELS = 1 ]; then
  echo "== 3/3 modelos de voz (downloads opcionais)"
  if [ ! -d /opt/vosk-models/vosk-model-small-pt-0.3 ] && \
     ask "Baixar o modelo Vosk pt-BR pequeno (palavra de ativação local, ~31 MB, alphacephei.com, Apache-2.0)?"; then
    cd /opt/vosk-models
    curl -L -sS --fail -o pt.zip https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip
    unzip -q -o pt.zip && rm -f pt.zip
  fi
  if [ ! -f /opt/piper-voices/pt_BR-faber-medium.onnx ] && \
     ask "Baixar a voz Piper pt_BR faber medium (voz local neural, ~63 MB, huggingface.co/rhasspy/piper-voices; veja a licença da voz no repositório)?"; then
    cd /opt/piper-voices
    B=https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium
    curl -L -sS --fail -O $B/pt_BR-faber-medium.onnx
    curl -L -sS --fail -O $B/pt_BR-faber-medium.onnx.json
  fi
fi
echo "pronto. Próximo passo no computador:  tools/deploy.sh --all  e  tools/set_gemini_key.sh"
