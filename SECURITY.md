# Segurança

> **In English:** please report vulnerabilities privately via GitHub Security Advisories (not a public issue). Never post API keys, tokens or the
> contents of `/etc/luizinhoshowos/`. The TARS agent runs behind a three-tier policy; bypasses of that policy are the most valuable reports.

## Como reportar

Se você achou uma falha de segurança, **não abra uma issue pública**. Use *Security → Report a vulnerability* na página do repositório (relatório privado) e inclua: o que é, como reproduzir, o impacto e, se tiver, uma sugestão de correção. Respondemos assim que possível; é um projeto de voluntários, sem SLA.

## O que é mais valioso

- **Contornar a política do TARS** (`lstools.classify`): conseguir que um comando de risco rode sem a confirmação na tela, ou que um comando *bloqueado* seja executado.
- ***Prompt injection*** que faça o TARS agir (página web, nome de aparelho, saída de comando, nota, mensagem de notícia). Tudo que volta das ferramentas é dado não confiável; se um texto externo consegue dar ordens, é bug.
- **Vazamento de segredos:** `/etc/luizinhoshowos/` (modo 700) e os `.json` de chaves (modo 600) nunca devem aparecer em respostas, logs, capturas de tela ou no git.
- **Escalonamento de privilégio** do usuário `user` (que roda os comandos do TARS) para root.
- Abusos do **FIFO de controle** (`/run/lsdesk.ctl`), da **transmissão da tela** (`:8080`, sem autenticação) ou dos sockets (`/run/lsaudio.sock`, `/run/lspiper.sock`) por outros usuários ou pela rede.

## O modelo de segurança do TARS, em uma página

- Comandos de shell rodam como **`user`**, nunca como root. Três níveis: `auto` (só leitura), `confirm` (SIM/NÃO na tela) e `block` (sudo, partições, segredos, `| sh`, reboot).
- A única via de root é `install_package` (apk), sempre com confirmação.
- Resultados de ferramentas, páginas e nomes de aparelhos são **dados não confiáveis**; o prompt manda ignorar instruções ali.
- O resumo de páginas do navegador roda **sem ferramentas e sem histórico**.
- Detalhes e limites em [docs/TARS.md](docs/TARS.md).

## Limitações conhecidas (leia antes de expor o aparelho)

- A **transmissão ao vivo da tela** (`http://<ip>:8080`) **não tem autenticação**: qualquer pessoa na sua rede vê a tela do Echo. Use numa rede de confiança ou bloqueie a porta no `nftables`.
- O `root` do aparelho entra por **chave SSH** (sem senha de login). Proteja a sua chave; **troque a senha do usuário `user`**.
- O microfone fica sempre ligado; o botão na tela desliga a escuta. Com `wake_mode: cloud`, trechos de fala vão ao Gemini para transcrição; use `local` se isso for um problema.
- O kernel (4.9.77) é antigo e não recebe correções de segurança do fabricante. **Não ponha o aparelho em redes hostis nem exposto à internet.**
- As chaves da Tuya (`tuya.json`) dão controle **local** dos aparelhos; o `tuya-sync` deliberadamente deixa de fora fechaduras e câmeras. **Não ligue a abertura de fechaduras à voz.**

## Se você publicou uma chave sem querer

Revogue na hora (Google AI Studio, Spotify, Tuya), gere outra e grave de novo com `tools/set_gemini_key.sh` / `tuya-sync`. Remover a chave de um commit **não** a apaga do histórico do git.
