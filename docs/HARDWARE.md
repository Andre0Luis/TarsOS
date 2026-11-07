# Hardware: o que descobrimos do Echo Show 5 (1ª geração)

> **In English:** field notes on the Echo Show 5 1st gen (`checkers`, MT8163): the LK prepends ~840 chars to the kernel cmdline (limit 1024),
> the speaker amp enable is **inverted**, the MTK audio driver panics on writes that are not multiples of 64 bytes, the framebuffer is a portrait
> RGBA panel, there is no fbcon / camera V4L2 / proximity / Bluetooth in the 4.9 kernel. Never write `lk`, `preloader`, `tee1`, `tee2`.

Este documento é a memória técnica do projeto: o que **medimos**, não só o que supomos. Se você tem um aparelho e um resultado diferente, abra uma issue.

## Identidade do aparelho

| | |
|---|---|
| Aparelho | Amazon Echo Show 5, **1ª geração (2019)**, codinome `checkers` |
| SoC | MediaTek **MT8163** (ARMv7 no nosso kernel, 32 bits), 1 GB de RAM |
| Tela | painel **480×960 em retrato** (a tela física é 960×480 em paisagem), 32 bpp **RGBA**, 3 páginas |
| Touch | Goodix gt9xx (I²C) |
| Wi-Fi | MediaTek **MT7668** (`wlan0`) |
| Áudio | codec **RT5616** (saída) + **TLV320AIC3101** (array de microfones) |
| Luz | **JSA1214** (ALS; a proximidade do mesmo chip **não** funciona no kernel) |
| Câmera | sensores no I²C (`camera_main`, `camera_sub`), **sem V4L2** (só o ISP proprietário da MediaTek) |
| Botões | Volume ± (e as teclas do `gpio-keys`/`mtk-kpd`; veja `/proc/bus/input/devices`) |

## Cadeia de boot

```
BootROM (MT8163)  →  preloader  →  LK (Amazon, "little kernel")  →  microloader do amonet  →  kernel 4.9.77 do pmOS  →  initramfs  →  OpenRC
```

- O **amonet** explora o BootROM e o LK. Depois dele há **dois fastboots**: o da Amazon (**travado**, `unlock_status: false`) e o **liberado** (`handshake2.py FACTFACT` com o aparelho em USBDL; ou `to-fastboot`).
- Este LK **não tem `fastboot boot`**: um kernel novo só se testa gravando.
- **Layout do amonet na partição `boot`:** *stub* do microloader no offset 0, **cópia do cabeçalho do pmOS no offset 1024**, kernel e ramdisk a partir do offset 2048. O `fastboot flash boot` faz a injeção.
- **Modo de boot** pelos 16 primeiros bytes da partição `MISC`: `boot-recovery` → TWRP; `FASTBOOT_PLEASE` → fastboot liberado. No TarsOS: `to-recovery`, `to-fastboot`.
- **Nunca grave `lk`, `preloader`, `tee1`, `tee2`:** brick permanente (aviso do README do amonet).

### A descoberta principal: o limite do cmdline

- O LK prefixa **~840 caracteres** ao cmdline (LCM, serial, versões, `androidboot.*`). O kernel ARM aceita **1024**.
- O cmdline padrão do postmarketOS (`quiet splash plymouth.* ... pmos_boot_uuid=... pmos_root_uuid=...`, ~215) **estoura** o limite, e o boot morre **antes** do kernel iniciar: parado no logo "amazon" até o watchdog reiniciar (~60 s). Dá a impressão de problema de ramdisk ou de kernel; **não é**.
- **Regra:** o nosso fica em **~180 caracteres ou menos**:
  `bootopt=64S3,32N2,32N2 pmos_boot_uuid=<uuid> pmos_root_uuid=<uuid> pmos_rootfsopts=defaults`
- `tools/bootimg_cmdline.py` mostra o tamanho (somando o prefixo do LK) e faz a troca com segurança na imagem já injetada (só o campo `cmdline` do cabeçalho em 1024; o stub e o kernel não são tocados; veja o cabeçalho do script para o porquê).
- `apk upgrade` do kernel **não** regrava a partição `boot` (o `boot-deploy` do aparelho não tem `flash_kernel_on_update`): só troca arquivos em `/boot`. O risco existe se alguém gravar esse `boot.img` à mão.

### Diagnóstico de boot sem UART

- **`ramoops`** (`/sys/fs/pstore/console-ramoops`): guarda o log do boot anterior **se não faltar energia** (reinicie a quente e entre no TWRP com Volume Up sem desligar da tomada).
- **Contador de montagem do ext4** (`mount_count`, offset 52 do superbloco): compara a imagem com o disco e mostra se o Linux chegou a montar a raiz.
- **Trocar peças entre imagens que funcionam e que não funcionam** (kernel do TWRP × ramdisk do pmOS) isola a causa. Foi assim que descobrimos o cmdline.

