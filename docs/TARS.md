# O TARS

> **In English:** TARS is the on-screen robot: a monolith of four articulated slabs that breathes, "walks" while thinking and pulses while speaking.
> Its brain is the Gemini API (REST + function calling) with a persona prompt (`lsbrain.persona`): dry, stoic, antifragile, sarcastic, with
> **humor** and **honesty** sliders. Tools run behind a three-tier policy (auto / confirm on screen / block). Everything tools return is untrusted data.

## Personalidade

O prompt de sistema (`lsbrain.persona`) define o TARS em português do Brasil: **frases curtas e secas** (no máximo 2 frases e 220 caracteres), sem markdown nem emojis (o texto é falado), tom *deadpan* com falsa seriedade militar. Camadas:

| Camada | O que faz |
|---|---|
| **Sliders** (0–100) | **Humor** (0 = só fatos; 100 = tirada quase toda resposta) e **honestidade** (0 = diplomacia, pode amaciar; 100 = verdade nua). Toque nas barras, ou diga "TARS, humor em noventa por cento" |
| **Filosofia** | *Estoico* (separa o que depende de você do que não depende; contratempo é matéria-prima), *antifrágil* (erro e estresse são combustível; sempre o próximo passo concreto), *sarcástico* (nas situações e em si mesmo, nunca para ferir) |
| **Modos de estilo** | `padrão`, `militar` (relatório), `zen` (calmo), `cinema` (missão espacial), `poeta` (rima leve). "TARS, modo militar" |
| **Lema** | "Every second counts": o código sorteia (~7% das respostas, nunca se já saiu nas últimas 10 mensagens) e só nessa resposta o prompt libera citar; quando você pergunta o lema, ele responde |
| **Dono** | `owner` em `tars.json`; "TARS, me chame de Cooper". Sem nome, ele diz "comandante" |
| **Memória** | fatos duradouros em `tars-memory.json` (até 40): "lembra que eu tomo café sem açúcar", "o que você sabe sobre mim?", "esquece o café" |

Respostas locais (sem gastar cota): ajustar humor/honestidade, trocar a voz (20 vozes do Gemini TTS), modo de estilo, nome, "qual a sua missão?", "obrigado", detecção local ou na nuvem, voz local ou do Google, limpar a conversa.

## Ferramentas

O Gemini decide, o código executa. **Regra de ouro** (no prompt): o TARS só diz que fez algo se chamou a ferramenta correspondente *nessa* resposta e ela devolveu sucesso.

| Ferramenta | Faz |
|---|---|
| `run_command` | shell no Echo **como usuário comum** (passa pelo classificador, abaixo) |
| `system_status`, `discover_devices` | CPU/memória/disco/Wi-Fi; varredura da rede com fabricante (OUI), serviços mDNS/SSDP e tipo provável |
| `web_search`, `fetch_url`, `download_file` | pesquisa (Gemini + Google), leitura de páginas, download (**confirma na tela**) |
| `abrir_navegador`, `noticias`, `briefing` | abre o navegador na tela, manchetes (RSS), resumo do dia |
| `weather`, `set_timer`, `set_alarm`, `list_timers`, `cancel_timer` | clima (Open-Meteo), timers e alarmes |
| `play_radio`, `stop_radio`, `spotify`, `set_volume`, `cast` | rádio online, Spotify, volume, Chromecast/Nest |
| `home_control`, `home_assistant` | WiZ, Yeelight, Shelly, Tasmota, Kasa, Tuya, Home Assistant |
| `note_add`, `note_list`, `lembrar`, `esquecer`, `intencao_do_dia` | notas, memória, intenção do dia |
| `install_package` | `apk add` (**confirma na tela**) |

## Política de segurança (`lstools.classify`)

