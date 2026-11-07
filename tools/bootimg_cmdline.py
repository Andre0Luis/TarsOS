#!/usr/bin/env python3
"""TarsOS: mostra ou troca a linha de comando do kernel (cmdline) dentro de um boot.img Android.

Por que existe: o LK do Echo Show 5 (checkers) injeta ~840 caracteres no cmdline e o kernel ARM aceita 1024. O cmdline
padrão do postmarketOS (~215) estoura o limite e o aparelho trava no logo "amazon" ANTES de o kernel iniciar.
O nosso precisa ficar em ~180 caracteres ou menos. Veja docs/HARDWARE.md.

Uso:
  tools/bootimg_cmdline.py show boot.img
  tools/bootimg_cmdline.py set  boot.img "bootopt=64S3,32N2,32N2 pmos_boot_uuid=... pmos_root_uuid=... pmos_rootfsopts=defaults"

Cabeçalho boot.img v0: magic "ANDROID!" no offset 0; cmdline = 512 bytes a partir do byte 64; extra_cmdline = 1024 bytes
a partir do byte 608.
ATENÇÃO (amonet): numa imagem já injetada pelo fastboot liberado, o cabeçalho do offset 0 é o STUB do microloader (com
marcadores próprios) e o cabeçalho REAL do pmOS fica copiado no offset 1024. Nesse caso só o do offset 1024 é alterado;
o stub nunca é tocado. Além disso, essa cópia em 1024 se sobrepõe ao início do kernel (que começa em 2048): só o campo
`cmdline` (bytes 1088..1599) é regravado; o `extra_cmdline` dela NÃO é tocado (seriam bytes do kernel).
Trabalha "no lugar": faça uma cópia antes.
"""
import sys

MAGIC = b'ANDROID!'
CMDLINE_OFF, CMDLINE_LEN = 64, 512
EXTRA_OFF, EXTRA_LEN = 608, 1024
LK_PREFIX = 840          # medido com `cat /proc/cmdline` no TWRP: o que o LK acrescenta
KERNEL_LIMIT = 1024


def headers(buf):
    """Offsets dos cabeçalhos ANDROID! que existem (0 e, na imagem injetada pelo amonet, 1024)."""
    return [o for o in (0, 1024) if buf[o:o + 8] == MAGIC]


def real_header(buf):
    """Onde está o cabeçalho real: 1024 se a imagem foi injetada pelo amonet (offset 0 = stub), senão 0."""
    offs = headers(buf)
    return offs[-1] if offs else None


def show(path):
    buf = open(path, 'rb').read()
    offs = headers(buf)
    if not offs:
        sys.exit('não achei o cabeçalho ANDROID! (isto é um boot.img?)')
    real = real_header(buf)
    for o in offs:
        cmd = buf[o + CMDLINE_OFF:o + CMDLINE_OFF + CMDLINE_LEN].split(b'\0')[0].decode('ascii', 'replace')
        extra = '' if o == 1024 else buf[o + EXTRA_OFF:o + EXTRA_OFF + EXTRA_LEN].split(b'\0')[0].decode('ascii', 'replace')
        total = len(cmd) + (1 + len(extra) if extra else 0)
        print('cabeçalho no offset %d%s: %d caracteres' % (o, '' if o == real else ' (stub do microloader, não mexer)', total))
        print('  cmdline:', cmd)
        if extra:
            print('  extra  :', extra)
        if o == real:
            print('  com o prefixo do LK (~%d): ~%d de %d  %s' % (LK_PREFIX, LK_PREFIX + total, KERNEL_LIMIT,
                                                               'OK' if LK_PREFIX + total <= KERNEL_LIMIT - 20 else 'PERIGO: pode travar no logo'))


def set_cmdline(path, cmdline):
    data = cmdline.encode('ascii')
    if len(data) > 400:
        sys.exit('cmdline longo demais (%d). Mantenha em ~180 caracteres.' % len(data))
    if LK_PREFIX + len(data) > KERNEL_LIMIT - 20:
        sys.exit('com o prefixo do LK isso passa de %d caracteres: o aparelho travaria no logo.' % (KERNEL_LIMIT - 20))
    buf = bytearray(open(path, 'rb').read())
    o = real_header(buf)
    if o is None:
        sys.exit('não achei o cabeçalho ANDROID! (isto é um boot.img?)')
    buf[o + CMDLINE_OFF:o + CMDLINE_OFF + CMDLINE_LEN] = data.ljust(CMDLINE_LEN, b'\0')
    if o == 0:                                       # imagem comum: o extra_cmdline cabe na página do cabeçalho
        buf[o + EXTRA_OFF:o + EXTRA_OFF + EXTRA_LEN] = b'\0' * EXTRA_LEN
    open(path, 'wb').write(buf)
    print('cmdline gravado no cabeçalho do offset %d: %d caracteres' % (o, len(data)))


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'show':
        show(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == 'set':
        set_cmdline(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
