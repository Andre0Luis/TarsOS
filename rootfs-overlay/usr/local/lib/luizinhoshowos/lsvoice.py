#!/usr/bin/env python3
"""TarsOS: escuta pelo array de microfones do Echo Show 5.

O codec TLV320AIC3101 entrega no PCM hw:0,22 apenas S24_3LE, 4 canais, 16 kHz (os canais 1 e 2 têm os
microfones; 3 e 4 vêm mudos). Este módulo grava, detecta o início e o fim da fala por energia (VAD) e
devolve um WAV mono de 16 bits e 16 kHz, pronto para o Gemini transcrever.

Cuidados com o driver da MediaTek: os períodos/buffers da captura são múltiplos de 64 bytes
(512 quadros x 12 bytes = 6144; buffer de 2048 quadros = 24576).

Teste:  python3 lsvoice.py   (grava uma frase e mostra duração e nível)"""
import io
import os
import subprocess
import time
import wave

import numpy as np

ARECORD = ['/usr/bin/arecord', '-q', '-D', 'hw:0,22', '-f', 'S24_3LE', '-r', '16000', '-c', '4',
           '--period-size=512', '--buffer-size=2048', '-t', 'raw']
FRAME_BYTES = 12                         # 4 canais x 3 bytes
CHUNK_FRAMES = 512                       # 32 ms
CHUNK_BYTES = CHUNK_FRAMES * FRAME_BYTES
RATE = 16000


def mono24(buf):
    """Bytes S24_3LE de 4 canais -> média dos canais 1 e 2 como inteiros de 24 bits."""
    a = np.frombuffer(buf, dtype=np.uint8).reshape(-1, FRAME_BYTES).astype(np.int32)

    def ch(i):
        v = a[:, i * 3] | (a[:, i * 3 + 1] << 8) | (a[:, i * 3 + 2] << 16)
        return np.where(v & 0x800000, v - 0x1000000, v)
    return (ch(0) + ch(1)) // 2


def rms(m):
    return float(np.sqrt(np.mean((m / 8388608.0) ** 2))) if len(m) else 0.0


def to_wav(samples24):
    """24 bits -> 16 bits com normalização de ganho (até 12x) e empacota em WAV."""
    x = samples24.astype(np.float64)
    peak = float(np.max(np.abs(x))) or 1.0
    gain = min(12.0, 0.8 * 8388608.0 / peak)
    pcm = np.clip(x * gain / 256.0, -32768, 32767).astype('<i2')
    bio = io.BytesIO()
    with wave.open(bio, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return bio.getvalue()


class Listener:
    def __init__(self):
        self.level = 0.0                 # último nível (RMS 0..1), para a tela desenhar o medidor
        self.speech_active = False       # True enquanto há fala sendo captada (indicador "estou te ouvindo")
        self.proc = None

    def listen(self, max_s=10.0, silence_s=0.9, start_timeout=8.0, cancel=lambda: False, thr=None):
        """Grava uma fala. Devolve (wav_bytes, motivo). wav_bytes é None se ninguém falou ou se foi cancelado."""
        p = subprocess.Popen(ARECORD, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.proc = p
        chunks, pre = [], []
        floor_samples = []
        started, voiced_run, last_voice = False, 0, 0.0
        t_start = time.monotonic()
        reason = 'timeout'
        try:
            buf = b''
            while True:
                if cancel():
                    reason = 'cancelado'
                    break
                data = p.stdout.read(CHUNK_BYTES - len(buf))
                if not data:
                    reason = 'erro de captura'
                    break
                buf += data
                if len(buf) < CHUNK_BYTES:
                    continue
                m, buf = mono24(buf), b''
                lvl = rms(m)
                self.level = lvl
                now = time.monotonic()
                if len(floor_samples) < 10:                       # ~0,3 s iniciais: mede o ruído de fundo
                    floor_samples.append(lvl)
                    pre.append(m)
                    continue
                if thr is None:
                    floor = float(np.median(floor_samples))
                    thr = max(0.004, floor * 3.5)
                if not started:
                    pre.append(m)
                    pre = pre[-10:]                               # mantém 0,3 s antes da fala
                    voiced_run = voiced_run + 1 if lvl > thr else 0
                    if voiced_run >= 3:
                        started = True
                        self.speech_active = True
                        chunks = list(pre)
                        last_voice = now
                    elif now - t_start > start_timeout:
                        reason = 'silêncio'
                        break
                else:
                    chunks.append(m)
                    if lvl > thr:
                        last_voice = now
                    if now - last_voice > silence_s:
                        reason = 'ok'
                        break
                    if now - t_start > max_s + start_timeout:
                        reason = 'ok'
                        break
        finally:
            p.kill()
            p.wait()
            self.proc = None
            self.level = 0.0
            self.speech_active = False
        if not started or reason != 'ok':
            return None, reason
        samples = np.concatenate(chunks)
        return to_wav(samples), 'ok'


if __name__ == '__main__':
    print('Fale agora (até 10 s)...', flush=True)
    wav, why = Listener().listen()
    print('resultado:', why, '| %s' % ('%.1f s' % ((len(wav) - 44) / 32000.0) if wav else 'sem áudio'))
    if wav:
        open('/tmp/fala-vad.wav', 'wb').write(wav)
        print('salvo em /tmp/fala-vad.wav')
