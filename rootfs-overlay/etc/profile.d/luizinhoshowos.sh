# Prompt do TarsOS
if [ "$(id -u)" = 0 ]; then PS1='\[\e[1;33m\]tarsos\[\e[0m\]:\w# '; else PS1='\[\e[1;33m\]tarsos\[\e[0m\]:\w$ '; fi

# Claude Code: o Echo é armv7, então usamos a última versão em JavaScript puro (2.1.112, via npm).
# O auto-update tentaria instalar a versão nativa (só x64/arm64) e quebraria o comando.
export DISABLE_AUTOUPDATER=1
# o ripgrep embutido não tem build armv7: usa o do sistema (apk add ripgrep)
export USE_BUILTIN_RIPGREP=0
