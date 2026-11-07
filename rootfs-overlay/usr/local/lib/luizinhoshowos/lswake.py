#!/usr/bin/env python3
"""TarsOS: detector LOCAL da palavra de ativação ("TARS"), com o Vosk (Kaldi) rodando no próprio Echo.

O áudio de cada trecho de fala passa por aqui ANTES de qualquer coisa ir à nuvem: se o nome não for ouvido localmente,
nada sai do aparelho. Usa o modelo pequeno em português (vosk-model-small-pt-0.3, Apache 2.0) por ctypes na libvosk.

Como o nome "tars" quase certamente não está no vocabulário do modelo, o reconhecimento é LIVRE (sem gramática) e o nome é
procurado de forma aproximada entre as palavras que o modelo escreve (variantes fonéticas: tars, tarso, tás, dars...).
As variantes ficam em /var/lib/luizinhoshowos/wake-words.json e podem crescer com o uso (tools/wake_calibrate).

Teste:  python3 lswake.py arquivo.wav   (WAV 16 kHz mono 16 bits)
"""
import ctypes
import json
import os
import re
import sys
import wave

LIB = '/usr/lib/libvosk.so'
MODEL = '/opt/vosk-models/vosk-model-small-pt-0.3'
WORDS_FILE = '/var/lib/luizinhoshowos/wake-words.json'
GRAMMAR_FILE = '/var/lib/luizinhoshowos/wake-grammar.json'   # palavras do modelo que soam como "TARS" (resultado da calibração)
GRAMMAR_DEFAULT = ['tarso']                                    # "tars" não existe no vocabulário do modelo; "tarso" é o mais próximo
# variantes que o modelo português costuma escrever para "TARS" (ajustadas com testes reais)
DEFAULT_VARIANTS = ['tars', 'tarso', 'tarsa', 'tarsi', 'tarz', 'tás', 'tas', 'darsi', 'dars', 'tarce', 'tarze', 'tarçe', 'tarçi', 'tá res']


class Wake:
    def __init__(self):
        self.ok = False
        self.error = ''
        self.variants = self._load_variants()
        self.grammar = self._load_grammar()
        try:
            L = ctypes.CDLL(LIB)
            L.vosk_set_log_level.argtypes = [ctypes.c_int]
            L.vosk_set_log_level(-1)
            L.vosk_model_new.argtypes = [ctypes.c_char_p]
            L.vosk_model_new.restype = ctypes.c_void_p
            L.vosk_recognizer_new.argtypes = [ctypes.c_void_p, ctypes.c_float]
            L.vosk_recognizer_new.restype = ctypes.c_void_p
            L.vosk_recognizer_accept_waveform.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
            L.vosk_recognizer_accept_waveform.restype = ctypes.c_int
            L.vosk_recognizer_final_result.argtypes = [ctypes.c_void_p]
            L.vosk_recognizer_final_result.restype = ctypes.c_char_p
            L.vosk_recognizer_free.argtypes = [ctypes.c_void_p]
            self.lib = L
            self.model = L.vosk_model_new(MODEL.encode())
            self.ok = bool(self.model)
            if not self.ok:
                self.error = 'modelo não carregou'
        except (OSError, AttributeError) as e:
            self.error = str(e)

    @staticmethod
    def _load_grammar():
        try:
            g = json.load(open(GRAMMAR_FILE))
            return [w for w in g if isinstance(w, str) and w and w != '[unk]'] or list(GRAMMAR_DEFAULT)
        except (OSError, ValueError):
            return list(GRAMMAR_DEFAULT)

    @staticmethod
    def _load_variants():
        try:
            extra = json.load(open(WORDS_FILE))
            return sorted(set(DEFAULT_VARIANTS) | set(w.lower() for w in extra))
        except (OSError, ValueError):
            return list(DEFAULT_VARIANTS)

    def transcribe(self, pcm):
        """PCM 16 kHz mono 16 bits (bytes) -> texto reconhecido pelo Vosk (livre)."""
        L = self.lib
        rec = L.vosk_recognizer_new(self.model, ctypes.c_float(16000.0))
        try:
            step = 8000                                              # 0,25 s por vez
            for i in range(0, len(pcm), step):
                L.vosk_recognizer_accept_waveform(rec, pcm[i:i + step], len(pcm[i:i + step]))
            res = L.vosk_recognizer_final_result(rec)
            return json.loads(res.decode('utf-8', 'replace')).get('text', '')
        finally:
            L.vosk_recognizer_free(rec)

    def detect_grammar(self, pcm):
        """Modo gramática (vocabulário restrito): ~0,3x o tempo real neste processador, contra ~5x do reconhecimento livre.
        -> (ouviu_o_nome, texto). Só as palavras da gramática (e [unk]) podem sair."""
        if not self.ok:
            return False, ''
        L = self.lib
        if not getattr(self, '_grm_ready', False):
            L.vosk_recognizer_new_grm.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_char_p]
            L.vosk_recognizer_new_grm.restype = ctypes.c_void_p
            self._grm_ready = True
        gram = json.dumps(self.grammar + ['[unk]'], ensure_ascii=False).encode()
        rec = L.vosk_recognizer_new_grm(self.model, ctypes.c_float(16000.0), gram)
        try:
            for i in range(0, len(pcm), 8000):
                L.vosk_recognizer_accept_waveform(rec, pcm[i:i + 8000], len(pcm[i:i + 8000]))
            text = json.loads(L.vosk_recognizer_final_result(rec).decode('utf-8', 'replace')).get('text', '')
        finally:
            L.vosk_recognizer_free(rec)
        return any(w in text.split() for w in self.grammar), text

    def hit(self, text):
        t = ' ' + re.sub(r'\s+', ' ', text.lower()).strip() + ' '
        return any((' ' + v + ' ') in t or t.startswith(' ' + v) for v in self.variants)

    def detect(self, pcm):
        """-> (ouviu_o_nome, texto_reconhecido)"""
        if not self.ok:
            return False, ''
        text = self.transcribe(pcm)
        return self.hit(text), text


def wav_pcm(path):
    with wave.open(path, 'rb') as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, 'precisa de WAV 16 kHz mono 16 bits'
        return w.readframes(w.getnframes())


if __name__ == '__main__':
    w = Wake()
    print('modelo ok:', w.ok, w.error)
    for p in sys.argv[1:]:
        print(p, '->', w.detect(wav_pcm(p)))
