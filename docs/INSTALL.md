# Instalação do TarsOS

> **In English:** this is the full install path for the 1st-gen Echo Show 5 (`checkers`): unlock with amonet → build a postmarketOS image
> with `pmbootstrap` → shorten the kernel cmdline (the LK prepends ~840 chars, the kernel takes 1024) → flash `boot` + `userdata` with the amonet
> hacked fastboot → first boot over USB serial → `tools/provision.sh` + `tools/deploy.sh --all`. It has been done end to end exactly once (by the author), so expect
> rough edges, and please send corrections.

⚠️ **Antes de qualquer coisa**

- Isto **só vale para o Echo Show 5 de 1ª geração (2019, `checkers`)**. Em outro aparelho, você pode brickar.
- **Nunca grave `lk`, `preloader`, `tee1` ou `tee2`.** Não existe volta.
- Faça **backup** de `boot` e `recovery` antes de gravar qualquer coisa (passo 0).
- O que está descrito aqui foi feito **uma vez**, pelo autor, no próprio aparelho. Pode haver diferenças de firmware, de rede e de ferramentas no seu caso.
- Você precisa de ~15 GB livres no computador para o build.

Tempo total: umas 3 a 5 horas na primeira vez (a gravação do `userdata` sozinha levou ~10 minutos).

---

## 0. Pré-requisito: bootloader desbloqueado (amonet)

O Echo Show 5 de 1ª geração só aceita código próprio depois do exploit **amonet** (BootROM + LK da MediaTek).

- Código: <https://github.com/bengris32/amonet>
- Guia passo a passo (XDA): *"[UNLOCK][ROOT][TWRP][UNBRICK] Amazon Echo Show 5 1st Gen (2019) checkers"*. Use **o pacote do `checkers`**: os arquivos de outros modelos têm nomes parecidos e causam boot loop.
- Resultado esperado: o aparelho abre o **TWRP** com Volume Up, e o **fastboot liberado** com o `handshake2.py FACTFACT` do amonet (aparelho em USBDL: Mute + Volume Down ao ligar). Teste: `fastboot getvar unlock_status` → `true`.

> **Dois fastboots.** O que se alcança com os três botões ou `adb reboot bootloader` é o **original da Amazon, travado**, e não serve. O **liberado** vem do amonet.

**Backup (não pule):** pelo TWRP, copie as partições `boot` e `recovery` (e confira o SHA-256 dos dois lados):

```bash
adb root
adb shell 'dd if=/dev/block/by-name/boot of=/tmp/boot.img'   # ajuste o caminho conforme o seu TWRP
adb pull /tmp/boot.img backup/boot.img
shasum -a 256 backup/boot.img
```

## 1. Gerar a imagem do postmarketOS

O porte `amazon-checkers` já existe no postmarketOS (categoria *downstream*). Configuração que usamos:

| Opção | Valor | Por quê |
|---|---|---|
| Canal | `edge` | é onde o `device-amazon-checkers` está |
| Dispositivo | `amazon-checkers` | |
| **Init** | **OpenRC** | o kernel 4.9.77 é velho demais para o systemd (pede ≥ 5.4) |
| UI | `fbkeyboard` | as UIs gráficas ficam escondidas (sem `deviceinfo_drm`); a nossa área de trabalho substitui |
| Extras | `openssh nano htop curl iw` | |
| Fuso / idioma | o seu | |

### No Linux

```bash
pip install --user pmbootstrap       # ou: git clone https://gitlab.postmarketos.org/postmarketOS/pmbootstrap.git
pmbootstrap init                      # responda como na tabela acima
pmbootstrap install --password <uma-senha-provisoria> --zap
pmbootstrap export                    # copia boot.img e a imagem do sistema para /tmp/postmarketOS-export
```

### No macOS (Docker)

O pmbootstrap não roda direto no macOS. Use o `build/Dockerfile` deste repositório:

```bash
docker build -t pmos-env build/
docker run -it --privileged --name pmos pmos-env
# dentro do contêiner:
pmbootstrap init
pmbootstrap install --password <uma-senha-provisoria> --zap
```

⚠️ **No Docker o `install` falha com `File did not appear: /dev/loop0p2`**: o contêiner cria o *loop device*, mas não os nós das partições. Em outro terminal, enquanto o `install` roda, crie os nós à mão (talvez seja preciso repetir algumas vezes):

```bash
docker exec -u root pmos bash -c '
  for p in /sys/block/loop*/loop*p*; do
    n=$(basename $p); IFS=: read ma mi < $p/dev
    for d in /dev /home/pmos/.local/var/pmbootstrap/chroot_native/dev; do
      [ -e $d/$n ] || mknod $d/$n b $ma $mi
    done
  done'
```

