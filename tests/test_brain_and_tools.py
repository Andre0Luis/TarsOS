# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The TarsOS Authors
"""Comandos locais do TARS (sem Gemini), persona, memória e utilidades das ferramentas. Usa pastas temporárias."""
import json
import os
import tempfile
import unittest

import _paths  # noqa: F401
import lsbrain
import lstools


class BrainLocal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        lsbrain.CFG_FILE = os.path.join(self.tmp, 'tars.json')
        lsbrain.HIST_FILE = os.path.join(self.tmp, 'hist.json')
        lsbrain.MEMORY_FILE = os.path.join(self.tmp, 'mem.json')
        self.b = lsbrain.Brain()

    def test_humor_e_honestidade_persistem(self):
        self.assertIn('90%', self.b.local('TARS, coloca o humor em 90%'))
        self.assertEqual(self.b.humor, 90)
        self.b.local('ajusta a honestidade para 40 por cento')
        self.assertEqual(self.b.honesty, 40)
        self.assertEqual(lsbrain.Brain().humor, 90)             # salvo em disco

    def test_valores_limitados(self):
        self.b.local('muda o humor para 250')
        self.assertEqual(self.b.humor, 100)

    def test_modos(self):
        self.b.local('modo militar')
        self.assertEqual(self.b.mode, 'militar')
        self.assertIn('ESTILO ATUAL', lsbrain.persona(75, 90, '', self.b.mode, False, ''))
        self.b.local('volta pro modo normal')
        self.assertEqual(self.b.mode, 'padrao')

    def test_dono(self):
        self.assertIn('Cooper', self.b.local('me chame de cooper'))
        self.assertEqual(self.b.owner, 'Cooper')
        self.assertIn('O nome do dono é Cooper', lsbrain.persona(75, 90, '', 'padrao', False, 'Cooper'))
        self.assertIn('comandante', lsbrain.persona(75, 90, '', 'padrao', False, ''))

    def test_voz_e_motor(self):
        self.b.local('troca a voz para Orus')
        self.assertEqual(self.b.voice_name, 'Orus')
        self.b.local('usa a voz local')
        self.assertEqual(self.b.voice_engine, 'piper')
        self.b.local('volta a voz do google')
        self.assertEqual(self.b.voice_engine, 'gemini')

    def test_frases_normais_vao_para_o_gemini(self):
        for t in ('que horas são?', 'qual a capital da França', 'liga a luz da sala'):
            self.assertIsNone(self.b.local(t), t)

    def test_memoria_lida_do_arquivo(self):
        json.dump([{'fato': 'toma café sem açúcar'}], open(lsbrain.MEMORY_FILE, 'w'))
        self.assertIn('café sem açúcar', self.b.local('o que você sabe sobre mim?'))

    def test_lema_so_de_vez_em_quando(self):
        self.b.history = [{'role': 'model', 'text': 'Every second counts.'}]
        self.assertFalse(any(self.b._motto_ok() for _ in range(300)))        # acabou de sair: nunca
        self.b.history = []
        usos = sum(self.b._motto_ok() for _ in range(2000))
        self.assertTrue(40 < usos < 260, usos)                                 # ~7%

    def test_persona_sem_nomes_pessoais_fixos(self):
        p = lsbrain.persona(75, 90, '', 'padrao', False, '')
        self.assertNotIn('Luiz', p)
        self.assertIn('Every second counts', p)


class Utilidades(unittest.TestCase):
    def test_hora_por_extenso(self):
        casos = {(20, 29): 'vinte horas e vinte e nove minutos', (1, 1): 'uma hora e um minuto', (2, 0): 'duas horas em ponto',
                 (0, 5): 'meia-noite e cinco minutos', (12, 0): 'meio-dia', (12, 45): 'meio-dia e quarenta e cinco minutos',
                 (23, 59): 'vinte e três horas e cinquenta e nove minutos'}
        for (h, m), esperado in casos.items():
            self.assertEqual(lstools.hora_extenso(h, m), esperado)

    def test_memoria_do_tars(self):
        tmp = tempfile.mkdtemp()
        lstools.VAR = tmp
        lstools.MEMORY_FILE = os.path.join(tmp, 'mem.json')
        self.assertTrue(lstools.memory_add('gosta de ficção científica')['ok'])
        self.assertTrue(lstools.memory_add('Gosta de ficção científica').get('ja_sabia'))     # sem duplicar
        self.assertIn('ficção', lstools.memory_context())
        self.assertEqual(lstools.memory_forget('ficção')['removidos'], 1)
        self.assertEqual(lstools.memory_context(), '')
        self.assertIn('erro', lstools.memory_add('x'))

    def test_intencao_e_reflexao(self):
        tmp = tempfile.mkdtemp()
        lstools.VAR = tmp
        lstools.INTENT_FILE = os.path.join(tmp, 'i.json')
        lstools.REFLECT_FILE = os.path.join(tmp, 'r.json')
        self.assertEqual(lstools.intencao_get(), '')
        lstools.intencao_set('terminar o relatório')
        self.assertEqual(lstools.intencao_get(), 'terminar o relatório')
        texto, cache = lstools.reflexao_get()
        self.assertFalse(cache)
        self.assertIn(texto, lstools.REFLEXOES)
        lstools.reflexao_save('uma reflexão nova')
        self.assertEqual(lstools.reflexao_get(), ('uma reflexão nova', True))

    def test_declaracoes_de_ferramentas_validas(self):
        class UI:
            confirm = staticmethod(lambda t: False)
            event = staticmethod(lambda t: None)
        decl = lstools.Tools(UI()).declarations()[0]['functionDeclarations']
        nomes = [d['name'] for d in decl]
        self.assertEqual(len(nomes), len(set(nomes)), 'ferramenta duplicada')
        for esperado in ('run_command', 'web_search', 'noticias', 'abrir_navegador', 'lembrar', 'briefing'):
            self.assertIn(esperado, nomes)
        for d in decl:
            self.assertTrue(d['description'])
            for k, v in d.get('parameters', {}).get('properties', {}).items():
                self.assertIn(v['type'], ('STRING', 'INTEGER', 'NUMBER', 'BOOLEAN'), (d['name'], k))


if __name__ == '__main__':
    unittest.main()
