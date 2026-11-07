#!/usr/bin/env python3
"""TarsOS: motor do navegador (modo leitura) e dos feeds de notícias. Só biblioteca padrão.

Não há motor de JavaScript nem de CSS: a página vira uma lista de blocos de texto (títulos, parágrafos, itens, citações)
com links clicáveis, parecida com o "modo leitura" dos navegadores. Funciona bem para notícias, Wikipédia, documentação e
buscas; sites que só existem em JavaScript (YouTube, redes sociais) aparecem vazios.

Teste:  python3 lsweb.py <url | consulta | rss:URL>
"""
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = 'Mozilla/5.0 (X11; Linux armv7l) AppleWebKit/537.36 (KHTML, like Gecko) TarsOS/1.0'
MAX_BYTES = 1500000
MAX_BLOCKS = 700

SKIP = {'script', 'style', 'noscript', 'svg', 'template', 'iframe', 'nav', 'footer', 'aside', 'form', 'button', 'select', 'canvas'}
BLOCK = {'p', 'div', 'section', 'article', 'main', 'header', 'ul', 'ol', 'li', 'tr', 'table', 'blockquote', 'pre', 'br', 'hr',
         'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'dd', 'dt', 'figcaption', 'details', 'summary'}


class Page:
    def __init__(self, url='', title=''):
        self.url = url
        self.title = title
        self.blocks = []          # [(tipo, [(palavra, href|None), ...])]  tipo: h1 h2 p li q

    def text(self, limit=6000):
        out = []
        for kind, toks in self.blocks:
            out.append(' '.join(w for w, _ in toks))
        s = '\n'.join(out)
        return s[:limit]

    def words(self):
        return sum(len(t) for _, t in self.blocks)


class _Reader(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.page = Page(base)
        self.skip = 0
        self.in_title = False
        self.title = ''
        self.cur = []
        self.kind = 'p'
        self.href = None
        self.lists = 0
        self.quote = 0
        self.main = 0                 # dentro de <article>/<main>: se houver bastante texto ali, só ele é mostrado
        self.main_blocks = []

    def flush(self):
        if self.cur and len(self.page.blocks) < MAX_BLOCKS:
            self.page.blocks.append((self.kind, self.cur))
            if self.main:
                self.main_blocks.append((self.kind, self.cur))
        self.cur = []
        self.kind = 'q' if self.quote else ('li' if self.lists else 'p')

    def handle_starttag(self, tag, attrs):
        if tag == 'title':
            self.in_title = not self.skip and not self.title      # só o <title> do cabeçalho (não o de ícones SVG)
            return
        if tag in SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in ('article', 'main'):
            self.flush()
            self.main += 1
        if tag in BLOCK:
            self.flush()
            if tag in ('h1', 'h2'):
                self.kind = 'h1' if tag == 'h1' else 'h2'
            elif tag in ('h3', 'h4', 'h5', 'h6'):
                self.kind = 'h2'
            elif tag == 'li':
                self.kind = 'li'
                self.lists += 1
            elif tag == 'blockquote':
                self.quote += 1
                self.kind = 'q'
        if tag == 'a':
            href = dict(attrs).get('href') or ''
            if href and not href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
                u = urllib.parse.urljoin(self.base, href)
                self.href = u if u.startswith(('http://', 'https://')) else None

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False
            return
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == 'a':
            self.href = None
        if tag in ('article', 'main'):
            self.flush()
            self.main = max(0, self.main - 1)
        if tag in BLOCK:
            self.flush()
            if tag == 'li':
                self.lists = max(0, self.lists - 1)
            elif tag == 'blockquote':
                self.quote = max(0, self.quote - 1)
            self.kind = 'q' if self.quote else ('li' if self.lists else 'p')

    def handle_data(self, data):
        if self.in_title:
            self.title += data
            return
        if self.skip:
            return
        for w in data.split():
            self.cur.append((w, self.href))


def parse_html(raw, base=''):
    r = _Reader(base)
    try:
        r.feed(raw)
        r.close()
    except Exception:
        pass
    r.flush()
    page = r.page
    if sum(len(t) for _, t in r.main_blocks) >= 120:
        page.blocks = r.main_blocks
    page.title = re.sub(r'\s+', ' ', r.title).strip()
    clean, last = [], None
    for kind, toks in page.blocks:
        if len(toks) <= 2 and all(h for _, h in toks):          # "chips" de menu: só links curtos
            continue
        key = ' '.join(w for w, _ in toks)
        if key == last:
            continue
        last = key
        clean.append((kind, toks))
    page.blocks = clean
    if not page.title:
        h = next((t for k, t in clean if k == 'h1'), None)
        page.title = ' '.join(w for w, _ in h) if h else base
    return page


def is_loopback(host):
    host = (host or '').lower()
    return host in ('localhost', '::1') or host.startswith('127.') or host.startswith('169.254.') or host.endswith('.localhost')


def http_get(url, timeout=20, max_bytes=MAX_BYTES, accept='text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5'):
    u = urllib.parse.urlparse(url)
    if u.scheme not in ('http', 'https') or not u.hostname:
        raise ValueError('só http e https')
    if is_loopback(u.hostname):
        raise ValueError('endereço local bloqueado')
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': accept, 'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.5'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(max_bytes)
        ctype = r.headers.get('Content-Type', '')
        cs = r.headers.get_content_charset()
        final = r.geturl()
    if not cs:
        m = re.search(rb'(?i)<meta[^>]+charset=["\']?([\w-]+)', raw[:4096])
        cs = m.group(1).decode('ascii', 'ignore') if m else 'utf-8'
    try:
        text = raw.decode(cs, 'replace')
    except LookupError:
        text = raw.decode('utf-8', 'replace')
    return final, ctype, text


def fetch(url):
    """-> Page (ou levanta ValueError/OSError com a mensagem do problema)."""
    try:
        final, ctype, text = http_get(url)
    except urllib.error.HTTPError as e:
        raise ValueError('o site respondeu HTTP %d' % e.code)
    except urllib.error.URLError as e:
        raise ValueError('não consegui abrir: %s' % getattr(e, 'reason', e))
    except (TimeoutError, OSError) as e:
        raise ValueError('não consegui abrir: %s' % e)
    if 'html' in ctype or text.lstrip()[:200].lower().startswith(('<!doctype', '<html')) or '<body' in text[:5000].lower():
        page = parse_html(text, final)
    else:
        page = Page(final, final)
        for para in re.split(r'\n\s*\n', text[:60000]):
            para = para.strip()
            if para:
                page.blocks.append(('p', [(w, None) for w in para.split()]))
    page.url = final
    if not page.blocks:
        page.blocks.append(('p', [(w, None) for w in 'Esta página não tem texto legível: provavelmente depende de JavaScript.'.split()]))
    return page


def looks_like_url(s):
    s = s.strip()
    if ' ' in s:
        return False
    if re.match(r'^https?://', s, re.I):
        return True
    return bool(re.match(r'^[\w-]+(\.[\w-]+)+(:\d+)?(/\S*)?$', s)) and not re.match(r'^\d+\.\d+$', s)


def normalize(s):
    s = s.strip()
    return s if re.match(r'^https?://', s, re.I) else 'https://' + s


# ------------------------------------------------------------------ busca
def search(query):
    """DuckDuckGo (versão HTML, sem JavaScript). -> Page com os resultados; levanta ValueError se não vierem resultados."""
    url = 'https://html.duckduckgo.com/html/?' + urllib.parse.urlencode({'q': query, 'kl': 'br-pt'})
    _, _, raw = http_get(url, timeout=15)
    page = Page(url, 'Busca: ' + query)
    page.blocks.append(('h1', [(w, None) for w in ('Resultados para "%s"' % query).split()]))
    for m in re.finditer(r'(?s)<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)', raw):
        href, title, rest = html.unescape(m.group(1)), re.sub(r'<[^>]+>', '', m.group(2)), m.group(3)
        if href.startswith('//'):
            href = 'https:' + href
        q = urllib.parse.urlparse(href)
        if q.path.startswith('/l/'):
            href = urllib.parse.parse_qs(q.query).get('uddg', [''])[0]
        if not href.startswith(('http://', 'https://')):
            continue
        if 'duckduckgo.com/y.js' in href or q.netloc.endswith('duckduckgo.com') and q.path.startswith('/y.js'):
            continue                                              # anúncio
        sn = re.search(r'(?s)class="result__snippet"[^>]*>(.*?)</a>', rest)
        title = re.sub(r'\s+', ' ', html.unescape(title)).strip()
        page.blocks.append(('h2', [(w, href) for w in title.split()]))
        host = urllib.parse.urlparse(href).netloc
        page.blocks.append(('q', [(host, None)]))
        if sn:
            snippet = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', sn.group(1)))).strip()
            page.blocks.append(('p', [(w, None) for w in snippet.split()]))
        if len(page.blocks) > 60:
            break
    if len(page.blocks) <= 1:
        raise ValueError('a busca não devolveu resultados')
    return page


def page_from_summary(title, summary, sources):
    """Página a partir do resumo com fontes da pesquisa do Gemini (reserva quando o DuckDuckGo recusa)."""
    p = Page('', title)
    p.blocks.append(('h1', [(w, None) for w in title.split()]))
    for para in re.split(r'\n+', summary.strip()):
        if para.strip():
            p.blocks.append(('p', [(w, None) for w in para.split()]))
    if sources:
        p.blocks.append(('h2', [('Fontes', None)]))
        for s in sources:
            p.blocks.append(('li', [(w, s['url']) for w in (s.get('titulo') or s['url']).split()]))
    return p


# ------------------------------------------------------------------ RSS / Atom
def rss(url, n=12):
    """-> lista de {titulo, link, resumo, data}."""
    _, _, raw = http_get(url, timeout=15, accept='application/rss+xml,application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.5')
    items = []

    def tag(block, name):
        m = re.search(r'(?is)<%s\b[^>]*>(.*?)</%s>' % (name, name), block)
        if not m:
            return ''
        v = m.group(1)
        v = re.sub(r'(?s)<!\[CDATA\[(.*?)\]\]>', r'\1', v)
        v = re.sub(r'(?s)<[^>]+>', ' ', html.unescape(v))
        return re.sub(r'\s+', ' ', html.unescape(v)).strip()
    for blk in re.findall(r'(?is)<(?:item|entry)\b.*?</(?:item|entry)>', raw):
        title = tag(blk, 'title')
        link = tag(blk, 'link')
        if not link:
            m = re.search(r'(?i)<link[^>]+href="([^"]+)"', blk)
            link = html.unescape(m.group(1)) if m else ''
        if not title:
            continue
        items.append({'titulo': title, 'link': link, 'resumo': (tag(blk, 'description') or tag(blk, 'summary'))[:280],
                      'data': tag(blk, 'pubDate') or tag(blk, 'updated') or tag(blk, 'published')})
        if len(items) >= n:
            break
    if not items:
        raise ValueError('o feed não trouxe itens')
    return items


if __name__ == '__main__':
    arg = ' '.join(sys.argv[1:])
    if arg.startswith('rss:'):
        print(json.dumps(rss(arg[4:]), ensure_ascii=False, indent=1))
    else:
        pg = fetch(normalize(arg)) if looks_like_url(arg) else search(arg)
        print(pg.title, '|', pg.url, '|', pg.words(), 'palavras')
        for kind, toks in pg.blocks[:25]:
            print('[%s] %s' % (kind, ' '.join(w if not h else '<%s>' % w for w, h in toks)[:160]))