Se preferir, rode o pmbootstrap numa VM Linux de verdade (UTM, Lima, Parallels): os passos do "No Linux" funcionam sem gambiarra.

Ao final você terá **`boot.img`** (~14 MB) e a **imagem do sistema** (~1,1 GB).

## 2. Encurtar o cmdline do boot.img (obrigatório)

O LK da Amazon injeta ~840 caracteres no cmdline do kernel, e o kernel ARM aceita **1024**. O cmdline padrão do postmarketOS (~215) **estoura** o limite, e o aparelho fica **parado no logo "amazon"** (o watchdog reinicia em ~60 s) sem nunca iniciar o kernel. Mantenha o seu em **~180 caracteres ou menos**.

```bash
python3 tools/bootimg_cmdline.py show  boot.img        # veja o cmdline atual e o seu UUID
python3 tools/bootimg_cmdline.py set   boot.img \
  "bootopt=64S3,32N2,32N2 pmos_boot_uuid=<UUID-do-boot> pmos_root_uuid=<UUID-da-raiz> pmos_rootfsopts=defaults"
```

Pegue os dois UUIDs do cmdline original (`pmos_boot_uuid` e `pmos_root_uuid`). O script avisa se o total (com os ~840 do LK) passa de 1004. Na imagem **já injetada pelo amonet** ele só altera o cabeçalho real (offset 1024) e preserva o *stub* do microloader e o kernel byte a byte.

## 3. Gravar

Coloque o aparelho em **USBDL** (Mute + Volume Down ao ligar) e abra o **fastboot liberado** do amonet (`handshake2.py FACTFACT`). Depois:

```bash
fastboot getvar unlock_status          # tem que dizer: true
fastboot flash boot     boot.img       # o fastboot do amonet injeta o microloader sozinho
fastboot flash userdata amazon-checkers.img   # ~10 min
```

> **Layout do amonet na partição `boot`:** o *stub* do microloader fica no offset 0, **uma cópia do cabeçalho do pmOS no offset 1024**, e o kernel/ramdisk ficam intocados a partir do offset 2048. Se um dia gravar pelo TWRP com `dd`, preserve esse layout (edite dentro da imagem já injetada, como o `bootimg_cmdline.py` faz).
> Este LK **não tem `fastboot boot`**: não dá para testar um kernel só na RAM, tem que gravar.
> O amonet escolhe o modo de boot pelos 16 primeiros bytes da partição `MISC`: `boot-recovery` → TWRP, `FASTBOOT_PLEASE` → fastboot liberado. Dentro do TarsOS, `to-recovery` e `to-fastboot` fazem isso.

Reinicie. Em ~30 s o Linux sobe (a tela mostra a boas-vindas do postmarketOS ou fica preta até o `lsdesk`, dependendo da etapa).

## 4. Primeiro acesso: serial USB, Wi-Fi e SSH

O postmarketOS expõe o USB como RNDIS, e o **macOS não suporta RNDIS**. Por isso o TarsOS troca o gadget USB por uma **serial CDC-ACM** (`etc/local.d/luizinhoshowos-usb.start`) com um shell root no `ttyGS0`. Na primeira vez, antes do overlay, o acesso depende do que a imagem do pmOS oferecer (RNDIS no Linux, ou o gadget padrão).

Com o overlay instalado, o Echo aparece no Mac como `/dev/cu.usbmodem*`. Use o cliente do projeto:

```bash
pip install pyserial
python3 tools/echosh.py 'showos'                                  # roda um comando no Echo pela serial
python3 tools/echosh.py --push minha_chave.pub /root/.ssh/authorized_keys
```

Conecte o Wi-Fi (no Echo, pela serial):

```bash
nmcli device wifi connect "NOME-DA-REDE" password "SENHA"
ip addr show wlan0
```

Crie uma chave SSH dedicada (as ferramentas procuram em `keys/`, que está no `.gitignore`):

```bash
mkdir -p keys && ssh-keygen -t ed25519 -f keys/tarsos_ed25519 -N ""
python3 tools/echosh.py --push keys/tarsos_ed25519.pub /root/.ssh/authorized_keys
export TARSOS_HOST=<IP-do-Echo>      # o mDNS (tarsos.local) funciona só em algumas redes
ssh -i keys/tarsos_ed25519 root@$TARSOS_HOST showos
```

> O `root` não tem senha de login: entra só por chave SSH ou pela serial. **Troque a senha do usuário `user`** (`passwd user`).

## 5. Provisionar e instalar o TarsOS

