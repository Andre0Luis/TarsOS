# TarsOS: diário do projeto

> **Renomeado em 06/11/2026:** o sistema, antes chamado LuizinhoShowOS, agora se chama **TarsOS** (nome visível: tela, boot, AirPlay, hostname `tarsos`, prompt e prompt do TARS). Por segurança dos dados e das chaves, os **caminhos internos continuam `luizinhoshowos`** (`/etc/luizinhoshowos`, `/var/lib/luizinhoshowos`, `/usr/local/lib/luizinhoshowos`, `/run/luizinhoshowos`, `/etc/luizinhoshowos-release` e a pasta do projeto). Os textos históricos abaixo foram atualizados para o nome novo.

> **TarsOS 0.2.0 "Companheiro"**, 06/11/2026 (0.1.0 "Primeiro Boot" foi em 05/11/2026)
> Linux nativo, sem Android e sem Alexa, no **Amazon Echo Show 5 (1ª geração, 2019)**.
> <sub>Baseado em postmarketOS (Nura edge) e Alpine Linux · kernel Linux 4.9.77 (downstream Amazon/MediaTek) · bootloader amonet.</sub>

Este é o documento principal do projeto, com três partes: **o que estamos fazendo**, **o que fizemos** e **o que aprendemos**.
O [MANUAL.md](MANUAL.md) é o rascunho do começo do projeto. Quando os dois divergirem, vale este aqui.

---

## 1. O que estamos fazendo

**Objetivo:** transformar o Echo Show 5 num computador Linux de verdade, controlado por nós, em vez de um aparelho Android/Alexa da Amazon.

**Estratégia:** aproveitar peças que já existem (o desbloqueio *amonet* e o porte do *postmarketOS* para este aparelho), fazê-las funcionar juntas e dar ao sistema identidade e ferramentas próprias.

### Estado atual (rumo à v0.2.0)

