# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The TarsOS Authors
"""tools/bootimg_cmdline.py mexe num boot.img: o erro aqui brica o aparelho. Estes testes usam imagens sintéticas e exigem
que o stub do microloader e o kernel continuem idênticos byte a byte."""
import importlib.util
import os
import tempfile
import unittest

import _paths

spec = importlib.util.spec_from_file_location('bootimg_cmdline', os.path.join(_paths.TOOLS, 'bootimg_cmdline.py'))
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)

KERNEL = bytes((i * 7 + 3) % 251 for i in range(4096))


def header(cmdline, extra=b''):
    h = bytearray(1632)
    h[0:8] = b'ANDROID!'
    h[64:64 + len(cmdline)] = cmdline
    h[608:608 + len(extra)] = extra
    return bytes(h)


def plain_image(cmdline=b'quiet splash pmos_boot_uuid=aaaa'):
    img = bytearray(2048)
    img[0:1632] = header(cmdline)
    return bytes(img) + KERNEL


def injected_image(cmdline=b'bootopt=64S3,32N2,32N2 pmos_boot_uuid=aaaa'):
    """Layout do amonet: stub no 0, cabeçalho real copiado no 1024 (só os primeiros 1024 bytes cabem), kernel no 2048."""
    img = bytearray(2048)
    img[0:1632] = header(b'bootopt=64S3,32N2,32N2', b'...backup payload')
    real = header(cmdline)
    img[1024:2048] = real[:1024]
    return bytes(img) + KERNEL


class Cmdline(unittest.TestCase):
    def run_set(self, data, cmdline):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
        try:
            bc.set_cmdline(f.name, cmdline)
            return open(f.name, 'rb').read()
        finally:
            os.unlink(f.name)

    def test_imagem_comum(self):
        out = self.run_set(plain_image(), 'bootopt=64S3 pmos_boot_uuid=bbbb')
        novo = b'bootopt=64S3 pmos_boot_uuid=bbbb'
        self.assertEqual(out[64:64 + len(novo)], novo)
        self.assertEqual(out[2048:], KERNEL)
        self.assertEqual(len(out), len(plain_image()))

    def test_imagem_injetada_preserva_stub_e_kernel(self):
        orig = injected_image()
        out = self.run_set(orig, 'bootopt=64S3,32N2,32N2 teste=1')
        self.assertEqual(out[:1024], orig[:1024], 'o stub do microloader foi alterado')
        self.assertEqual(out[2048:], orig[2048:], 'o kernel foi alterado')
        self.assertEqual(out[1024 + 64:1024 + 64 + 30], b'bootopt=64S3,32N2,32N2 teste=1\0'[:30])
        changed = [i for i in range(len(orig)) if orig[i] != out[i]]
        self.assertTrue(changed and all(1088 <= i < 1600 for i in changed), 'bytes fora do campo cmdline mudaram')

    def test_mesmo_cmdline_nao_muda_nada(self):
        orig = injected_image(b'bootopt=64S3 x=1')
        self.assertEqual(self.run_set(orig, 'bootopt=64S3 x=1'), orig)

    def test_limite_com_prefixo_do_lk(self):
        with self.assertRaises(SystemExit):
            self.run_set(injected_image(), 'x' * 300)           # 840 + 300 > 1004
        self.run_set(injected_image(), 'x' * 150)               # cabe

    def test_nao_e_boot_img(self):
        with self.assertRaises(SystemExit):
            self.run_set(b'\0' * 4096, 'abc')

    def test_cabecalho_real(self):
        self.assertEqual(bc.real_header(plain_image()), 0)
        self.assertEqual(bc.real_header(injected_image()), 1024)


if __name__ == '__main__':
    unittest.main()
