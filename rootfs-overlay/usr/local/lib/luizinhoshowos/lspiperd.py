#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The TarsOS Authors
"""TarsOS: serviço da voz local (Piper). Carrega o modelo UMA vez (leva ~17 s neste processador) e atende o tars-say
pelo socket /run/lspiper.sock. Protocolo: o cliente manda o texto em UTF-8 terminado em \\n; o serviço responde com blocos
[taxa u32][tamanho u32][PCM s16le mono], um por frase, e termina com um bloco de tamanho 0."""
import os
import socket
import struct
import threading
import time

import lspiper

SOCK = '/run/lspiper.sock'
READY = '/run/luizinhoshowos/piper-ready'


def handle(conn, piper, lock):
    try:
        conn.settimeout(10)
        data = b''
        while not data.endswith(b'\n') and len(data) < 4096:
            part = conn.recv(1024)
            if not part:
                break
            data += part
        text = data.decode('utf-8', 'replace').strip()
        conn.settimeout(120)
        if text:
            with lock:                                        # uma síntese por vez
                for pcm, rate in piper.speak_iter(text):
                    conn.sendall(struct.pack('<II', rate, len(pcm)) + pcm)
        conn.sendall(struct.pack('<II', 0, 0))
    except (OSError, RuntimeError):
        pass
    finally:
        conn.close()


def main():
    t0 = time.time()
    piper = lspiper.Piper()
    if not piper.ok:
        raise SystemExit('piper indisponível')
    print('modelo carregado em %.1fs' % (time.time() - t0), flush=True)
    try:
        os.remove(SOCK)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    os.chmod(SOCK, 0o666)
    srv.listen(4)
    os.makedirs(os.path.dirname(READY), exist_ok=True)
    open(READY, 'w').close()
    lock = threading.Lock()
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn, piper, lock), daemon=True).start()


if __name__ == '__main__':
    main()