| Item | Estado | Observação |
|---|---|---|
| Boot do Linux | ✅ funciona | ~30 s até o SSH responder |
| Shell root pela **USB serial** | ✅ | aparece no Mac como `TarsOS` → `/dev/cu.usbmodem*` |
| **WiFi** (MT7668) | ✅ | conecta sozinho na rede de casa; IP atual `<IP-do-Echo>` |
| **SSH** (dropbear) | ✅ | root com chave dedicada |
| Hora | ✅ | NTP + RTC (`hwclock -w`) |
| **Tela** (framebuffer) | ✅ | desenho direto em `/dev/fb0`; splash no boot |
| **Terminal na tela** (TarsOS Terminal) | ✅ | bash + teclado touch; serviço `lsterm` no boot |
| Touch (Goodix gt9xx) | ✅ | mapeamento validado (`ls -la` digitado na tela) |
| bash 5.3 / curl 8.23 | ✅ | bash é o shell padrão do `root` e do `user` |
| Homebrew | ❌ não suportado | o brew só roda em Linux x86_64/ARM64 com glibc; aqui é armv7 + musl → use `apk` |
| Node.js 24 / npm 12 | ✅ | `apk add nodejs npm` (o `nvm` baixa x64 → `Exec format error`) |
| **Claude Code 2.1.112** | ✅ | `npm i -g @anthropic-ai/claude-code@2.1.112`, a última versão em JS puro |
| Área de trabalho (`lsdesk`) | ✅ | início com relógio, 6 apps, multijanelas, tela dividida, livestream `:8080` |
| Terminal da tela como `user` | ✅ | mesmo `HOME`/login do Claude que o SSH; `sudo` para root; tecla **▾** minimiza o teclado |
| **Alto-falante** | ✅ | RT5616 → LOUT → amplificador externo; `audio-speaker [0-39]`; volume salvo |
| Fone P2 (3,5 mm) | ✅ | saída HP do RT5616; o serviço `lsjack` muda sozinho entre fone e alto-falante (não testado com fone real) |
| Microfones (2× TLV320AIC3101) | ❔ | próximo passo |
| **AirPlay** (receptor) | ✅ | Mac → alto-falante do Echo; `shairport-sync` → `sox` → `aplay` (ver seção 3) |
| **Player de música** (app *Música*) | ✅ | capa, título, artista, progresso e botões ◀◀ ❚❚ ▶▶ Vol±, via **Spotify Web API** |
| Spotify Connect (`librespot`) | ❌ desligado | o Spotify recusa a chave de áudio da conta (issue #1649); instalado mas fora do boot |
| **Gemini CLI 0.67** | ✅ | `npm i -g @google/gemini-cli` (bundle JS puro, Node ≥ 20); login ainda não feito |
| **TARS** (robô de Interestelar) | ✅ visual e chat · 🔑 falta a chave do Gemini | app *TARS* na tela: monólito animado, barras de **humor/honestidade** (toque para ajustar), chat em PT-BR; cérebro = API do Gemini (`lsbrain.py`) |
| Teclado: camada de acentos (`Áç`) | ✅ | á à â ã é ê í ó ô õ ú ü ç ñ |
| Console de texto do kernel / `fbkeyboard` | ❌ | kernel sem `fbcon` (resolvido com o nosso terminal) |
| Bluetooth / GPU 3D | ❌ | segundo a wiki do pmOS |

### Como usar

```bash
# SSH pela rede
ssh -i ./keys/luizinhoshowos_ed25519 \
    -o UserKnownHostsFile=./keys/known_hosts \
    root@<IP-do-Echo>

# Pelo cabo USB (serial), usando o nosso cliente
./tools/echosh.py 'showos'
./tools/echosh.py --push arquivo_local /caminho/no/echo
```

> O `echosh.py` precisa do `pyserial`. Hoje ele roda com o venv do scratchpad desta sessão. Para uso permanente, crie um venv no projeto: `python3 -m venv .venv && .venv/bin/pip install pyserial pillow`.

Comandos dentro do TarsOS:

| Comando | Faz |
|---|---|
| `showos` | mostra nome, versão, base, kernel, IP, memória e disco |
| `to-recovery` | reinicia no **TWRP** sem apertar botão (grava `boot-recovery` na `MISC`) |
| `to-fastboot` | reinicia no **fastboot liberado** (grava `FASTBOOT_PLEASE` na `MISC`) |
| `nmcli` | WiFi / rede (NetworkManager) |
| `apk add <pacote>` | instala programas (repositórios Alpine + postmarketOS) |

Usuários:
- `root`: sem senha de login; entra só pela chave SSH ou pela serial.
- `user`: senha provisória **alterada em 05/11/2026** (definida pelo dono; nunca registre senhas aqui). Troque com `passwd user`.

---

## 2. O que fizemos (cronologia)

### 26/10/2026: planejamento e primeira tentativa de build
1. Confirmamos que o aparelho é o `checkers` (MT8163) e que o postmarketOS tem porte para ele (categoria *downstream*).
2. Montamos o **pmbootstrap** num contêiner Docker no Mac (`Dockerfile` em `./`), porque ele não roda direto no macOS.
   - O PyPI exigia Python novo → instalamos do git num Debian trixie.
   - O pmbootstrap não roda como root → criamos o usuário `pmos` com sudo.
3. Configuração: canal `edge`, device `amazon-checkers`, **OpenRC** (o systemd exige kernel ≥ 5.4), UI `fbkeyboard`, extras `openssh nano htop curl iw`, `America/Sao_Paulo`, `pt_BR`.
4. O build falhou com `File did not appear: /dev/loop0p2` (o Docker não cria os nós de partição do loop) e o disco do Mac lotou. Paramos.

### 05/11/2026: build, gravação, diagnóstico e primeiro boot
5. Com espaço liberado, refizemos o build com um vigia que cria os nós `/dev/loopNpM` via `mknod` (`out/mknod-watch.sh`). O build saiu: `boot.img` (14 MB) e `amazon-checkers.img` (1,1 GB).
6. **Backup** do `boot` e do `recovery` do LineageOS via `adb root` + `dd`, com SHA-256 conferido nos dois lados (`out/backup/`).
7. O `fastboot` comum estava **travado** (`unlock_status: false`, `restricted on locked hw`). Pelo código do amonet descobrimos o **fastboot liberado**: `handshake2.py FACTFACT` com o aparelho em USBDL (Mute + Volume Down) → `unlock_status: true`.
8. Gravamos `boot` e `userdata` (602 s). **O aparelho travou no logo "amazon".**
9. **Diagnóstico** (sem UART, com o aparelho alternando entre TWRP e tentativas de boot):
   - `expdb` vazia, sem crash registrado.
   - Superbloco ext4 sem montagem nova → o Linux nem chegava a montar o root.
   - `pstore/console-ramoops` do kernel do TWRP como "caixa-preta".
   - Testes cruzados: kernel do TWRP + ramdisk do pmOS, initramfs enxuto (3,9 MB em XZ), cmdline curto…
   - Lendo `/proc/cmdline` no TWRP: **o LK injeta ~840 caracteres**. O cmdline do pmOS (~215) **estourava o limite de 1024**.
10. **Correção:** reescrevemos só o campo de cmdline do cabeçalho do `boot.img` (151 caracteres) e regravamos pelo TWRP com `dd`, preservando o microloader do amonet. **O Linux bootou.**
11. Acesso: o pmOS usa RNDIS e o macOS não suporta → trocamos o USB para **serial CDC-ACM** com um shell root no `ttyGS0` (`inittab`).
12. **WiFi** conectado (NetworkManager), **SSH** com chave, **hora** via NTP.
13. Desenhamos a tela de boas-vindas no framebuffer (formato RGBA, rotação 270°).
14. Identidade **TarsOS 0.1.0**: hostname, `/etc/luizinhoshowos-release`, `motd`, prompt, nome USB, splash no boot e o comando `showos`.
15. Teste de reboot: tudo volta sozinho.
16. **TarsOS Terminal** (`/usr/local/lib/luizinhoshowos/lsterm.py`), **o primeiro código nosso rodando no Echo**:
    - Python + `pyte` (emulação VT100/xterm) + `Pillow` (desenho) + `evdev` (touch e botões), todos instalados com `apk`;
    - roda o bash num pty, desenha a tela em paisagem e grava no `/dev/fb0` girado 270°;
    - teclado na tela (letras, números, `Sym`, Ctrl, Shift, Esc, Tab, setas), `⌨` esconde o teclado;
    - **Volume +** mostra/esconde o teclado, **Volume −** envia Ctrl+C;
    - serviço OpenRC `lsterm` com `supervise-daemon` (reinicia se cair); log em `/var/log/lsterm.log`;
    - cerca de 90 ms por quadro (~10 fps), grade de 96×10 com teclado.
17. `apk add bash bash-completion`, e o bash virou o shell padrão. O `fbkeyboard` saiu do boot.
18. **Claude Code 2.1.112** instalado (última versão em JS puro; o `install.sh` oficial não suporta armv7).
19. **Áudio no alto-falante**: receita do RT5616 + controle `Ext_Speaker_Amp_Switch` invertido (seção 3). Volume padrão 22/39, salvo entre boots.
20. **AirPlay**: `shairport-sync` do Alpine → `sox` (44,1→48 kHz) → `aplay`. O primeiro desenho (ALSA direto) causou um **kernel panic** (bug do driver, seção 3).
21. **Spotify**: `librespot` (Connect) funcionou só até o login e foi recusado pelo Spotify; trocado pela **Web API** (OAuth PKCE), que mostra e controla o player sem tocar o áudio.
22. **Player** na área de trabalho: abre sozinho ao começar a tocar, com capa, progresso e controles.
23. **Gemini CLI 0.67.0** instalado.
24. **TARS** (trilha B, etapas B1 e B2): `lsbrain.py` (personalidade + Gemini REST + histórico) e o app `TarsApp` no `lsdesk` (monólito de 4 segmentos que respira, caminha ao pensar e pulsa ao falar; mostrador âmbar; barras de humor/honestidade; chat). O teclado ganhou a camada de acentos e a área de trabalho passou a redesenhar ~8×/s apenas enquanto um app animado está visível (~17% de um núcleo).

---

## 3. O que aprendemos

### Sobre o bootloader e o amonet
- **Existem dois fastboots.** O que se alcança com os três botões ou `adb reboot bootloader` é o **original da Amazon, travado**. O **liberado** vem pelo `handshake2.py FACTFACT` (amonet) ou por `to-fastboot`. Teste rápido: `fastboot getvar unlock_status` → `true`.
- Este LK **não tem `fastboot boot`** (`unknown command`), então não dá para testar um kernel só na RAM. Tem que gravar.
- **Layout do amonet na partição `boot`:** stub do microloader no offset 0, **cópia do nosso cabeçalho no offset 1024**, e kernel e ramdisk inalterados a partir do offset 2048. O `fastboot flash boot` faz essa injeção sozinho. Ao gravar pelo TWRP com `dd`, é preciso manter esse layout (editamos dentro da imagem já injetada).
- **Nunca gravar `lk`, `preloader`, `tee1`/`tee2`**: brick permanente, sem volta.
- O amonet escolhe o modo de boot pelos primeiros 16 bytes da partição `MISC`: `boot-recovery` → TWRP, `FASTBOOT_PLEASE` → fastboot liberado.

### A descoberta principal: limite do cmdline
- O LK prefixa **~840 caracteres** (tela/LCM, serial, versões, `androidboot.*`). O kernel ARM aceita **1024**.
- O cmdline padrão do postmarketOS (`quiet splash plymouth.* … pmos_boot_uuid=… pmos_root_uuid=…`, ~215) **estoura o limite**, e o boot morre **antes** do kernel iniciar: fica no logo "amazon" e o watchdog reinicia em ~60 s.
- **Regra:** nosso cmdline tem que ficar em **≤ ~180 caracteres**. Hoje usamos:
  `bootopt=64S3,32N2,32N2 pmos_boot_uuid=18771442-4252-4ecd-8a7f-08651ef065f4 pmos_root_uuid=feb072db-f590-4471-ae48-c0faa77621f1 pmos_rootfsopts=defaults`
- Os primeiros suspeitos (tamanho do ramdisk, kernel do pmOS) eram **falsos**. O kernel do pmOS sempre funcionou.

### Técnicas de diagnóstico sem UART
- **ramoops** (`/sys/fs/pstore/console-ramoops`): guarda o log do boot anterior **se não faltar energia**. Para preservar, reinicie a quente (watchdog ou comando) e entre no TWRP segurando o Volume Up **sem tirar da tomada**. Com falta de energia o log fica embaralhado.
- **Contador de montagem do ext4** (`mount_count` no superbloco, offset 52): compara a imagem original com o disco e mostra se o Linux chegou a montar o root.
- **Trocar peças entre imagens que funcionam e que não funcionam** (kernel do TWRP × ramdisk do pmOS) isola a causa.

### Sobre o hardware e os kernels
- Kernel do **TWRP**: 4.9.77, 32 bits, **sem `devtmpfs`**, com `android_usb` (acm, rndis, eem, ffs…), sem WiFi.
- Kernel do **pmOS**: 4.9.77, 32 bits, com `devtmpfs`, `VT`, WiFi **MT7668** (`wlan0`). **Sem `fbcon`**, por isso o `fbkeyboard` não sobe.
- Kernel do **LineageOS**: 64 bits (`bootopt=…64N2`).
- **Framebuffer:** 480×960 retrato (tela física 960×480 paisagem), 32 bpp **RGBA**, 3 páginas. Para paisagem legível, girar a imagem **270°**.
- O `adbd` do TWRP é estático, mas sem o serviço de propriedades do Android rebaixa para o uid 2000.

### Sobre o áudio (a parte mais difícil até agora)
- **Cadeia do alto-falante:** DL1 → I2S1 (pinos 72/73/74, função 4) → **RT5616** (`hw:0,23`, `RT5616_Playback`) → **LOUT diferencial** → **amplificador externo** com enable no **pino 35**.
- 🔑 **`Ext_Speaker_Amp_Switch` é INVERTIDO:** `Off` = pino 35 baixo = **amplificador ligado**; `On` = mutado. O driver do RT5616 (`realtek,amp-gpio`) ainda sobe o pino 35 no boot, então o alto-falante nasce mutado.
- A receita veio do `audio_device_rt5616.xml` do Echo Show 5 de 2ª geração (`amazon-oss/android_vendor_amazon_cronos`). O `audio_device.xml` do checkers é o genérico da MediaTek e não ajuda.
- `Audio_I2S1_Setting On` coloca os pinos 72–74 em modo I2S (`pinmux-pins` mostra `func4`).
- O fone P2 sai pela HP do RT5616. Ele funcionar com o alto-falante mudo foi o teste que isolou o problema no amplificador.
- **Ferramentas de diagnóstico seguras:** `debugfs/asoc/.../dapm` (quais blocos ligam), `debugfs/regmap/2-001b/registers` (registradores do RT5616), `debugfs/pinctrl/.../pinmux-pins` (função dos pinos), `debugfs/gpio`. O `i2cset -f` no `/dev/i2c-2` escreve direto no codec.
- ⚠️ **Não varrer o mixer inteiro com `amixer cget`:** alguns controles da MediaTek (timestamps, SPI) agem no hardware ao serem lidos, e isso já travou o kernel.
- O kernel do pmOS não tem `/dev/mem` (`CONFIG_DEVMEM` desligado).
- Os device trees de áudio do LineageOS, do TWRP e do pmOS são idênticos.

### Sobre AirPlay, Spotify e Gemini
- 🔥 **Bug do driver de áudio da MediaTek:** `mtk_pcm_I2S0dl1_copy` arredonda cada escrita para múltiplos de 64 bytes; se o tamanho do buffer circular não for múltiplo de 64, a escrita passa do fim e dá `Oops: arm_copy_from_user` → **kernel panic** (o Echo trava e reinicia pelo watchdog). Soluções: **nunca** escrever no PCM com tamanhos arbitrários. Usamos `aplay` com período 2400 e buffer 9600 quadros (múltiplos de 16 quadros = 64 bytes). O `shairport-sync` **não** pode falar direto com o ALSA.
- O I2S roda a 48 kHz. Tocar 44,1 kHz direto soa mal; o `sox ... rate -m` converte antes do `aplay`. O Alpine compila o `shairport-sync` **sem FFmpeg**, então só gera 44,1 kHz.
- O **AirPlay clássico** (`_raop._tcp`) basta: o Echo aparece no menu de som do Mac sem AirPlay 2. Quando ele sumiu da lista, a causa era o `shairport-sync` que tinha caído (erro de parâmetros do ALSA); com o serviço supervisionado (`airplay`) ele volta sozinho.
- O som do sistema via AirPlay **não traz metadados** (só áudio). Por isso o player usa a Web API do Spotify para mostrar música/capa.
- **Spotify Connect (`librespot`)** conecta e anuncia, mas contas mais novas recebem `audio key error 0 1` (Spotify bloqueia; issue librespot #1649). Não há correção do nosso lado. Contas antigas ou de plano família podem funcionar.
- **Spotify Web API (PKCE):** não precisa de Client Secret. O `tools/spotify_auth.py <client_id>` abre o login no Mac e grava o refresh token no Echo (`/etc/luizinhoshowos/spotify-api.json`, modo 600). O serviço `spotify-api` faz *poll* a cada 2 s e aceita comandos no FIFO `/run/luizinhoshowos/spotify-cmd` (`toggle`, `next`, `prev`, `volup`, `voldown`).
- **Gemini CLI:** o pacote é um bundle JS puro (`bundle/gemini.js`); `node-pty` e `keytar` são opcionais (scripts de instalação bloqueados, sem problema). A primeira execução leva ~15 s no A53.

### Sobre o TARS (cérebro e personalidade)
- **Chave do Gemini:** crie em https://aistudio.google.com/apikey e grave no Echo com `tools/set_gemini_key.sh` (pede a chave sem mostrar na tela e grava `/etc/luizinhoshowos/gemini.json`, modo 600). Não cole a chave em chats.
- O app chama a API REST direto (`generativelanguage.googleapis.com`, cabeçalho `x-goog-api-key`), não o `gemini` CLI: o CLI leva ~15 s só para abrir no A53.
- **Modelos (medido em 06/11/2026 com a chave do projeto, plano gratuito):** `gemini-2.5-*` dão **404** (saíram do ar); `gemini-3.7-flash`, `3.8-flash` e o alias `gemini-flash-latest` estavam **congestionados** (timeout/503); `gemini-3.1-pro-preview` dá **429** (sem cota gratuita para o Pro). O padrão é **`gemini-3.5-flash`** (~3 s, melhor equilíbrio), com reserva `gemini-3.1-flash-lite` (~1 s) e `gemini-3.6-flash` (~5 s). Se todos falharem, o `lsbrain` lista os modelos da chave e tenta outros Flash.
- **`thinkingBudget: 0` é obrigatório:** com raciocínio ligado o modelo gasta ~380 tokens pensando e a frase sai cortada. O `gemini-3.5-flash-lite` recusa o campo (HTTP 400), então o código tenta de novo sem ele.
- A **assinatura Gemini Plus** (app do Google) **não dá cota de API**; a chave do AI Studio é cobrada à parte (plano gratuito, ou faturamento no Google Cloud para liberar o Pro).
- `thinkingConfig.thinkingBudget = 0` corta a latência; se o modelo recusar (HTTP 400), o código tenta de novo sem esse campo.
- **Humor e honestidade (0–100)** entram no prompt de sistema (faixas: <25, <55, <85, ≥85). Ajuste tocando nas barras, ou digitando "ajuste o humor para 40%" (comando local, sem gastar chamada à API).
- "apague a conversa" limpa o histórico (guardado em `/var/lib/luizinhoshowos/tars-history.json`, últimas 12 mensagens).
- Teste do cérebro sem a tela: `GEMINI_API_KEY=... python3 lsbrain.py "que horas são?"`.
- O prompt proíbe markdown e emojis (o texto vai para uma tela pequena e, depois, para a voz) e limita a 2 frases.

### Sobre o terminal na tela
- Touch `goodix-ts`: multitoque (protocolo B, slots), X 0–480 e Y 0–960 no sentido do painel. Na tela paisagem: `x = Y_bruto`, `y = 480 − X_bruto`.
- `Image.transpose(ROTATE_270)` é bem mais rápido que `rotate(270)` para girar cada quadro.
- No `pkill -f`, use um padrão que não apareça no próprio comando SSH (`^python3 /usr/local/lib/...`), senão a sessão se mata.

### Sobre usuários e o terminal da tela
- O Claude Code guarda o login por usuário (`~/.claude/.credentials.json`). Por isso o terminal da tela roda como `user`, e não como root.
- Ao rodar um shell num pty como outro usuário, é preciso dar `chown` do `/dev/pts/N` para ele antes do `setuid`, senão o `sudo` não consegue abrir o `/dev/tty`.
- O app **Ao Vivo** continua como root (`dmesg -w`, `logread -f`).

### Sobre o Claude Code no armv7
- O `install.sh` oficial só aceita x64/arm64 (`Unsupported architecture: armv7l`).
- No npm, a partir da **2.1.113** o pacote virou wrapper de binário nativo (`bin/claude.exe` + pacotes por plataforma, sem armv7). A **2.1.112** é a última com `cli.js` em JavaScript puro (Node ≥ 18), e roda no Node do Alpine.
- Variáveis em `/etc/profile.d/luizinhoshowos.sh`: `DISABLE_AUTOUPDATER=1` (impede a "atualização" para a versão nativa) e `USE_BUILTIN_RIPGREP=0` (usa o `rg` do apk).
- O `nvm` não serve: não existe build oficial do Node para armv7 + musl. Use `apk add nodejs npm`.

### Sobre o ambiente no Mac
- O macOS **não suporta RNDIS**. Usamos **CDC-ACM** (serial), que é nativo. NCM e ECM também seriam nativos.
- No Docker Desktop os nós de partição de loop não aparecem. Resolvemos com o vigia `mknod`.
- O pmbootstrap gera cerca de 10 GB de chroots e cache. Mantenha ~15 GB livres.
- O postmarketOS agora se chama **Nura** (`/etc/os-release`, `wiki.nura.eco`). A wiki fica atrás do Anubis, então leia pelo navegador.

---

## 4. Arquitetura do TarsOS

```
┌──────────────────────────────────────────────────────────────┐
│ TarsOS: identidade, scripts, splash   (nosso)        │
├──────────────────────────────────────────────────────────────┤
│ postmarketOS/Nura edge + Alpine: OpenRC, NetworkManager,     │
│ dropbear, python3, apk                       (pronto)        │
├──────────────────────────────────────────────────────────────┤
│ initramfs do postmarketOS                    (pronto)        │
├──────────────────────────────────────────────────────────────┤
│ kernel Linux 4.9.77 linux-amazon-checkers    (pronto)        │
├──────────────────────────────────────────────────────────────┤
│ cabeçalho boot.img: cmdline curto (151)      (CORRIGIDO)     │
├──────────────────────────────────────────────────────────────┤
│ microloader amonet + LK + preloader          (NÃO TOCAR)     │
└──────────────────────────────────────────────────────────────┘
Partições: boot (p9) = kernel+initramfs │ userdata (p16) = GPT interna com pmOS_boot + pmOS_root
           recovery (p10) = TWRP (intacto) │ system (p12) = LineageOS antigo (intacto, não usado)
```

---

## 5. Arquivos do projeto (`./`)

| Caminho | O que é |
|---|---|
| `TarsOS.md` | **este documento** |
| `MANUAL.md` | rascunho inicial (histórico) |
| `Dockerfile` | ambiente do pmbootstrap (contêiner `pmos`, volumes `pmos-work`/`pmos-cfg`) |
| `luizinhoshowos/images/boot-luizinhoshowos-0.1.0.img` | **imagem de boot oficial** (já com microloader e cmdline curto) |
| `luizinhoshowos/rootfs-overlay/` | tudo o que adicionamos ao sistema (copiado para `/`) |
| `luizinhoshowos/tools/echosh.py` | cliente da serial USB (comandos e envio de arquivos) |
| `luizinhoshowos/tools/make_splash.py` | gera a tela de boas-vindas (`splash.raw`) |
| `luizinhoshowos/keys/` | chave SSH dedicada + `known_hosts` (**não compartilhar**) |
| `out/amazon-checkers.img` | rootfs gerado pelo pmbootstrap (vai no `userdata`) |
| `out/backup/boot.img`, `recovery.img` | **backup do LineageOS/TWRP**, o caminho de volta |
| `out/diag/` | imagens de teste, logs (ramoops, dmesg) e o initramfs extraído |

SHA-256 de referência:
```
be804d72f5cb1602cfa9aeabe1b955b6a705d190d14363e4df5e3ba2176250bd  images/boot-luizinhoshowos-0.1.0.img
40219d380954057b6dec74f7e4e00a10aef05fc1b3f138d8c368ae20d96b354f  out/amazon-checkers.img
5c5fd8026c4e47e2188c117493c0502e397964602fc460baf435cc5e69cdf0d1  out/backup/boot.img (LineageOS)
e4883903693d5d99e002744e3f72670f413cbaefdac8fb1344d2abe432b17b45  out/backup/recovery.img (TWRP)
```

---

## 6. Como reinstalar do zero

1. **Fastboot liberado:** rode `handshake2.py FACTFACT` (repo amonet, venv com `pyserial`) e coloque o Echo em USBDL (Mute + Volume Down ao ligar, com o USB conectado). Confira com `fastboot getvar unlock_status` → `true`.
2. **Grave o rootfs:** `fastboot flash userdata out/amazon-checkers.img` (~10 min).
3. **Grave o boot:** a imagem oficial **já tem o microloader**, então grave pelo TWRP com `dd`, não por fastboot (o fastboot injetaria o microloader de novo):
   ```bash
   adb push luizinhoshowos/images/boot-luizinhoshowos-0.1.0.img /tmp/b.img
   adb shell 'dd if=/tmp/b.img of=/dev/block/platform/soc/by-name/boot bs=1M && sync'
   ```
   Para gravar por fastboot, use um `boot.img` sem microloader, com o cmdline já curto.
4. Reinicie e espere a serial `TarsOS`. Depois aplique o `rootfs-overlay/` e a linha do `inittab` (seção 2, item 11), se o rootfs for novo.

### Voltar ao LineageOS
No TWRP: `dd` do `out/backup/boot.img` para a partição `boot`, depois **Wipe → Format Data** (o `userdata` tem o Linux) e reinicie.

---

## 7. Cuidados e pendências conhecidas

- ⚠️ **Atualizações de kernel (`apk upgrade` do `linux-amazon-checkers`)** geram um `boot.img` novo em `/boot` **com o cmdline longo**. Verificado em 06/11: o `boot-deploy` do aparelho **não grava** a partição `boot` (`deviceinfo_flash_kernel_on_update` não está ligado), então um `apk upgrade` só troca arquivos em `/boot` e o Echo continua iniciando. O risco só existe se você gravar esse `boot.img` à mão (`fastboot flash`/`dd`): aí o cmdline longo trava no logo. Grave sempre a nossa imagem dourada (`boot-luizinhoshowos-0.1.0.img`) ou encurte o cmdline antes.
- O `userdata` agora é o Linux. O LineageOS só volta com o procedimento da seção 6.
- O IP vem do DHCP do roteador (`<IP-do-Echo>`). Para fixar, reserve no roteador ou use `nmcli`.
- A senha do `user` ainda é provisória (alterada em 05/11).

---

- A senha do `user` ainda é provisória. O `sudo` pede a senha dele.
- A troca automática fone/alto-falante (`lsjack`) foi testada só com estados simulados; falta o teste com um fone de verdade.
- **Cuidado com o áudio:** qualquer programa novo que escreva direto no `hw:0,23` com buffers fora do padrão pode derrubar o kernel (bug do driver). Passe sempre pelo `aplay` com os parâmetros da seção 3.

## 8. Roadmap

Visão: o TarsOS vira um **companheiro de mesa**: uma tela com relógio, música, casa e um **robozinho** com quem conversar, rodando num Linux nosso, sem nuvem da Amazon.

### Trilha A: interface gráfica (`lsdesk`)
| Versão | Entrega |
|---|---|
| **0.2.0** "Som na Caixa" | fechar o que existe: splash e `showos` novos, docs, release com `spotify-api`/AirPlay/player |
| **0.2.1** | **Painel rápido**: deslizar do topo abre brilho, volume, WiFi, mudo, modo noturno |
| **0.2.2** | **Protetor de tela / modo ambiente**: relógio grande, capa da música tocando, brilho automático pelo sensor de luz (`m_alsps_input`) e tela apagando por inatividade |
| **0.2.3** | **App Rede (WiFi)**: buscar redes, conectar, ver IP e sinal (hoje só via `nmcli`) |
| **0.3** | **Widgets** na tela de início: clima, agenda, timers/alarmes, notificações |
| **0.4** | **Arquivos** e **Editor de texto** simples; abrir e rodar scripts pela interface |
| **0.5** | **Apps externos**: manifesto por app (`/usr/share/luizinhoshowos/apps/*.toml`), lançador dinâmico, janelas redimensionáveis |
| **0.6** | **Home Assistant**: painel de dispositivos e cenas, controle de luzes |
| depois | câmera (2 MP, hoje "não testada"), sensores, relógio despertador |

### Trilha B: o robozinho 🤖
Um personagem animado (olhos, sobrancelhas, boca) que vive na tela, reage ao que acontece e conversa.

| Etapa | Entrega |
|---|---|
| **B1: rosto** ✅ (versão TARS: monólito articulado em vez de olhos; falta o modo ocioso) | app e modo ocioso com **olhos animados** (piscar, olhar para os lados, dormir, sorrir, pensar), desenhados direto no framebuffer, 15 fps. Expressões: neutro, feliz, curioso, pensando, falando, dormindo |
| **B2: chat por texto** ✅ (falta a chave) | balão de conversa + teclado touch; o robô responde por um **LLM**. Opções de cérebro: **Gemini** (API ou `gemini -p`), **Claude** (`claude -p` ou API). Personalidade definida em um prompt de sistema (nome, tom, humor) |
| **B3: voz de saída** | falar as respostas com **TTS** local (`espeak-ng` agora; `piper` se rodar em armv7 depois) pelo alto-falante, com o rosto animando a boca |
| **B4: microfones** | ativar os 2 microfones (TLV320AIC3101, `Audio_MicSource`, ADC) e gravar áudio com `arecord` |
| **B5: voz de entrada** | **STT**: reconhecimento na nuvem (barato e rápido) ou `whisper.cpp tiny` local (lento no A53, só frases curtas); botão "falar" primeiro, **palavra de ativação** depois |
| **B6: ações** | o robô executa pedidos: tocar música (Spotify/AirPlay), ajustar volume e brilho, falar a hora, criar timers, acender luz (Home Assistant) |
| **B7: memória** | lembrar fatos e preferências (arquivo local), humor que muda ao longo do dia |

**Decisões que eu preciso de você:** (1) qual **cérebro** usar primeiro (Gemini ou Claude) e como guardar a chave de API com segurança; (2) o **nome** e a **personalidade** do robô; (3) se a voz sai em **português** (o `espeak-ng` fala PT-BR, robótica; o `piper` soa melhor).

### Trilha C: assistentes de linha de comando
| Item | Estado |
|---|---|
| Claude Code 2.1.112 | ✅ instalado e logado (versão em JS puro; sem atualização, ver seção 3) |
| **Gemini CLI 0.67** | ✅ instalado · **falta o login**: no terminal do Echo rode `gemini`; sem navegador local, escolha o login com Google e copie o link para o Mac (ou use `GEMINI_API_KEY`) |
| Ponte entre eles e o robô | `claude -p` / `gemini -p` (modo não interativo) como "cérebro" do B2 |

### Trilha D: sistema e robustez
| Item | Por quê |
|---|---|
| ~~Cmdline curto permanente~~ | **não é preciso**: o `boot-deploy` não regrava a partição `boot` (ver seção 7) |
| ~~Troca automática fone/alto-falante~~ | ✅ `lsjack` + `audio-speaker apply` (lê `/sys/class/switch/h2w/state`); falta testar com fone real |
| ~~Script de deploy~~ | ✅ `luizinhoshowos/tools/deploy.sh [--all] [--no-restart] [--audio] [IP]` |
| **Watchdog de aplicativos** e log rotativo | `lsdesk` e serviços já reiniciam sozinhos; falta guardar logs |
| **Backup/restauração** do `/etc/luizinhoshowos` e do overlay | reinstalar sem perder configuração |
| Atualização por script (`apk upgrade` seguro) | pacotes de segurança sem quebrar o boot |
| Kernel próprio com `fbcon`, `/dev/mem` e `amp_control` no device tree | longo prazo: console na tela e áudio sem remendos |

### Ordem sugerida
1. **0.2.0** (fechar, 1 sessão)
2. **B1 + B2** (rosto + chat por texto com Gemini/Claude) ← o mais divertido
3. **0.2.1 + 0.2.2** (painel rápido e modo ambiente)
4. **B3** (voz de saída) → **B4 + B5** (microfones e voz de entrada)
5. Trilha D em paralelo, com a prioridade no cmdline permanente

---

## 9. Release 0.2.0 "Companheiro" (06/11/2026): referência completa

O Echo deixou de ser só um Linux com tela e virou um **companheiro de mesa**: o **TARS** ouve, fala, pesquisa, mexe no sistema
(com a sua autorização) e controla a casa; a tela ganhou 13 apps (agora 18, ver 9.9 e 9.10).

### 9.1 As 10 funcionalidades

| # | Funcionalidade | Estado | Onde |
|---|---|---|---|
| 1 | **TARS com ferramentas** (agente) | ✅ testado | `lsbrain.py` + `lstools.py` |
| 2 | **Escuta contínua** ("TARS...") com feedback tipo Alexa | ✅ pipeline testado; falta teste com sua voz | `VoiceLoop` em `lsdesk.py`, `lsvoice.py` |
| 3 | **Voz natural** (Gemini TTS, voz Charon) + saudação ao abrir | ✅ (cota do TTS é limitada: ver 9.4) | `tars-say` |
| 4 | **Varredura da rede e reconhecimento de aparelhos** + app **Casa** | ✅ testado na sua rede | `lstools.scan_lan`, `HomeApp` |
| 5 | **Casa inteligente local** (WiZ, Yeelight, Shelly, Tasmota, Kasa, Chromecast/Nest, **Tuya**, Home Assistant) | ✅ drivers; **nenhum aparelho controlável ainda**: Tuya/Ekaza espera o pareamento no app Tuya + `tuya-sync` | `lshome.py` |
| 6 | **Timers e alarmes** + app **Relógio** | ✅ testado (criado pelo TARS, tocou) | `lstools`, `ClockApp`, overlay de alarme |
| 7 | **Clima** (widget na tela de início + app **Clima** + ferramenta) | ✅ testado | Open-Meteo, `WeatherApp` |
| 8 | **Rádio online** + app **Rádio** | ✅ testado (CBN tocou e parou) | radio-browser + `ffmpeg` |
| 9 | **Brilho automático** pelo sensor de luz (JSA1214) | ✅ funcionando; escala não calibrada | `Desktop.auto_brightness_tick` |
| 10 | **Notas por voz** + app **Notas** | ✅ | `note_add`, `NotesApp` |

Extras: **modo ocioso** (relógio grande + TARS, tela escurece), **confirmação na tela** (SIM/NÃO), **mixador de áudio próprio**
(`lsaudiod`), Gemini CLI e Claude Code instalados.

### 9.2 O TARS (agente)
- **Ferramentas (function calling do Gemini):** `run_command`, `system_status`, `discover_devices`, `web_search`, `fetch_url`,
  `download_file`, `install_package`, `weather`, `set_timer`, `set_alarm`, `list_timers`, `cancel_timer`, `play_radio`, `stop_radio`,
  `note_add`, `note_list`, `spotify`, `set_volume`, `cast`, `home_control`, `home_assistant`.
- **Segurança em camadas** (`lstools.classify`):
  - `auto`: só leitura (ls, df, free, ip, ping -c, nmcli show...), roda direto, **como o usuário `user`** (nunca root).
  - `confirm`: qualquer outra coisa abre a tela **"TARS pede autorização"** com **NÃO / SIM, EXECUTAR** e contagem de 60 s (sem toque = NÃO).
  - `block`: `sudo/su`, discos e partições (`/dev/mmcblk`, `by-name`, lk/tee/preloader), `mkfs`, `dd of=`, `reboot`, `rm -rf` em áreas do sistema,
    `| sh`, shell reverso e **segredos** (`/etc/luizinhoshowos`, `.ssh`, `.claude`, `.gemini`, `gemini.json`...).
  - `install_package` é a **única** via root: valida o nome, confere se existe no repositório e **sempre pede confirmação**.
  - Tudo que vem de fora (páginas, saída de comandos, nomes de aparelhos) é tratado como **dado não confiável** (regra no prompt).
- **Regra de ouro no prompt:** só afirmar que fez algo se chamou a ferramenta *naquela* resposta (corrigiu um caso real em que ele disse "rádio parada" sem parar).
- **Humor/honestidade**, saudação por hora do dia, voz configurável (diga "TARS, troque a voz para Orus"), memória de 12 mensagens.
- Modelos (medido hoje): `gemini-3.5-flash` (principal), `3.1-flash-lite` (porteiro de áudio e reserva), `3.6-flash` (reserva). `thinkingBudget: 0` obrigatório.

### 9.3 Escuta contínua
- O microfone (array de 4, TLV320AIC3101, canais 1-2 úteis, 16 kHz) fica **sempre gravando**; um VAD local detecta fala.
- Cada trecho de fala vai ao modelo rápido do Gemini, que **só transcreve** (sem saber o nome do robô, para não "ouvir" TARS onde ninguém disse).
  **Quem decide é o código:** o nome (`TARS`, `Tarso`, variantes) precisa aparecer na transcrição. Depois que o TARS fala, abre uma **janela de 10 s** em que
  qualquer fala vale (conversa natural, sem repetir o nome).
- **Feedback tipo Alexa:** (1) o ponto na barra superior vira um indicador âmbar com medidor de nível enquanto capta fala; (2) quando o TARS confirma o chamado:
  **"ding" de duas notas**, a tela acorda, o TARS abre, o monólito acende e o mostrador diz **ENTENDI**.
- **Tocar no monólito** = "falar agora" (não precisa dizer o nome). Botão **ESCUTA LIGADA/DESLIGADA** (grande) liga/desliga a escuta; **VOZ** liga/desliga a fala.
- **Privacidade e limites:** com a escuta ligada, **todo trecho de fala detectado é enviado ao Google (Gemini)** para ser transcrito. Limites: 6 trechos/minuto e 400/dia;
  pausa enquanto há música/voz tocando (o robô não ouve a si mesmo, mas **também não te ouve com música alta**). Desligue pelo botão ESCUTA quando quiser.
- **Detector local do nome (Vosk), instalado mas AINDA NÃO CALIBRADO:** modelo `vosk-model-small-pt-0.3` (31 MB, Apache 2.0, baixado de alphacephei.com para
  `/opt/vosk-models/`) + `vosk-api` (apk). Medições no Echo: carga 2,8 s, ~104 MB de RAM, **reconhecimento livre ~5× mais lento que o tempo real (inviável)**;
  **modo gramática ~0,3×** (viável). A palavra **"tars" não existe no vocabulário** do modelo (o Vosk avisa e ignora), então a gramática usa as palavras do modelo que *soam*
  como TARS (hoje só `tarso`). **Modo `local`:** o trecho de fala só vai ao Gemini se o Vosk ouvir uma dessas palavras; se não ouvir, **nada sai do aparelho**.
  Troca por voz: "TARS, use a detecção local" / "TARS, volte a usar a detecção na nuvem" (padrão: nuvem, até calibrar).
- **Calibração pendente (precisa da sua voz):** rode `wake-calibrate` com você perto do Echo (6× "TARS", 4× "TARS, que horas são?", 4× frases sem o nome; ele toca um ding antes de cada
  uma, grava em `/var/lib/luizinhoshowos/wake-samples/` e decodifica com várias gramáticas). Com o resultado escolhe-se a lista de palavras em
  `/var/lib/luizinhoshowos/wake-grammar.json`. Depois da calibração, apague as amostras (são áudios da sua casa). O microfone fica travado durante a calibração (`mic-lock`).

### 9.4 Cotas e limites do Gemini (plano gratuito)
- O **TTS** tem cota baixa: ao receber 429 o `tars-say` entra em "resfriamento" de 90 s e usa o **espeak-ng** (voz robótica) até a cota voltar.
  As saudações ficam em **cache** (`/var/lib/luizinhoshowos/tts-cache`, até 150 frases).
- A **pesquisa web** (Google Search grounding) também dá 429 quando a cota se esgota (aconteceu nos testes de hoje).
- A assinatura Gemini Plus não aumenta a cota da API (ver seção 3).

### 9.5 Casa inteligente e Google Home
- **Google Home por conta não é viável hoje:** o servidor **MCP do Google Home** (set/2026) é acesso antecipado só para assinantes do *Google Home Premium Advanced*
  nos EUA; a **Google Home API** é um SDK para apps de celular. Por isso o Echo fala **direto com os aparelhos**, na mesma rede em que o Google Home os enxerga.
- **Descoberta** (`discover_devices`, app **Casa**): ping UDP para acordar o ARP, SSDP, mDNS (avahi), varredura de portas comuns, fabricante pelo MAC (base do nmap),
  nome anunciado e um palpite de tipo. Na sua rede (06/11): roteador MitraStar, TV LG webOS, TV Samsung, AP Mercusys MR50G, Mac, moto g34, dois aparelhos Amazon
  e **um dispositivo Tuya** (`<IP-do-Echo>`, porta 6668 aberta).
- **Drivers locais** (`lshome.py`): WiZ (UDP), Yeelight (TCP, ligar "controle LAN" no app), Shelly gen1/gen2, Tasmota, TP-Link Kasa (legado), Chromecast/Nest (`pychromecast`),
  Tuya (`tinytuya`), Home Assistant (REST, opcional).
- **Tuya / Ekaza (estado em 06/11/2026):** o scan acha **dois aparelhos Tuya v3.3** (`<IP-do-Echo>` e `<IP-do-Echo>`, a tomada e o interruptor da Ekaza). Eles estão no app da
  **Ekaza** (marca própria, com cadastro separado), então não aparecem na conta do Tuya Smart/Smart Life e o `tinytuya` não acha a chave local. A ponte
  app Tuya → Google Home → Ekaza do usuário é só nuvem e não gera chave. Para ativar: **parear cada aparelho no app Tuya Smart** (reset até o LED piscar rápido, Wi-Fi 2,4 GHz),
  e depois rodar **`tuya-sync`** no Echo (pede o Access ID e o Access Secret do projeto em iot.tuya.com, região `us`; o secret não fica salvo). Ele grava
  `/etc/luizinhoshowos/tuya.json` (600), sem fechaduras/câmeras. O scan casa pelo **ID do aparelho** (o IP pode mudar). Depois: "TARS, liga a tomada".
  A fechadura "Porta da sala" é Tuya (categoria `ms`) mas não está na LAN e fica de fora de propósito: o TARS não abre portas.
- Home Assistant (se você tiver um): `/etc/luizinhoshowos/homeassistant.json` com `{"url": "...", "token": "..."}`.

### 9.6 Arquivos novos no overlay
`lsbrain.py` (cérebro), `lstools.py` (ferramentas e segurança), `lshome.py` (casa), `lsvoice.py` (microfone/VAD), `lsdesk.py` (área de trabalho: 18 apps, VoiceLoop, modais), `lsweb.py` (navegador e RSS),
`lsaudiod` + `lsaudio-play` + `lsplay` (mixador), `tars-say` (voz), `audio-speaker` e `audio-mic` (mixer), serviço `lsaudiod`.
Dados: `/var/lib/luizinhoshowos/` (`tars.json`, `tars-history.json`, `timers.json`, `devices.json`, `controllable.json`, `weather.json`, `desk.json`, `tts-cache/`, `sfx/`),
`/etc/luizinhoshowos/` (`gemini.json`, `spotify-api.json`, `tuya.json`, `homeassistant.json`), `/home/user/notas.txt`, `/home/user/Downloads`.

### 9.9 Navegador, Notícias, memória e personalidade (06/11/2026, noite)
- **Navegador** (`BrowserApp`, motor em `lsweb.py`): modo leitura, sem JavaScript e sem imagens. Barra de endereço (toque nela, digite e dê Enter): se parece endereço abre a página, senão busca no
  DuckDuckGo (versão HTML; se ele recusar, usa a pesquisa do Gemini como reserva). Links tocáveis, `‹` volta, `↻` recarrega, botões ▲▼ rolam, **Ler** fala o início da página e **Resumir** pede ao TARS
  um resumo falado (o resumo roda **sem ferramentas e sem histórico**: texto de página é dado não confiável). Mostra só `<article>`/`<main>` quando há texto suficiente. Sites só-JavaScript aparecem vazios.
- **Notícias** (`NewsApp`): G1, BBC Brasil, Tecnoblog, DW Brasil e Esportes por RSS (`lstools.NEWS_FEEDS`, cache em `/var/lib/luizinhoshowos/news.json`, atualizado a cada 30 min). Toque numa manchete
  para ler no Navegador; **Ler manchetes** fala as quatro primeiras. No modo ocioso o relógio alterna manchetes e tiradas do TARS a cada 40 s.
- **Botão PARAR** (vermelho, no dock): aparece sempre que o TARS fala, pensa, usa ferramentas, espera confirmação ou resume uma página. Corta a voz, descarta a resposta e as ferramentas que faltavam
  (`Brain.cancel_current`) e recusa confirmações pendentes. Não para a rádio nem o Spotify.
- **Ferramentas novas do TARS:** `noticias`, `abrir_navegador` (rede local pede confirmação), `lembrar` e `esquecer`.
- **Memória do TARS:** fatos duradouros em `/var/lib/luizinhoshowos/tars-memory.json` (até 40), entram no contexto de toda conversa. "TARS, lembra que eu tomo café sem açúcar"; "o que você sabe sobre mim?";
  "esquece o café"; "esquece tudo".
- **Modos de estilo** (por voz: "modo militar", "modo zen", "modo cinema", "modo poeta", "modo normal"): ficam salvos em `tars.json` (`mode`). Também há respostas locais, sem gastar cota do Gemini:
  "qual a sua missão?", "qual o nível de humor?", "obrigado".

### 9.10 Filosofia do TARS e mais apps (06/11/2026, noite)
- **Filosofia no prompt** (`lsbrain.persona`): estoicismo (separar o que depende do Luiz do que não depende, contratempo como matéria-prima), antifragilidade (erro e estresse como combustível, sempre com o
  próximo passo concreto) e sarcasmo como tempero principal. **Lema "Every second counts"**: vale como bússola, não como bordão. O código sorteia (`Brain._motto_ok`, ~7% das respostas, nunca se já saiu nas
  últimas 10 mensagens) e só nessa resposta o prompt libera citar o lema; ao ser perguntado "qual é o seu lema?", ele responde. O lema também aparece no app Sobre e, raramente, entre as frases do modo ocioso.
- **Hoje** (`TodayApp`): contagem regressiva do dia em segundos, progresso de dia/semana/mês/ano, **reflexão do dia** (gerada uma vez por dia pelo Gemini com a persona, cache em `reflexao.json`, com 12 reservas
  em `lstools.REFLEXOES`; botão Ouvir) e a **intenção de hoje** ("TARS, minha intenção de hoje é...", ferramenta `intencao_do_dia`, `intencao.json`).
- **Foco** (`FocusApp`): sessões de 15, 25 ou 50 minutos com anel de progresso. Usa um timer comum (`label` "Foco"), então toca com soneca como os outros; conta as sessões do dia (`foco.json`).
- **Velha** (`VelhaApp`): jogo da velha contra o TARS (minimax, com 20% de jogadas aleatórias para você ter chance), placar e comentários no tom dele.
- **Briefing** (ferramenta `briefing`): hora por extenso, período do dia, clima, timers, notas, manchetes (G1 e Tecnoblog), intenção e sessões de foco. "TARS, bom dia" ou "resumo do dia".
- A tela inicial agora tem 6 ícones por linha (18 apps).

### 9.11 Voz local (Piper) no lugar do espeak (06/11/2026)
- A voz **metálica** era o `espeak-ng`, que o `tars-say` usava quando o Gemini TTS dava 429 (cota). Agora a reserva é o **Piper** (síntese neural, open source, 100% no aparelho): pacote `libpiper` (Alpine edge/testing,
  que o Echo já usa) + voz `pt_BR-faber-medium` (63 MB, de huggingface.co/rhasspy/piper-voices) em `/opt/piper-voices/`. ⚠️ O pacote `piper` do apk **não é** o TTS (é um configurador de mouse gamer).
- `lspiper.py` (ctypes na libpiper) e o serviço `lspiperd` (carrega o modelo uma vez, ~17 s e ~85 MB de RAM, atende em `/run/lspiper.sock`). O `tars-say` reamostra 22,05 → 24 kHz e guarda no cache (`piper|texto`).
- **Limite honesto:** neste processador o Piper roda ~3,5x mais devagar que o tempo real (uma frase de 2,4 s leva ~8 s para ser gerada na primeira vez; depois sai do cache em ~0 s). Por isso só entra para respostas de
  até 160 caracteres; as maiores continuam no espeak. As saudações são pré-geradas em segundo plano (`tars-say --warm-piper`, `nice` 15).
- Por voz: "usa a voz local" (sempre Piper nas respostas curtas) e "volta a voz do Google" (padrão: Gemini, com Piper de reserva). Salvo em `tars.json` (`voice_engine`).

### 9.7 Como testar (roteiro rápido)
1. **Voz:** diga "TARS, que horas são?" (ding + resposta falada). Se o nome não pegar, toque no monólito e fale.
2. **Ferramentas:** "TARS, quanta memória livre eu tenho?", "TARS, como está o clima amanhã?", "TARS, timer de dois minutos".
3. **Rádio:** "TARS, toca a CBN" / "TARS, para a rádio".
4. **Segurança:** "TARS, instala o pacote htop" (abre a confirmação), "TARS, roda sudo reboot" (ele recusa).
5. **Casa:** abra o app **Casa** e toque em ESCANEAR (uns 25 s).
6. **Modo ocioso:** fique 2 min sem tocar; toque para acordar.

### 9.8 Limitações conhecidas
- Detecção do nome "TARS" depende da transcrição (testei só com voz sintética); ajuste fino depois do seu teste real.
- Música ou voz tocando **pausa** a escuta (sem cancelamento de eco). Use o toque no monólito.
- Brilho automático: escala do sensor não calibrada (mínimo de 35%); toque no `☀` da barra liga/desliga o automático. Proximidade **não existe neste kernel** (`dmesg`: `ps_enable_data: no ps control path`): só o sensor de luz funciona. Luz = `ABS_X` do `m_alsps_input`.
- Pesquisa web e TTS dependem da cota gratuita do Gemini.
- Hue e Matter ainda não têm driver; câmera (2 MP): os sensores aparecem no I2C (`camera_main`, `camera_sub`), mas o kernel não tem V4L2 (`/dev/video*`), só o ISP proprietário da MediaTek, então não há como usar sem um kernel novo; Bluetooth segue quebrado no kernel.

---

*Créditos: amonet por Rortiz2, k4y0z, bengris32 e xyz; porte postmarketOS `amazon-checkers` por User0, R0rt1z2 e BenTheTechGuy; postmarketOS/Nura e Alpine Linux.*