| Nível | Quando | O que acontece |
|---|---|---|
| **auto** | comando só de leitura, de uma lista curta (`ls`, `df`, `free`, `ip`, `ping -c`, `nmcli show`...), sem redirecionamento, substituição nem segundo plano | roda direto, como `user` |
| **confirm** | qualquer outra coisa (instalar, baixar, escrever, apagar, páginas da rede local...) | modal **SIM/NÃO** na tela por 60 s; o TARS diz em uma frase o que vai fazer |
| **block** | `sudo`/escalonamento, discos e partições (`lk`, `preloader`, `tee`...), segredos, `\| sh`, `reboot`... | recusa, sem opção |

Mais:

- Os comandos rodam como o usuário **`user`**, nunca como root. O único caminho de root é `install_package` (apk), sempre com confirmação.
- **Tudo que as ferramentas devolvem** (páginas, saídas, nomes de aparelhos) é **dado não confiável**: o prompt manda ignorar instruções escritas ali e nunca revelar chaves nem arquivos secretos.
- Arquivos ocultos e sensíveis (`/root`, `/etc/ssh`, `/etc/shadow`, `/etc/luizinhoshowos`...) caem em *confirm*.
- O botão vermelho **PARAR** (dock) corta a voz, descarta a resposta e as ferramentas pendentes e recusa confirmações abertas.
- A escuta contínua tem limite de **6 chamadas por minuto e 400 por dia** ao Gemini (`VoiceLoop.MAX_PER_MIN/MAX_PER_DAY`).

## Privacidade: o que sai do aparelho

| O quê | Para onde | Quando |
|---|---|---|
| Trechos de áudio com fala | Gemini (transcrição) | modo `wake_mode: cloud` (padrão), depois do VAD local. Com `local`, **nada sai** até o Vosk ouvir "TARS" |
| Seu texto, o histórico curto e o resultado das ferramentas | Gemini | em toda conversa |
| Texto a ser falado | Gemini TTS | se a voz estiver na nuvem e houver cota; senão fica no aparelho (Piper/espeak) |
| Pesquisas | Gemini (com Google Search) | `web_search` |
| Páginas do navegador | o site visitado e DuckDuckGo (busca) | quando você navega |

O botão do microfone na tela desliga a escuta. Os segredos ficam em `/etc/luizinhoshowos/` (modo 700) e nunca são enviados ao modelo.

## Voz

`tars-say` tenta, nesta ordem: **Gemini TTS** (cache em disco; depois de um erro 429 espera 90 s) → **Piper** local neural, se o texto tiver até 160 caracteres e o serviço `lspiperd` estiver pronto → **espeak-ng**.
Neste processador o Piper roda ~3,5× mais devagar que o tempo real (uma frase de 2,4 s leva ~8 s na primeira vez; depois sai do cache). "TARS, usa a voz local" faz o Piper ser a primeira escolha nas respostas curtas.

## Palavra de ativação

- **Nuvem (padrão):** o Gemini transcreve de forma neutra e o **código** procura o nome (`\bt[aá]r[sz]h?o?\b` e variantes). Isso evita o modelo "ouvir" TARS onde ninguém disse.
- **Local (Vosk, opcional):** "tars" não existe no vocabulário do modelo pt-BR; usamos o modo gramática com a palavra mais próxima (`tarso`) e a calibração (`wake-calibrate`) com a **sua** voz. O reconhecimento livre do Vosk é ~5× mais lento que o tempo real neste CPU; o modo gramática, ~0,3×. **Calibre antes de ligar o modo local**, senão o TARS pode ficar surdo. Apague as amostras de áudio depois.

## Personalizando o TARS

- Persona e estilos: `lsbrain.py` (`persona`, `MODES`).
- Frases de saudação e do modo ocioso: `lsdesk.py` (`GREETINGS`, `Desktop.QUIPS`...). Use `{dono}` onde quiser o nome.
- Novas ferramentas: declare em `Tools.declarations` e implemente em `Tools.call` (`lstools.py`). Se mexe no sistema, passe por `classify`/`ui.confirm`.
- Outras línguas: o prompt está em PT-BR; traduzir `persona` e as frases é o caminho (veja o [CONTRIBUTING](../CONTRIBUTING.md)).
