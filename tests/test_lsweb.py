# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The TarsOS Authors
"""Navegador em modo leitura e RSS (lsweb): análise de HTML, URLs e feeds, sem rede (http_get é trocado por um falso)."""
import unittest

import _paths  # noqa: F401
import lsweb

HTML = """<!doctype html><html><head><title>Página de teste</title><script>var x = 1;</script></head>
<body><nav><a href="/menu">Menu</a></nav>
<main><h1>Título principal</h1>
<p>Primeiro parágrafo com <a href="/rel">um link relativo</a> e bastante texto para passar do mínimo de palavras do modo leitura
do navegador, que só mostra o miolo da página quando existe texto suficiente dentro de main ou article.</p>
<ul><li>item um</li><li>item dois</li></ul>
<p>Segundo parágrafo, também com texto suficiente para contar palavras e garantir que o corte do miolo funcione bem.</p>
</main><footer>rodapé que não deve aparecer</footer></body></html>"""

DDG = """<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexemplo.com%2Fa&rut=x">Resultado A</a>
<a class="result__snippet">Resumo do resultado A</a></div>
<div class="result"><a class="result__a" href="//duckduckgo.com/y.js?ad=1">Anúncio</a><a class="result__snippet">comprar</a></div>
<div class="result"><a class="result__a" href="https://exemplo.org/b">Resultado B</a><a class="result__snippet">Resumo B</a></div>"""

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Manchete um</title><link>https://x/1</link><description><![CDATA[<p>Resumo <b>um</b></p>]]></description><pubDate>Fri, 06 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Manchete dois</title><link>https://x/2</link><description>Resumo dois</description></item>
<item><link>https://x/sem-titulo</link></item></channel></rss>"""


class Parse(unittest.TestCase):
    def test_miolo_titulo_e_links(self):
        p = lsweb.parse_html(HTML, 'https://exemplo.com/pagina')
        self.assertEqual(p.title, 'Página de teste')
        text = p.text()
        self.assertIn('Título principal', text)
        self.assertIn('item um', text)
        self.assertNotIn('rodapé', text)          # footer fora
        self.assertNotIn('Menu', text)            # nav fora (e fora do miolo)
        self.assertNotIn('var x', text)           # script fora
        links = [h for _, toks in p.blocks for _, h in toks if h]
        self.assertIn('https://exemplo.com/rel', links)       # link relativo resolvido

    def test_titulo_ignora_svg(self):
        p = lsweb.parse_html('<html><head><title>Certo</title></head><body><svg><title>ícone</title></svg><p>texto</p></body></html>', 'https://x')
        self.assertEqual(p.title, 'Certo')

    def test_urls(self):
        for u in ('https://exemplo.com', 'exemplo.com', 'exemplo.com.br/caminho?q=1', 'http://10.0.0.1:8080/x'):
            self.assertTrue(lsweb.looks_like_url(u), u)
        for q in ('receita de pão de queijo', 'o que é isso', '3.14', 'tempo amanhã'):
            self.assertFalse(lsweb.looks_like_url(q), q)
        self.assertEqual(lsweb.normalize('exemplo.com'), 'https://exemplo.com')
        self.assertEqual(lsweb.normalize('http://a.b'), 'http://a.b')

    def test_loopback_bloqueado(self):
        for h in ('localhost', '127.0.0.1', '127.1.2.3', '169.254.169.254', 'app.localhost', '::1'):
            self.assertTrue(lsweb.is_loopback(h), h)
        self.assertFalse(lsweb.is_loopback('exemplo.com'))
        with self.assertRaises(ValueError):
            lsweb.http_get('http://127.0.0.1:8080/')
        with self.assertRaises(ValueError):
            lsweb.http_get('file:///etc/passwd')


class Rede(unittest.TestCase):
    def setUp(self):
        self._orig = lsweb.http_get

    def tearDown(self):
        lsweb.http_get = self._orig

    def test_busca_ddg(self):
        lsweb.http_get = lambda url, **k: (url, 'text/html', DDG)
        p = lsweb.search('qualquer coisa')
        hrefs = [h for _, toks in p.blocks for _, h in toks if h]
        self.assertIn('https://exemplo.com/a', hrefs)         # redirecionamento do DDG desfeito
        self.assertIn('https://exemplo.org/b', hrefs)
        self.assertTrue(all('y.js' not in h for h in hrefs))  # anúncio descartado

    def test_busca_sem_resultados_levanta(self):
        lsweb.http_get = lambda url, **k: (url, 'text/html', '<html>captcha</html>')
        with self.assertRaises(ValueError):
            lsweb.search('x')

    def test_rss(self):
        lsweb.http_get = lambda url, **k: (url, 'application/rss+xml', RSS)
        itens = lsweb.rss('https://x/feed')
        self.assertEqual([i['titulo'] for i in itens], ['Manchete um', 'Manchete dois'])    # item sem título é ignorado
        self.assertEqual(itens[0]['resumo'], 'Resumo um')
        self.assertEqual(itens[0]['link'], 'https://x/1')

    def test_pagina_do_resumo(self):
        p = lsweb.page_from_summary('Busca: x', 'linha 1\nlinha 2', [{'titulo': 'Fonte', 'url': 'https://f/1'}])
        self.assertIn('linha 2', p.text())
        self.assertEqual([h for _, toks in p.blocks for _, h in toks if h], ['https://f/1'])


if __name__ == '__main__':
    unittest.main()
