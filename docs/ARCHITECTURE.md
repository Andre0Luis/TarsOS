# Arquitetura do TarsOS

> **In English:** TarsOS is a thin layer on top of postmarketOS. A single Python process (`lsdesk`) is the "graphics server" (Pillow →
> `/dev/fb0`, `evdev` touch), hosts the apps and the always-on voice loop; `lsaudiod` is the only owner of the speaker; `lsbrain` + `lstools`
> are the Gemini-backed agent with a three-tier safety gate; `lspiperd` serves local neural TTS. Everything is stdlib-first Python and shell.

## Camadas

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Apps (em lsdesk.py)  TARS · Música · Relógio · Rádio · Clima · Casa ·   │
│  Notas · Navegador · Notícias · Hoje · Foco · Velha · Terminal ·         │
│  Ao Vivo · Monitor · Som · Ajustes · Sobre                               │
├──────────────────────────────────────────────────────────────────────────┤
│  lsbrain (persona + Gemini)   lstools (ferramentas + segurança)          │
│  lshome (casa)  lsweb (navegador/RSS)  lsvoice (mic+VAD)  lswake (Vosk)  │
├──────────────────────────────────────────────────────────────────────────┤
│  lsaudiod (mixador)   tars-say (voz)   lspiperd (Piper)   lsjack (P2)    │
│  shairport-sync (AirPlay)   spotify-api (Web API)                        │
├──────────────────────────────────────────────────────────────────────────┤
│  OpenRC · Alpine · postmarketOS · kernel 4.9.77 (downstream Amazon/MTK)  │
├──────────────────────────────────────────────────────────────────────────┤
│  amonet microloader · LK (Amazon) · BootROM MT8163                       │
└──────────────────────────────────────────────────────────────────────────┘
```

## Processos e serviços (OpenRC, runlevel `default`)

| Serviço | Arquivo | Faz |
|---|---|---|
| `lsaudiod` | `usr/local/bin/lsaudiod` | **único dono do alto-falante**: socket `/run/lsaudio.sock`, mistura PCM S16LE 48 kHz estéreo em blocos de 9600 bytes e escreve em múltiplos de 64 bytes |
| `lsdesk` | `lsdesk.py` | área de trabalho: desenha, lê toque/botões, hospeda apps, `VoiceLoop`, modais, transmissão `:8080` |
| `lsjack` | `usr/local/bin/lsjack` | lê `/sys/class/switch/h2w/state` e chama `audio-speaker apply` (alto-falante ↔ fone) |
| `lspiperd` | `lspiperd.py` | mantém o modelo do Piper na memória; socket `/run/lspiper.sock` |
| `airplay`, `airplay-meta` | `usr/local/bin/airplay-*` | `shairport-sync` → `sox` → `lsaudiod`; metadados para o player da tela |
| `spotify-api` | `usr/local/bin/spotify-api` | consulta e controla o Spotify pela Web API, escreve `/run/luizinhoshowos/spotify.json` |

Tarefas de boot em `etc/local.d/`: configuração do áudio, hora (NTP + RTC), gadget USB serial e a tela de boas-vindas.

## A área de trabalho (`lsdesk.py`)

- **Saída:** o painel é 480×960 retrato (RGBA, 32 bpp, 3 páginas); a imagem paisagem 960×480 é girada **270°** e gravada em `/dev/fb0`. Não há `fbcon`, DRM nem X.
- **Entrada:** toque Goodix (`evdev`, multitoque, só **toques**; não há gestos de arrastar, por isso as listas rolam por botões ▲▼) e botões de volume. Os eventos de toque chegam em coordenadas do painel e são convertidos.
- **Janelas:** cada app é uma subclasse de `App` (`draw`, `on_tap`, `on_text`, `tick`). Há um app em foco, ou dois lado a lado (◫). Um app com `animated = True` faz a tela redesenhar ~8×/s só enquanto está visível.
- **Teclado na tela:** camadas `abc`/`Sym`/acentos, Ctrl/Shift/Esc/Tab/setas; apps com `wants_keyboard` abrem o teclado.
- **Modo ocioso:** depois de 120 s sem toque (e sem o TARS ocupado) a tela escurece e mostra o relógio, manchetes e frases do TARS; o primeiro toque só acorda.
- **Brilho:** o sensor JSA1214 entrega lux por `evdev` (`m_alsps_input`); a curva é logarítmica, com piso de 35%. **O sensor de proximidade do mesmo chip não funciona neste kernel.**
- **Controle externo:** o FIFO `/run/lsdesk.ctl` aceita `tap X Y`, `text ...`, `open <app>`, `home`, `idle`, `say ...`. Junto com `http://<ip>:8080/shot` (um JPEG da tela), é a forma de **testar a interface sem tocar no aparelho**.