```bash
# 1) pacotes (apk) e modelos de voz: cada download é anunciado e pede confirmação
ssh -i keys/tarsos_ed25519 root@$TARSOS_HOST 'sh -s' < tools/provision.sh

# 2) envia o código e ativa os serviços (lsaudiod, lsdesk, lsjack, lspiperd, airplay, ...)
tools/deploy.sh --all

# 3) a chave do Gemini (modo 600, só do root; a chave nunca vai para o chat nem para o git)
tools/set_gemini_key.sh
```

O `provision.sh` instala via `apk` (Python, Pillow, numpy, evdev, pyte, sox, ffmpeg, espeak-ng, shairport-sync, vosk-api, pychromecast...), o `tinytuya` via `pip` e, **se você aceitar**:

| Download | Tamanho | Para quê |
|---|---|---|
| `vosk-model-small-pt-0.3` ([alphacephei.com](https://alphacephei.com/vosk/models)) | ~31 MB | detector local da palavra "TARS" |
| `pt_BR-faber-medium.onnx` ([huggingface.co/rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices)) | ~63 MB | voz local neural (reserva quando a cota do Gemini acaba) |

A voz local usa a biblioteca `libpiper` (repositório *edge/testing* do Alpine; confira que está em `/etc/apk/repositories`). Ela roda ~3,5× mais devagar que o tempo real nesse processador, então só entra para respostas curtas (veja [TARS.md](TARS.md)).

> ⚠️ Nos repositórios do Alpine existe um pacote chamado **`piper`** que **não é** a síntese de voz (é um configurador de mouse gamer). O TTS é o `libpiper`.

**Opção B, sem git:** baixe o `tarsos-<versão>.tar.gz` em [Releases](https://github.com/Andre0Luis/TarsOS/releases) (a `edge` é o último commit; as `vX.Y.Z` são as estáveis), confira com `sha256sum -c SHA256SUMS`, descompacte e rode `tools/deploy.sh --all` de dentro da pasta. Ou, só com o sistema de arquivos do Echo: `ssh root@IP 'tar -C / -xzf -' < tarsos-overlay-<versão>.tar.gz` (depois ative os serviços com `rc-update add <serviço> default`; o `deploy.sh` faz isso por você).

Reinicie o Echo (ou `rc-service lsdesk restart`). A tela inicial do TarsOS aparece; abra o app **TARS** e fale.

## 6. Opcionais

**Spotify (player e controles na tela; o áudio vem por AirPlay):**

```bash
# crie um app em developer.spotify.com/dashboard (Redirect URI: http://127.0.0.1:8899/callback) e copie o Client ID
python3 tools/spotify_auth.py <CLIENT_ID>        # fluxo PKCE: não precisa de Client Secret
```

**Tomadas e lâmpadas Tuya / Smart Life** (precisam da chave local de cada aparelho):

1. Pareie o aparelho no app **Tuya Smart** ou **Smart Life** (apps de marca própria, como alguns da "Ekaza", têm cadastro separado e **não** aparecem na conta do Tuya).
2. Crie um projeto em [iot.tuya.com](https://iot.tuya.com) (Industry: Smart Home) e ligue a conta do app (*Devices → Link App Account*).
3. No Echo: `tuya-sync` (pede o Access ID e o Access Secret; o secret **não** é guardado). Grava `/etc/luizinhoshowos/tuya.json` (modo 600).

**Palavra de ativação local (Vosk):** rode `wake-calibrate` no Echo, perto dele, e depois diga "TARS, usa a detecção local". Sem calibrar, deixe o padrão (nuvem).

**Home Assistant:** `/etc/luizinhoshowos/homeassistant.json` com `{"url": "...", "token": "..."}`.

## 7. Atualizar e solucionar problemas

- **Atualizar o código:** `git pull && tools/deploy.sh` (envia só `usr/local` e `etc/init.d`; `--all` envia tudo).
- **Ver a tela do Echo pelo navegador:** `http://<IP-do-Echo>:8080` (transmissão ao vivo).
- **Logs:** `/var/log/lsdesk.log`, `lsaudiod.log`, `lspiperd.log`.
- **Parou no logo "amazon":** o cmdline está longo demais (passo 2). Entre no TWRP (Volume Up) e corrija o cabeçalho.
- **`apk upgrade` do kernel:** o `boot-deploy` deste aparelho **não** regrava a partição `boot`, então o upgrade só troca arquivos em `/boot` e o Echo continua iniciando. O risco só existe se você gravar esse `boot.img` à mão: aí o cmdline longo trava no logo.
- **Sem som:** rode `audio-speaker 22` (o amplificador do alto-falante tem o controle invertido; veja [HARDWARE.md](HARDWARE.md)).
- **Voltar ao Android:** grave de volta o seu backup de `boot` (e o resto do seu backup do LineageOS/Android, se tiver feito).

Achou um erro neste guia? Abra uma issue ou um PR: [CONTRIBUTING.md](../CONTRIBUTING.md).