## Kernels

| Kernel | Notas |
|---|---|
| **TWRP** | 4.9.77, 32 bits, sem `devtmpfs`, `android_usb` (acm, rndis, eem, ffs), sem Wi-Fi |
| **postmarketOS** (o nosso) | 4.9.77, 32 bits, com `devtmpfs`, VT, Wi-Fi MT7668. **Sem `fbcon`** (por isso o `fbkeyboard` não sobe), **sem `/dev/mem`**, **sem `mmap` no driver de áudio da MTK** |
| **LineageOS** | 64 bits (`bootopt=...64N2`) |

## Tela

Painel 480×960 retrato. Para a imagem paisagem 960×480, **gire 270°** (90° sai de cabeça para baixo). Gravação direta em `/dev/fb0`. O console de texto do kernel não existe (sem `fbcon`); o terminal na tela é o nosso (`lsterm.py` / app Terminal).

## Áudio (a parte mais difícil)

**Cadeia do alto-falante:** DL1 → I²S1 (pinos 72/73/74, função 4) → **RT5616** (`hw:0,23`, `RT5616_Playback`) → **LOUT diferencial** → **amplificador externo** com enable no **pino 35**.

- 🔑 **`Ext_Speaker_Amp_Switch` é INVERTIDO:** `Off` = pino 35 em nível baixo = **amplificador ligado**; `On` = mutado. O driver (`realtek,amp-gpio`) sobe o pino 35 no boot, então o alto-falante **nasce mudo**. `audio-speaker` aplica a receita completa (`Audio_I2S1_Setting On`, os mixers do RT5616 e o amplificador).
- A receita veio do `audio_device_rt5616.xml` do Echo Show 5 de 2ª geração (`amazon-oss/android_vendor_amazon_cronos`). O `audio_device.xml` do `checkers` é o genérico da MediaTek e não ajuda.
- 🔥 **Bug do driver da MediaTek:** o *playback* I²S0DL1 entra em ***kernel panic*** com escritas que **não sejam múltiplas de 64 bytes**. Nunca escreva no PCM por um caminho que não alinhe (o `dmix` e o `mmap_emul` do ALSA falharam). Solução: o `lsaudiod` mistura tudo e escreve em blocos alinhados (9600 bytes = 150 × 64).
- O fone P2 sai pela HP do RT5616 e o estado do P2 vem de `/sys/class/switch/h2w/state` (0 nada, 1 fone com microfone, 2 fone sem microfone). O `lsjack` liga/desliga o amplificador conforme o estado.
- **Microfones:** `hw:0,22`, **S24_3LE, 4 canais, 16 kHz**; só os **canais 1 e 2** têm sinal (3 e 4 vêm mudos). Buffers múltiplos de 64 bytes (`--period-size=512 --buffer-size=2048`). `audio-mic` ajusta o ganho (MICPGA).
- ⚠️ **Nunca varra todos os controles com `amixer cget`:** travou o kernel. Controles são lidos/escritos pelo nome.
- Ferramentas de diagnóstico seguras: `debugfs/asoc/.../dapm` (quais blocos ligam), `debugfs/regmap/2-001b/registers` (registradores do RT5616), `debugfs/pinctrl/.../pinmux-pins`, `debugfs/gpio`. `i2cset -f` no `/dev/i2c-2` escreve direto no codec.

## USB

O pmOS expõe RNDIS; o **macOS não suporta RNDIS**. O TarsOS (`etc/local.d/luizinhoshowos-usb.start`) troca por uma **serial CDC-ACM** com shell root em `ttyGS0` (`/etc/inittab`). No Mac aparece como `/dev/cu.usbmodem*`; o cliente é o `tools/echosh.py`.

## O que não funciona (e por quê)

| Recurso | Situação |
|---|---|
| **Câmera** | os sensores aparecem no I²C, mas o kernel não tem V4L2 (`/dev/video*`); só o ISP proprietário da MediaTek. Precisaria de um kernel novo |
| **Proximidade** | `dmesg`: `ps_enable_data: no ps control path`. Só o sensor de luz funciona |
| **Bluetooth** | quebrado no kernel (segundo a wiki do pmOS) |
| **GPU 3D** | sem aceleração; por isso a UI é por software |
| **`fbcon`, `/dev/mem`** | ausentes; o kernel próprio é meta de longo prazo |

Fontes úteis: [wiki do postmarketOS (amazon-checkers)](https://wiki.postmarketos.org/wiki/Amazon_Echo_Show_5_(amazon-checkers)), [amonet](https://github.com/bengris32/amonet).