## O pipeline de voz

```
microfone (S24_3LE, 4 canais, 16 kHz; canais 1–2 úteis)
   → lsvoice.Listener: VAD por energia, grava a fala em WAV 16 kHz mono
   → VoiceLoop (lsdesk):
        • modo "local": lswake.detect_grammar (Vosk) → só continua se ouvir "TARS"
        • modo "nuvem": transcrição NEUTRA no Gemini; quem decide se falaram com o TARS é o
          código (regex do nome), para o modelo não "ouvir" o nome onde ninguém o disse
        • janela de conversa de 10 s depois de uma resposta (não precisa repetir "TARS")
        • limites: 6 chamadas por minuto e 400 por dia (VoiceLoop)
   → lsbrain.ask_audio → Gemini com function calling (até 7 rodadas de ferramentas)
   → tars-say: Gemini TTS (cache em disco, esfria 90 s depois de um 429)
        → Piper local (respostas ≤ 160 caracteres) → espeak-ng
   → lsaudiod → alto-falante
```

Enquanto algo toca ou o TARS fala, a escuta pausa (não há cancelamento de eco). `/run/luizinhoshowos/audio-active`, `tars-speaking` e `mic-lock` coordenam isso.

## Dados e configuração no Echo

| Caminho | Conteúdo |
|---|---|
| `/etc/luizinhoshowos/` (modo 700) | `gemini.json`, `spotify-api.json`, `tuya.json`, `homeassistant.json`: **segredos, só do root** |
| `/var/lib/luizinhoshowos/` | `tars.json` (humor, honestidade, voz, modo, dono, motor de voz), `tars-history.json`, `tars-memory.json`, `timers.json`, `devices.json`, `news.json`, `weather.json`, `desk.json`, `tts-cache/` |
| `/opt/vosk-models/`, `/opt/piper-voices/` | modelos baixados pelo `provision.sh` |
| `/run/luizinhoshowos/` | estado volátil (tmpfs): quem fala, mic, Spotify, AirPlay |
| `/etc/luizinhoshowos-release` | nome e versão (lido pelo app **Sobre**) |

> Os nomes `luizinhoshowos` são históricos (o projeto foi renomeado para TarsOS). Mudá-los exige migrar dados e chaves; está no [roadmap](../README.md#roadmap) como baixa prioridade.

## Por que Python e Pillow, e não GTK/Qt?

O kernel não tem DRM nem `fbcon`, a GPU 3D não funciona e o aparelho tem 1 GB de RAM. Um compositor próprio em Pillow dá controle total, ~17% de um núcleo com um app animado e **zero** dependências gráficas. O custo é o desenho manual; a vantagem é caber no aparelho e ser fácil de ler.

## Decisões que valem lembrar

- **OpenRC, não systemd:** o kernel 4.9.77 não suporta o systemd moderno.
- **`thinkingBudget: 0` no Gemini:** com raciocínio ligado o modelo gasta os tokens pensando e a frase sai cortada.
- **Spotify por Web API + AirPlay:** o Spotify Connect (`librespot`) é recusado pela conta; a Web API mostra e controla, e o áudio chega por AirPlay.
- **Resumos sem ferramentas:** o *Resumir* do navegador usa uma geração sem *function calling* e sem histórico: texto de página é dado não confiável.
- **Cache e reserva de voz:** frases repetidas saem do cache; a cota gratuita do TTS acaba fácil, então a reserva local (Piper) existe para não cair no espeak metálico.
