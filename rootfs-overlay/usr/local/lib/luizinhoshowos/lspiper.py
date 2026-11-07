#!/usr/bin/env python3
"""TarsOS: voz local do TARS com o Piper (síntese neural, open source, 100% no aparelho) via libpiper/ctypes.

Substitui o espeak-ng (metálico) quando o Gemini TTS não está disponível. Modelo: /opt/piper-voices/pt_BR-faber-medium.onnx.
Teste:  python3 lspiper.py "Olá, comandante." > /tmp/saida.raw     (PCM s16le mono; taxa e tempo no stderr)
"""
import ctypes
import os
import sys
import time

import numpy as np

LIB = '/usr/lib/libpiper.so'
MODEL = os.environ.get('PIPER_MODEL', '/opt/piper-voices/pt_BR-faber-medium.onnx')
ESPEAK_DATA = '/usr/share/espeak-ng-data'


class _Opts(ctypes.Structure):
    _fields_ = [('speaker_id', ctypes.c_int), ('length_scale', ctypes.c_float),
                ('noise_scale', ctypes.c_float), ('noise_w_scale', ctypes.c_float)]


class _Chunk(ctypes.Structure):
    _fields_ = [('samples', ctypes.c_void_p), ('num_samples', ctypes.c_size_t), ('sample_rate', ctypes.c_int),
                ('is_last', ctypes.c_bool), ('phonemes', ctypes.c_void_p), ('num_phonemes', ctypes.c_size_t),
                ('phoneme_ids', ctypes.c_void_p), ('num_phoneme_ids', ctypes.c_size_t),
                ('alignments', ctypes.c_void_p), ('num_alignments', ctypes.c_size_t)]


class Piper:
    def __init__(self, model=MODEL):
        self.ok = False
        self.rate = 22050
        if not (os.path.exists(LIB) and os.path.exists(model)):
            return
        L = ctypes.CDLL(LIB)
        L.piper_create.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
        L.piper_create.restype = ctypes.c_void_p
        L.piper_default_synthesize_options.argtypes = [ctypes.c_void_p]
        L.piper_default_synthesize_options.restype = _Opts
        L.piper_synthesize_start.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(_Opts)]
        L.piper_synthesize_start.restype = ctypes.c_int
        L.piper_synthesize_next.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Chunk)]
        L.piper_synthesize_next.restype = ctypes.c_int
        self.lib = L
        self.synth = L.piper_create(model.encode(), None, ESPEAK_DATA.encode())
        self.ok = bool(self.synth)

    def speak_iter(self, text, length_scale=1.0):
        """Texto -> gera (PCM s16le mono, taxa) frase por frase. Levanta RuntimeError se a síntese falhar."""
        L = self.lib
        opts = L.piper_default_synthesize_options(self.synth)
        opts.length_scale = length_scale
        if L.piper_synthesize_start(self.synth, text.encode('utf-8'), ctypes.byref(opts)) != 0:
            raise RuntimeError('piper_synthesize_start falhou')
        chunk = _Chunk()
        while True:
            r = L.piper_synthesize_next(self.synth, ctypes.byref(chunk))
            if r < 0:
                raise RuntimeError('piper_synthesize_next falhou')
            self.rate = chunk.sample_rate or self.rate
            if chunk.num_samples:                         # o último trecho vem junto com o código "terminou" (1)
                f = np.frombuffer(ctypes.string_at(chunk.samples, chunk.num_samples * 4), dtype=np.float32)
                yield np.clip(f * 32767.0, -32768, 32767).astype('<i2').tobytes(), self.rate
            if r != 0 or chunk.is_last:
                return

    def speak(self, text, length_scale=1.0):
        """Texto -> (PCM s16le mono bytes, taxa) de uma vez."""
        out = [pcm for pcm, _ in self.speak_iter(text, length_scale)]
        return b''.join(out), self.rate


if __name__ == '__main__':
    t0 = time.time()
    p = Piper()
    if not p.ok:
        sys.exit('piper indisponível')
    t1 = time.time()
    pcm, rate = p.speak(' '.join(sys.argv[1:]) or 'Olá, comandante. Estou funcionando.')
    t2 = time.time()
    dur = len(pcm) / 2 / rate
    sys.stderr.write('carga %.1fs | síntese %.1fs para %.1fs de áudio (fator %.2fx) | %d Hz\n' % (t1 - t0, t2 - t1, dur, (t2 - t1) / max(dur, 0.01), rate))
    sys.stdout.buffer.write(pcm)
