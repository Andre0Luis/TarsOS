#!/bin/bash
# Grava a chave da API do Gemini no Echo (arquivo só do root, modo 600).
# Crie a chave em https://aistudio.google.com/apikey  ·  uso: tools/set_gemini_key.sh [ip-do-echo]
# Variáveis: TARSOS_HOST (padrão tarsos.local), TARSOS_KEY (padrão keys/tarsos_ed25519 ou keys/luizinhoshowos_ed25519).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ECHO="${1:-${TARSOS_HOST:-tarsos.local}}"
KEY_FILE="${TARSOS_KEY:-$(ls "$HERE"/../keys/tarsos_ed25519 "$HERE"/../keys/luizinhoshowos_ed25519 2>/dev/null | head -1)}"
[ -n "$KEY_FILE" ] || { echo "sem chave SSH em keys/ (veja docs/INSTALL.md)"; exit 1; }
read -rs -p "Cole a chave da API do Gemini (não aparece na tela): " KEY; echo
[ -n "$KEY" ] || { echo "chave vazia"; exit 1; }
printf '{"api_key": "%s"}\n' "$KEY" | ssh -i "$KEY_FILE" -o UserKnownHostsFile="$HERE/../keys/known_hosts" -o StrictHostKeyChecking=accept-new "root@$ECHO" \
  'umask 077; mkdir -p /etc/luizinhoshowos; cat > /etc/luizinhoshowos/gemini.json; chmod 600 /etc/luizinhoshowos/gemini.json; echo "chave gravada no Echo (modo 600)"'
