#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The TarsOS Authors
"""TarsOS: ferramentas do TARS (function calling do Gemini).

Rodam dentro da área de trabalho (como root), mas os COMANDOS DE SHELL que o modelo pede rodam como o usuário
comum `user`, passam por uma política de segurança em três níveis e, fora da lista de leitura, exigem
CONFIRMAÇÃO NA TELA:

  auto     comandos só de leitura (ls, df, free, ip, ping -c, nmcli show...), sem redirecionamento/substituição
  confirm  qualquer outra coisa (instalar, baixar, escrever, apagar...): o dono toca SIM/NÃO na tela
  block    escalonamento de privilégio, discos/partições (lk, preloader, tee...), segredos, `| sh`, reboot etc.

Tudo que vem de fora (páginas web, saída de comandos, nomes de dispositivos) é DADO NÃO CONFIÁVEL: o prompt do
TARS manda ignorar instruções que apareçam nesses resultados, e nenhuma ação de risco roda sem o toque do dono.
"""
import hashlib
import html
import ipaddress
import json
import os
import pwd
import re
import shlex
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

VAR = '/var/lib/luizinhoshowos'
RUN = '/run/luizinhoshowos'
USER = 'user'
HOME = '/home/user'
DOWNLOADS = HOME + '/Downloads'
NOTES = HOME + '/notas.txt'
TIMERS_FILE = VAR + '/timers.json'
DEVICES_FILE = VAR + '/devices.json'
LOCATION_FILE = VAR + '/location.json'
KEY_FILE = '/etc/luizinhoshowos/gemini.json'
HA_FILE = '/etc/luizinhoshowos/homeassistant.json'
GEMINI = 'https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent'
MAX_OUT = 3500


def log(*a):
    print(time.strftime('%H:%M:%S'), '[tools]', *a, flush=True)


def clip(text, n=MAX_OUT):
    text = text.strip()
    return text if len(text) <= n else text[:n // 2] + '\n[...cortado...]\n' + text[-n // 2:]


# ======================================================================= segurança de comandos
BLOCK = [
    (r'\b(sudo|su|doas|pkexec)\b', 'escalonamento de privilégio não é permitido'),
    (r'/dev/(mmcblk|block|mem|kmem|sd[a-z]|loop|mtd)|by-name|/proc/sysrq', 'acesso a discos e partições é proibido'),
    (r'\b(mkfs|fdisk|sfdisk|parted|wipefs|blkdiscard|losetup|mount\s+-o|umount|swapoff|insmod|rmmod|modprobe)\b', 'operação de disco/módulo proibida'),
    (r'\bdd\b.*\bof=', 'dd com saída em arquivo/disco é proibido'),
    (r'\b(reboot|poweroff|shutdown|halt|init\s+[06])\b', 'reiniciar/desligar só pelo app Ajustes'),
    (r'\brm\b[^|;&]*-\w*[rf]\w*[^|;&]*(\s/(\s|$|\*)|\s/(etc|usr|var|boot|bin|sbin|lib|home|root|opt)\b|\s~/?\s*$|\s\$HOME|\s\*\s*$)', 'rm destrutivo bloqueado'),
    (r':\(\)\s*\{', 'fork bomb'),
    (r'\|\s*(sh|bash|ash|zsh|dash)\b|\bbase64\b[^|]*\|\s*(sh|bash)|(sh|bash|ash)\s+-c\s+["\']?\$\(\s*(curl|wget)', 'executar código baixado direto de um pipe é proibido'),
    (r'\b(nc|ncat|netcat|socat)\b[^|;&]*(-e\b|exec)', 'shell reverso proibido'),
    (r'/etc/luizinhoshowos|\.claude|\.gemini|\.ssh|/etc/shadow|gemini\.json|spotify-api\.json|credentials|authorized_keys|id_ed25519', 'segredos e chaves são intocáveis'),
    (r'chmod\s+(-R\s+)?[0-7]*7[0-7]*\s+/(\s|$)|chown\s+-R\s+\S+\s+/(\s|$)', 'permissões em / proibidas'),
]
SAFE = {'ls', 'cat', 'head', 'tail', 'df', 'du', 'free', 'uptime', 'uname', 'whoami', 'id', 'date', 'ps', 'top', 'ip',
        'ifconfig', 'ping', 'nslookup', 'dig', 'host', 'traceroute', 'tracepath', 'nmcli', 'iw', 'iwconfig', 'lsblk',
        'grep', 'egrep', 'fgrep', 'wc', 'sort', 'uniq', 'echo', 'which', 'pwd', 'hostname', 'cut', 'tr', 'stat',
        'file', 'lsusb', 'nproc', 'arp', 'netstat', 'ss', 'route', 'getent', 'basename', 'dirname', 'find',
        'apk', 'curl', 'wget', 'true', 'printf', 'seq', 'tee', 'ntpq', 'chronyc', 'amixer', 'rc-status',
        'rc-service', 'cat', 'readlink', 'realpath', 'paste', 'rev', 'nl', 'diff', 'cmp'}
# Fora da lista de propósito (executam ou escrevem sozinhos): awk (system/pipes), sed (e/w), xargs, less/more (shell),
# aplay/arecord (escrita direta no PCM derruba o kernel da MediaTek; arecord grava arquivo).
SAFE_ARGS = {                               # (subcomandos permitidos) para programas que também escrevem
    'apk': {'info', 'search', 'list', 'policy', 'version', 'stats'},
    'rc-service': {'status'},
    'rc-status': None, 'chronyc': {'tracking', 'sources', 'sourcestats'},
    'nmcli': {'device', 'connection', 'general', 'radio', 'dev', 'con', 'd', 'c', 'g', 'r', 'monitor'},
    'amixer': {'sget', 'sgets', 'get', 'cget', 'controls', 'scontrols', 'scontents'},
    'ip': {'addr', 'address', 'a', 'route', 'r', 'link', 'l', 'neigh', 'n', 'neighbor', '-4', '-6', '-br', '-s', '-o'},
}
UNSAFE_WORDS = {'add', 'del', 'delete', 'set', 'flush', 'change', 'replace', 'up', 'down', 'connect', 'modify', 'edit',
                'up', 'wifi-connect', 'reload', 'start', 'stop', 'restart', 'add-apt'}


def classify(cmd):
    """-> ('block'|'confirm'|'auto', motivo)"""
    for pat, why in BLOCK:
        if re.search(pat, cmd, re.I):
            return 'block', why
    # Tira só os destinos inofensivos (>/dev/null, 2>&1); qualquer outro > ou <, substituição, segundo plano (& sozinho),
    # várias linhas ou nohup pode esconder um segundo comando ou escrever arquivo: confirma.
    plain = re.sub(r'\d*>>?\s*/dev/null|\d*>&\d+|\d*<\s*/dev/null', ' ', cmd)
    if re.search(r'[`<>\n\r]|\$\(|\$\{|\$\[|(?<!&)&(?!&)|\bnohup\b', plain):
        return 'confirm', 'usa redirecionamento, substituição, segundo plano ou várias linhas'
    segs = [s for s in re.split(r'\|\||&&|;|\|', cmd) if s.strip()]
    if not segs:
        return 'confirm', 'comando vazio'
    for seg in segs:
        try:
            parts = shlex.split(seg)
        except ValueError:
            return 'confirm', 'sintaxe incomum'
        while parts and re.match(r'^\w+=', parts[0]):
            parts.pop(0)
        if not parts:
            continue
        prog = os.path.basename(parts[0])
        if prog not in SAFE:
            return 'confirm', 'programa fora da lista de leitura: ' + prog
        args = parts[1:]
        if prog in ('curl', 'wget'):
            if any(a.startswith(('-d', '--data', '-X', '-T', '--upload', '-F', '--form', '-o', '-O', '--output', '-P')) for a in args) \
                    or prog == 'wget':
                return 'confirm', 'transferência de rede com escrita/envio'
            return 'confirm', 'acesso de rede por comando (prefira fetch_url)'
        if prog == 'sed' and any(a.startswith('-i') or a == '--in-place' for a in args):
            return 'confirm', 'sed -i altera arquivos'
        if prog == 'find' and any(a in ('-exec', '-execdir', '-delete', '-ok', '-fprint') for a in args):
            return 'confirm', 'find com ação'
        if prog == 'tee':
            return 'confirm', 'tee escreve em arquivo'
        if prog == 'sort' and any(a == '--output' or a.startswith('--output=') or re.match(r'^-[A-Za-z]*o', a) for a in args):
            return 'confirm', 'sort -o escreve em arquivo'
        if prog == 'uniq' and len([a for a in args if not a.startswith('-')]) >= 2:
            return 'confirm', 'uniq com arquivo de saída escreve em arquivo'
        if prog == 'top' and '-b' not in args:
            return 'confirm', 'top interativo'
        if prog == 'ping' and '-c' not in args:
            return 'confirm', 'ping sem -c não termina'
        if prog in SAFE_ARGS and SAFE_ARGS[prog] is not None:
            first = next((a for a in args if not a.startswith('-')), None)
            if prog == 'ip':
                if any(a in UNSAFE_WORDS for a in args):
                    return 'confirm', 'ip com ação que altera a rede'
            elif first is not None and first not in SAFE_ARGS[prog]:
                return 'confirm', '%s %s altera o sistema' % (prog, first)
            elif first is None and prog not in ('chronyc', 'rc-status'):
                pass
        if prog == 'nmcli' and any(a in ('up', 'down', 'connect', 'delete', 'modify', 'add', 'reload') for a in args):
            return 'confirm', 'nmcli altera conexões'
        if prog in ('cat', 'head', 'tail', 'less', 'more', 'grep', 'egrep', 'fgrep', 'ls', 'stat', 'file', 'find') and \
                any(re.search(r'(^|/)\.|/root|/etc/(ssh|shadow|luiz)|/proc/\d+/environ', a) for a in args):
            return 'confirm', 'toca em arquivo oculto ou sensível'
    return 'auto', 'somente leitura'


def drop_privileges():
    pw = pwd.getpwnam(USER)

    def _drop():
        os.setsid()
        os.initgroups(USER, pw.pw_gid)
        os.setgid(pw.pw_gid)
        os.setuid(pw.pw_uid)
    return pw, _drop


def run_shell(cmd, timeout=30):
    pw, drop = drop_privileges()
    env = {'HOME': pw.pw_dir, 'USER': USER, 'LOGNAME': USER, 'LANG': 'C.UTF-8', 'TERM': 'dumb',
           'PATH': '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'}
    p = subprocess.Popen(['/bin/bash', '-c', cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         cwd=pw.pw_dir, env=env, preexec_fn=drop)
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, clip(out or '')
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            p.kill()
        out, _ = p.communicate()
        return -9, clip((out or '') + '\n[comando interrompido após %d s]' % timeout)


# ======================================================================= rede
def lan_info():
    out = subprocess.run(['ip', '-4', '-o', 'addr', 'show', 'wlan0'], capture_output=True, text=True).stdout
    m = re.search(r'inet (\d+\.\d+\.\d+\.\d+)/(\d+)', out)
    if not m:
        return None, None
    ip, prefix = m.group(1), int(m.group(2))
    net = ipaddress.ip_network('%s/%d' % (ip, max(prefix, 24)), strict=False)
    return ip, net


def is_private(host):
    try:
        for res in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(res[4][0])
            if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
                return 'bloqueado'
            if ip.is_private:
                return 'privado'
        return 'publico'
    except (socket.gaierror, ValueError):
        return 'publico'


def oui_vendor(mac):
    """Fabricante pelo prefixo do MAC (base do nmap). MAC aleatório (bit local) = celular com privacidade."""
    first = int(mac.split(':')[0], 16)
    if first & 2:
        return 'MAC privado (celular/PC com privacidade)'
    key = mac.replace(':', '').upper()[:6]
    try:
        with open('/usr/share/nmap/nmap-mac-prefixes', errors='replace') as f:
            for line in f:
                if line.startswith(key):
                    return line[7:].strip()
    except OSError:
        pass
    return 'desconhecido'


PORTS = {22: 'ssh', 23: 'telnet', 53: 'dns', 80: 'http', 443: 'https', 554: 'rtsp(câmera)', 1400: 'sonos', 1883: 'mqtt',
         5000: 'upnp/synology', 6668: 'tuya', 7000: 'airplay', 8008: 'cast', 8009: 'cast', 8060: 'roku', 8123: 'homeassistant',
         8443: 'https-alt', 8888: 'http-alt', 9000: 'media', 9100: 'impressora', 55443: 'yeelight', 49152: 'upnp'}


def probe_ports(ip, timeout=0.4):
    found = []
    for port in PORTS:
        s = socket.socket()
        s.settimeout(timeout)
        try:
            s.connect((ip, port))
            found.append(port)
        except OSError:
            pass
        finally:
            s.close()
    return found


def guess_type(vendor, services, ports, name):
    v = (vendor or '').lower()
    sv = ' '.join(services).lower()
    n = (name or '').lower()
    # o nome que o aparelho anuncia na rede costuma dizer mais que o fabricante do chip de WiFi
    if re.search(r'webos|lgtv|bravia|tizen|roku|firetv|fire-tv|androidtv|appletv|shield', n) or re.search(r'(^|[-_ ])tv($|[-_ ])', n):
        return 'TV/streaming'
    if re.search(r'^(mr|ax|ac|archer|deco|tl-|mercusys)\w*\d', n) or 'mercury' in v or 'mercusys' in v or 'mitrastar' in v:
        return 'Roteador/AP'
    if re.search(r'iphone|ipad|galaxy|moto-|motorola|pixel|redmi|xiaomi-|oneplus|android-', n):
        return 'Celular/tablet'
    if re.search(r'macbook|^mac$|imac|notebook|laptop|desktop-|pc-', n):
        return 'Computador'
    if re.search(r'echo|alexa|kindle|fire', n) and 'amazon' in v:
        return 'Amazon Echo/Alexa/Fire'
    if '_googlecast' in sv or 8009 in ports or 'chromecast' in n or 'nest' in n or 'google' in v:
        return 'Google Cast/Nest/Chromecast'
    if '_hap' in sv or 'homekit' in sv:
        return 'HomeKit (Apple Home)'
    if '_airplay' in sv or '_raop' in sv or 'apple' in v:
        return 'Apple/AirPlay'
    if 'amazon' in v or 'echo' in n or 'alexa' in n:
        return 'Amazon Echo/Alexa/Fire'
    if 'philips' in v or 'signify' in v or 'hue' in n:
        return 'Philips Hue (lâmpadas)'
    if 'wiz' in v or 'wiz' in n:
        return 'Lâmpada WiZ'
    if 'espressif' in v or 6668 in ports or 'tuya' in v or 'smartlife' in n:
        return 'IoT Tuya/ESP (lâmpada, tomada, sensor)'
    if 'tp-link' in v or 'tp link' in v or 'kasa' in n:
        return 'TP-Link (roteador/Kasa)'
    if 'xiaomi' in v or 'yeelight' in n or 55443 in ports:
        return 'Xiaomi/Yeelight'
    if 'shelly' in n or 'allterco' in v:
        return 'Shelly (relé)'
    if 'sonos' in v or 1400 in ports:
        return 'Sonos'
    if 'samsung' in v or 'lg ' in v or 'sony' in v or 'roku' in v or 8060 in ports or '_mediaremote' in sv:
        return 'TV/streaming'
    if 'hp' in v or 'canon' in v or 'epson' in v or 'brother' in v or 9100 in ports or '_ipp' in sv or '_printer' in sv:
        return 'Impressora'
    if 554 in ports or 'hikvision' in v or 'intelbras' in v or 'dahua' in v:
        return 'Câmera IP'
    if 8123 in ports:
        return 'Home Assistant'
    if 'raspberry' in v:
        return 'Raspberry Pi'
    if v.startswith('mac privado'):
        return 'Celular/notebook'
    if 'intelbras' in v or 'tp-link' in v or 'zte' in v or 'huawei' in v or 'mikrotik' in v or 'ubiquiti' in v or 53 in ports:
        return 'Roteador/AP'
    return 'Desconhecido'


def mdns_services():
    """ip -> [tipos de serviço]  via avahi-browse (o avahi-daemon já roda no Echo)."""
    res = {}
    try:
        out = subprocess.run(['avahi-browse', '-a', '-r', '-t', '-p'], capture_output=True, text=True, timeout=12).stdout
    except (OSError, subprocess.SubprocessError):
        return res
    for line in out.splitlines():
        p = line.split(';')
        if p and p[0] == '=' and len(p) >= 9 and p[2] == 'IPv4':
            res.setdefault(p[7], []).append('%s:%s' % (p[4], p[3]))
    return res


def ssdp_devices(wait=2.5):
    res = {}
    msg = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\nMX: 2\r\nST: ssdp:all\r\n\r\n').encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.5)
    try:
        s.sendto(msg, ('239.255.255.250', 1900))
        t0 = time.time()
        while time.time() - t0 < wait:
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                continue
            txt = data.decode('utf-8', 'replace')
            srv = re.search(r'^SERVER:\s*(.+)$', txt, re.M | re.I)
            st = re.search(r'^ST:\s*(.+)$', txt, re.M | re.I)
            res.setdefault(addr[0], set()).add(((srv.group(1) if srv else '') + ' ' + (st.group(1) if st else '')).strip()[:90])
    except OSError:
        pass
    finally:
        s.close()
    return {k: sorted(v) for k, v in res.items()}


def scan_lan():
    """Varre a rede local e identifica os aparelhos (fabricante, serviços, portas, tipo provável)."""
    me, net = lan_info()
    if not net:
        return {'erro': 'sem rede WiFi'}
    # 0) escuta os avisos de rede dos aparelhos Tuya (UDP 6666/6667): dá ID e versão sem precisar de chave
    tuya = {}

    def listen_tuya():
        try:
            import tinytuya
            for ip, d in tinytuya.deviceScan(False, 8).items():
                tuya[ip] = {'id': d.get('gwId'), 'versao': d.get('version'), 'produto': d.get('productKey')}
        except Exception as e:
            log('tuya scan:', e)
    tuya_thread = threading.Thread(target=listen_tuya, daemon=True)
    tuya_thread.start()
    # 1) acorda o ARP de todos os IPs com um datagrama UDP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for host in net.hosts():
        for port in (9, 5353):
            try:
                s.sendto(b'\0', (str(host), port))
            except OSError:
                pass
    s.close()
    ssdp = ssdp_devices(2.5)
    time.sleep(0.5)
    arp = {}
    try:
        for line in open('/proc/net/arp').read().splitlines()[1:]:
            f = line.split()
            if len(f) >= 4 and f[2] == '0x2' and f[3] != '00:00:00:00:00:00':
                arp[f[0]] = f[3]
    except OSError:
        pass
    mdns = mdns_services()
    tuya_thread.join(12)
    hosts = sorted(set(arp) | set(ssdp) | set(mdns) | set(tuya) | {me}, key=lambda x: tuple(int(i) for i in x.split('.')))
    hosts = [h for h in hosts if ipaddress.ip_address(h) in net]
    # 2) portas em paralelo
    ports = {}
    lock = threading.Lock()

    def work(h):
        r = probe_ports(h)
        with lock:
            ports[h] = r
    threads = [threading.Thread(target=work, args=(h,)) for h in hosts]
    for i in range(0, len(threads), 24):
        batch = threads[i:i + 24]
        [t.start() for t in batch]
        [t.join(15) for t in batch]
    devices = []
    for h in hosts[:60]:
        mac = arp.get(h, '')
        if not mac and h == me:
            try:
                mac = open('/sys/class/net/wlan0/address').read().strip()
            except OSError:
                mac = ''
        vendor = oui_vendor(mac) if mac else 'desconhecido'
        try:
            name = socket.gethostbyaddr(h)[0]
        except (OSError, socket.herror):
            name = ''
        svc = mdns.get(h, [])
        mname = next((x.split(':', 1)[1] for x in svc if ':' in x), '')
        d = {'ip': h, 'mac': mac, 'fabricante': vendor, 'nome': name or mname,
             'servicos': sorted(set(x.split(':')[0] for x in svc))[:6] + [x[:60] for x in ssdp.get(h, [])][:2],
             'portas': [PORTS[p] for p in ports.get(h, [])]}
        d['tipo'] = guess_type(vendor, d['servicos'], ports.get(h, []), d['nome'])
        if h in tuya:
            d['tuya'] = tuya[h]
            d['tipo'] = 'IoT Tuya (tomada/interruptor/lâmpada)'
            d['portas'] = sorted(set(d['portas']) | {'tuya'})
        if h == me:
            d['tipo'] = 'ESTE APARELHO (TarsOS)'
        devices.append(d)
    result = {'rede': str(net), 'quando': time.strftime('%d/%m/%Y %H:%M'), 'total': len(devices), 'aparelhos': devices}
    try:
        os.makedirs(VAR, exist_ok=True)
        json.dump(result, open(DEVICES_FILE, 'w'), ensure_ascii=False)
    except OSError:
        pass
    return result


# ======================================================================= web
def gemini_key():
    try:
        return json.load(open(KEY_FILE)).get('api_key', '')
    except (OSError, ValueError):
        return ''


def web_search(query):
    key = gemini_key()
    if not key:
        return {'erro': 'sem chave do Gemini'}
    body = {'contents': [{'parts': [{'text': query + '\n\nResponda em português, de forma objetiva, em até 5 frases.'}]}],
            'tools': [{'google_search': {}}],
            'generationConfig': {'maxOutputTokens': 700, 'temperature': 0.2, 'thinkingConfig': {'thinkingBudget': 0}}}
    last = ''
    for model in ('gemini-3.5-flash', 'gemini-3.1-flash-lite', 'gemini-3.6-flash'):
        for _ in range(2):
            try:
                req = urllib.request.Request(GEMINI % model, data=json.dumps(body).encode(),
                                             headers={'x-goog-api-key': key, 'Content-Type': 'application/json'})
                r = json.load(urllib.request.urlopen(req, timeout=40))
                cand = r['candidates'][0]
                text = ''.join(p.get('text', '') for p in cand['content'].get('parts', [])).strip()
                srcs = []
                for ch in cand.get('groundingMetadata', {}).get('groundingChunks', [])[:5]:
                    w = ch.get('web', {})
                    if w.get('uri'):
                        srcs.append({'titulo': w.get('title', ''), 'url': w['uri']})
                return {'resumo': text, 'fontes': srcs}
            except urllib.error.HTTPError as e:
                last = 'HTTP %d' % e.code
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(1)
                    continue
                if e.code == 400 and 'thinkingConfig' in body['generationConfig']:
                    del body['generationConfig']['thinkingConfig']
                    continue
                break
            except (KeyError, IndexError, TimeoutError, OSError, ValueError) as e:
                last = type(e).__name__
                break
    return {'erro': 'pesquisa indisponível (%s)' % last}


def html_to_text(raw):
    raw = re.sub(r'(?is)<(script|style|noscript|svg|head|nav|footer|form)\b.*?</\1>', ' ', raw)
    raw = re.sub(r'(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>|</tr>', '\n', raw)
    raw = re.sub(r'(?s)<[^>]+>', ' ', raw)
    raw = html.unescape(raw)
    raw = re.sub(r'[ \t\r\f\v]+', ' ', raw)
    raw = re.sub(r'\n\s*\n+', '\n', raw)
    return raw.strip()


def fetch_url(url, confirm):
    u = urllib.parse.urlparse(url)
    if u.scheme not in ('http', 'https') or not u.hostname:
        return {'erro': 'só http e https'}
    kind = is_private(u.hostname)
    if kind == 'bloqueado':
        return {'erro': 'endereço local/loopback bloqueado'}
    if kind == 'privado' and not confirm('Abrir a página da sua rede local?\n' + url):
        return {'erro': 'o dono não autorizou'}
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 TarsOS/1.0', 'Accept-Language': 'pt-BR,pt;q=0.9'})
        with urllib.request.urlopen(req, timeout=20) as r:
            ctype = r.headers.get('Content-Type', '')
            raw = r.read(600000).decode(r.headers.get_content_charset() or 'utf-8', 'replace')
        text = html_to_text(raw) if 'html' in ctype or raw.lstrip().startswith('<') else raw
        return {'url': url, 'texto': clip(text, 5000)}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {'erro': 'falha ao abrir: %s' % e}


def download_file(url, name, confirm):
    u = urllib.parse.urlparse(url)
    if u.scheme not in ('http', 'https') or not u.hostname or is_private(u.hostname) == 'bloqueado':
        return {'erro': 'URL inválida ou bloqueada'}
    name = re.sub(r'[^\w.\-]', '_', name or os.path.basename(u.path) or 'arquivo')[:80] or 'arquivo'
    if not confirm('Baixar arquivo?\n%s\npara %s/%s' % (url, DOWNLOADS, name)):
        return {'erro': 'o dono não autorizou'}
    pw = pwd.getpwnam(USER)
    os.makedirs(DOWNLOADS, exist_ok=True)
    os.chown(DOWNLOADS, pw.pw_uid, pw.pw_gid)
    path = os.path.join(DOWNLOADS, name)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'TarsOS/1.0'})
        h, size = hashlib.sha256(), 0
        with urllib.request.urlopen(req, timeout=30) as r, open(path, 'wb') as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > 150 * 1024 * 1024:
                    raise OSError('maior que 150 MB')
                h.update(chunk)
                f.write(chunk)
        os.chown(path, pw.pw_uid, pw.pw_gid)
        return {'arquivo': path, 'bytes': size, 'sha256': h.hexdigest()}
    except (urllib.error.URLError, OSError) as e:
        try:
            os.remove(path)
        except OSError:
            pass
        return {'erro': 'download falhou: %s' % e}


def system_status():
    out = {}
    try:
        out['uptime_min'] = int(float(open('/proc/uptime').read().split()[0]) // 60)
        out['carga'] = open('/proc/loadavg').read().split()[:3]
        mem = {l.split(':')[0]: int(l.split()[1]) for l in open('/proc/meminfo')}
        out['memoria_mb'] = {'total': mem['MemTotal'] // 1024, 'livre': mem['MemAvailable'] // 1024}
        st = os.statvfs('/')
        out['disco_gb'] = {'total': round(st.f_blocks * st.f_frsize / 1e9, 1), 'livre': round(st.f_bavail * st.f_frsize / 1e9, 1)}
        temps = []
        for z in range(12):
            try:
                t = int(open('/sys/class/thermal/thermal_zone%d/temp' % z).read()) / 1000
                if 5 < t < 120:
                    temps.append(t)
            except (OSError, ValueError):
                pass
        if temps:
            out['temperatura_c'] = round(max(temps), 1)
        ip, net = lan_info()
        out['ip'] = ip
        w = subprocess.run(['nmcli', '-t', '-f', 'ACTIVE,SSID,SIGNAL', 'dev', 'wifi'], capture_output=True, text=True, timeout=6).stdout
        for line in w.splitlines():
            if line.startswith('yes:'):
                _, ssid, sig = line.split(':')[:3]
                out['wifi'] = {'rede': ssid, 'sinal_pct': int(sig)}
                break
        out['kernel'] = os.uname().release
        out['sistema'] = 'TarsOS (postmarketOS/Alpine), Echo Show 5 1a geracao'
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        out['erro'] = str(e)
    return out


# ======================================================================= clima
WMO = {0: 'céu limpo', 1: 'predominantemente limpo', 2: 'parcialmente nublado', 3: 'nublado', 45: 'neblina', 48: 'neblina com geada',
       51: 'garoa fraca', 53: 'garoa', 55: 'garoa forte', 61: 'chuva fraca', 63: 'chuva', 65: 'chuva forte', 66: 'chuva congelante',
       71: 'neve fraca', 73: 'neve', 75: 'neve forte', 80: 'pancadas de chuva fracas', 81: 'pancadas de chuva', 82: 'pancadas fortes',
       95: 'trovoada', 96: 'trovoada com granizo', 99: 'trovoada com granizo forte'}


def http_json(url, timeout=15):
    req = urllib.request.Request(url, headers={'User-Agent': 'TarsOS/1.0'})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def my_location():
    try:
        return json.load(open(LOCATION_FILE))
    except (OSError, ValueError):
        pass
    try:
        j = http_json('http://ip-api.com/json/?fields=status,city,regionName,lat,lon,timezone&lang=pt-BR')
        if j.get('status') == 'success':
            loc = {'cidade': j['city'], 'lat': j['lat'], 'lon': j['lon']}
            os.makedirs(VAR, exist_ok=True)
            json.dump(loc, open(LOCATION_FILE, 'w'), ensure_ascii=False)
            return loc
    except (urllib.error.URLError, OSError, ValueError):
        pass
    return {'cidade': 'São Paulo', 'lat': -23.55, 'lon': -46.63}


def weather(city=None):
    try:
        if city:
            g = http_json('https://geocoding-api.open-meteo.com/v1/search?count=1&language=pt&name=' + urllib.parse.quote(city))
            r0 = g['results'][0]
            loc = {'cidade': r0['name'], 'lat': r0['latitude'], 'lon': r0['longitude']}
        else:
            loc = my_location()
        j = http_json('https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&timezone=auto&forecast_days=3'
                      '&current=temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m'
                      '&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max' % (loc['lat'], loc['lon']))
        c, dly = j['current'], j['daily']
        out = {'cidade': loc['cidade'], 'agora': {'temperatura': c['temperature_2m'], 'sensacao': c['apparent_temperature'],
               'umidade': c['relative_humidity_2m'], 'vento_kmh': c['wind_speed_10m'], 'chuva_mm': c['precipitation'],
               'condicao': WMO.get(c['weather_code'], 'indefinido'), 'codigo': c['weather_code']}, 'proximos_dias': []}
        for i in range(len(dly['time'])):
            out['proximos_dias'].append({'data': dly['time'][i], 'min': dly['temperature_2m_min'][i], 'max': dly['temperature_2m_max'][i],
                                         'chance_chuva_pct': dly['precipitation_probability_max'][i],
                                         'condicao': WMO.get(dly['weather_code'][i], 'indefinido')})
        if not city:
            try:
                json.dump(out, open(VAR + '/weather.json', 'w'), ensure_ascii=False)
            except OSError:
                pass
        return out
    except (urllib.error.URLError, OSError, KeyError, IndexError, ValueError) as e:
        return {'erro': 'clima indisponível: %s' % e}


# ======================================================================= timers e alarmes
_tlock = threading.Lock()


def _load_timers():
    try:
        return json.load(open(TIMERS_FILE))
    except (OSError, ValueError):
        return []


def _save_timers(items):
    try:
        os.makedirs(VAR, exist_ok=True)
        json.dump(items, open(TIMERS_FILE, 'w'), ensure_ascii=False)
    except OSError:
        pass


def add_timer(seconds, label):
    seconds = max(1, min(int(seconds), 24 * 3600))
    with _tlock:
        items = _load_timers()
        item = {'id': int(time.time() * 1000) % 10 ** 9, 'kind': 'timer', 'at': time.time() + seconds, 'label': label or 'Timer'}
        items.append(item)
        _save_timers(items)
    return {'ok': True, 'id': item['id'], 'toca_em': time.strftime('%H:%M:%S', time.localtime(item['at']))}


def add_alarm(hhmm, label):
    m = re.match(r'^\s*(\d{1,2})[:h](\d{2})?\s*$', hhmm or '')
    if not m:
        return {'erro': 'hora inválida, use HH:MM'}
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if h > 23 or mi > 59:
        return {'erro': 'hora inválida'}
    now = time.time()
    t = time.localtime(now)
    at = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, mi, 0, 0, 0, -1))
    if at <= now:
        at += 86400
    with _tlock:
        items = _load_timers()
        item = {'id': int(now * 1000) % 10 ** 9, 'kind': 'alarm', 'at': at, 'label': label or 'Alarme'}
        items.append(item)
        _save_timers(items)
    return {'ok': True, 'id': item['id'], 'toca_em': time.strftime('%d/%m %H:%M', time.localtime(at))}


def list_timers():
    with _tlock:
        items = sorted(_load_timers(), key=lambda i: i['at'])
    return [{'id': i['id'], 'tipo': i['kind'], 'rotulo': i['label'], 'falta_s': max(0, int(i['at'] - time.time())),
             'toca_em': time.strftime('%d/%m %H:%M:%S', time.localtime(i['at']))} for i in items]


def cancel_timer(tid=None):
    with _tlock:
        items = _load_timers()
        keep = [i for i in items if tid is not None and i['id'] != tid]
        _save_timers(keep)
    return {'ok': True, 'cancelados': len(items) - len(keep)}


def due_timers():
    """Remove e devolve os timers/alarmes vencidos (chamado pela área de trabalho a cada segundo)."""
    now = time.time()
    with _tlock:
        items = _load_timers()
        due = [i for i in items if i['at'] <= now]
        if due:
            _save_timers([i for i in items if i['at'] > now])
    return due


# ======================================================================= rádio
RADIO_PID = RUN + '/radio.pid'
radio_now = {'nome': '', 'url': ''}


def radio_search(name):
    q = urllib.parse.quote(name)
    for cc in ('BR', ''):
        url = 'https://de1.api.radio-browser.info/json/stations/search?limit=8&hidebroken=true&order=clickcount&reverse=true&name=%s%s' % (
            q, ('&countrycode=' + cc) if cc else '')
        try:
            for st in http_json(url, 12):
                if st.get('url_resolved') and st.get('lastcheckok'):
                    return st['name'].strip(), st['url_resolved']
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return None, None


def radio_stop():
    try:
        pid = int(open(RADIO_PID).read())
        os.killpg(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass
    try:
        os.remove(RADIO_PID)
    except OSError:
        pass
    radio_now.update(nome='', url='')
    return {'ok': True}


def radio_play(name):
    radio_stop()
    st_name, url = radio_search(name)
    if not url:
        return {'erro': 'não achei a rádio "%s"' % name}
    p = subprocess.Popen(
        'exec /usr/bin/ffmpeg -loglevel quiet -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -i "%s" '
        '-vn -f s16le -ar 48000 -ac 2 - | /usr/local/bin/lsaudio-play' % url.replace('"', ''),
        shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
    os.makedirs(RUN, exist_ok=True)
    open(RADIO_PID, 'w').write(str(p.pid))
    radio_now.update(nome=st_name, url=url)
    return {'ok': True, 'tocando': st_name}


# ======================================================================= notas
def note_add(text):
    pw = pwd.getpwnam(USER)
    new = not os.path.exists(NOTES)
    with open(NOTES, 'a') as f:
        f.write('%s  %s\n' % (time.strftime('%d/%m %H:%M'), text.strip()))
    if new:
        os.chown(NOTES, pw.pw_uid, pw.pw_gid)
    return {'ok': True, 'arquivo': NOTES}


def note_list(n=10):
    try:
        return {'notas': open(NOTES).read().splitlines()[-n:]}
    except OSError:
        return {'notas': []}


# ======================================================================= Cast (Chromecast, Nest, Google TV)
def _cast_devices(timeout=6):
    import pychromecast
    casts, browser = pychromecast.get_chromecasts(timeout=timeout)
    return pychromecast, casts, browser


def cast_control(action, device=None, value=None, url=None):
    try:
        pychromecast, casts, browser = _cast_devices()
    except Exception as e:
        return {'erro': 'Cast indisponível: %s' % e}
    try:
        if action == 'list':
            return {'dispositivos': [{'nome': c.name, 'modelo': c.model_name, 'ip': c.cast_info.host, 'tipo': c.cast_type,
                                      'app': c.app_display_name} for c in casts]}
        cast = next((c for c in casts if device and device.lower() in c.name.lower()), None)
        if not cast:
            return {'erro': 'não achei o dispositivo "%s". Disponíveis: %s' % (device, ', '.join(c.name for c in casts) or 'nenhum')}
        cast.wait(timeout=8)
        mc = cast.media_controller
        if action == 'play':
            mc.play()
        elif action == 'pause':
            mc.pause()
        elif action == 'stop':
            mc.stop()
            cast.quit_app()
        elif action == 'volume':
            cast.set_volume(max(0.0, min(1.0, float(value) / 100.0)))
        elif action == 'mute':
            cast.set_volume_muted(True)
        elif action == 'unmute':
            cast.set_volume_muted(False)
        elif action == 'play_url':
            mc.play_media(url, 'audio/mp3' if not (url or '').endswith(('.mp4', '.m3u8')) else 'video/mp4')
            mc.block_until_active(timeout=10)
        else:
            return {'erro': 'ação desconhecida'}
        return {'ok': True, 'dispositivo': cast.name}
    except Exception as e:
        return {'erro': 'falha no Cast: %s' % e}
    finally:
        try:
            browser.stop_discovery()
        except Exception:
            pass


# ======================================================================= casa inteligente local + pacotes
CONTROL_FILE = VAR + '/controllable.json'


def home_control(action, device=None, value=None):
    import lshome
    action = (action or 'list').lower()
    if action == 'list' or not device:
        try:
            devs = json.load(open(DEVICES_FILE))['aparelhos']
        except (OSError, ValueError, KeyError):
            devs = scan_lan().get('aparelhos', [])
        found = lshome.scan_controllable(devs)
        try:
            json.dump(found, open(CONTROL_FILE, 'w'), ensure_ascii=False)
        except OSError:
            pass
        return {'controlaveis': [{'nome': d.get('nome') or d.get('tipo'), 'ip': d['ip'], 'fabricante': d.get('fabricante'),
                                  'protocolos': d['protocolos']} for d in found]}
    try:
        found = json.load(open(CONTROL_FILE))
    except (OSError, ValueError):
        found = []
    key = device.lower()
    hit = next((d for d in found if key in d['ip'] or key in (d.get('nome') or '').lower()
                or key in (d.get('fabricante') or '').lower() or key in (d.get('tipo') or '').lower()), None)
    if hit is None:                                              # talvez seja um IP novo: tenta identificar na hora
        import re as _re
        if _re.match(r'^\d+\.\d+\.\d+\.\d+$', device):
            protos = lshome.identify(device)
            if protos:
                hit = {'ip': device, 'protocolos': protos}
    if hit is None:
        return {'erro': 'não achei "%s" entre os aparelhos controláveis. Use home_control(list) para atualizar a lista.' % device}
    return lshome.control(hit['ip'], hit['protocolos'][0], action, value)


def install_package(name, confirm):
    if not re.match(r'^[a-z0-9][a-z0-9+._-]{0,60}$', name or ''):
        return {'erro': 'nome de pacote inválido'}
    found = subprocess.run(['apk', 'search', '-x', name], capture_output=True, text=True, timeout=60).stdout.strip()
    if not found:
        return {'erro': 'o pacote "%s" não existe no repositório do Alpine/postmarketOS' % name}
    if not confirm('Instalar o pacote "%s"?\n(apk add, como administrador do sistema)' % name):
        return {'erro': 'o dono não autorizou'}
    r = subprocess.run(['apk', 'add', '--no-progress', name], capture_output=True, text=True, timeout=400)
    return {'codigo_saida': r.returncode, 'saida': clip((r.stdout + r.stderr)[-1500:])}


# ======================================================================= Home Assistant (opcional)
def home_assistant(action, entity=None, service=None, data=None):
    try:
        cfg = json.load(open(HA_FILE))
    except (OSError, ValueError):
        return {'erro': 'Home Assistant não configurado (/etc/luizinhoshowos/homeassistant.json com url e token)'}
    base = cfg['url'].rstrip('/')
    hdr = {'Authorization': 'Bearer ' + cfg['token'], 'Content-Type': 'application/json'}
    try:
        if action == 'list':
            req = urllib.request.Request(base + '/api/states', headers=hdr)
            st = json.load(urllib.request.urlopen(req, timeout=15))
            dom = (entity or '').lower()
            return {'entidades': [{'id': s['entity_id'], 'estado': s['state'], 'nome': s['attributes'].get('friendly_name', '')}
                                  for s in st if not dom or s['entity_id'].startswith(dom)][:60]}
        if action == 'call' and service and '.' in service:
            d, s = service.split('.', 1)
            body = dict(data or {})
            if entity:
                body['entity_id'] = entity
            req = urllib.request.Request('%s/api/services/%s/%s' % (base, d, s), data=json.dumps(body).encode(), headers=hdr, method='POST')
            urllib.request.urlopen(req, timeout=15).read()
            return {'ok': True}
        return {'erro': 'use action=list ou action=call com service=dominio.servico'}
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        return {'erro': 'Home Assistant: %s' % e}


# ======================================================================= o registro de ferramentas
# ------------------------------------------------------------------ memória do TARS (fatos sobre o dono)
MEMORY_FILE = VAR + '/tars-memory.json'


def memory_load():
    try:
        m = json.load(open(MEMORY_FILE))
        return [x for x in m if isinstance(x, dict) and x.get('fato')]
    except (OSError, ValueError):
        return []


def _memory_save(items):
    os.makedirs(VAR, exist_ok=True)
    tmp = MEMORY_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(items[-40:], f, ensure_ascii=False)
    os.replace(tmp, MEMORY_FILE)


def memory_add(fato):
    fato = re.sub(r'\s+', ' ', (fato or '')).strip()[:200]
    if len(fato) < 3:
        return {'erro': 'nada para lembrar'}
    items = memory_load()
    if any(x['fato'].lower() == fato.lower() for x in items):
        return {'ok': True, 'ja_sabia': True, 'total': len(items)}
    items.append({'fato': fato, 'quando': time.strftime('%Y-%m-%d')})
    _memory_save(items)
    return {'ok': True, 'total': min(40, len(items))}


def memory_forget(trecho):
    trecho = (trecho or '').strip().lower()
    items = memory_load()
    if trecho in ('tudo', 'todas', 'todos'):
        _memory_save([])
        return {'ok': True, 'removidos': len(items)}
    keep = [x for x in items if trecho not in x['fato'].lower()] if len(trecho) >= 3 else items
    _memory_save(keep)
    return {'ok': True, 'removidos': len(items) - len(keep)}


def memory_context():
    items = memory_load()
    if not items:
        return ''
    return 'O QUE VOCÊ SABE SOBRE O DONO (memória sua, use com naturalidade, sem listar):\n' + '\n'.join('- ' + x['fato'] for x in items)


# ------------------------------------------------------------------ intenção e reflexão do dia
INTENT_FILE = VAR + '/intencao.json'
REFLECT_FILE = VAR + '/reflexao.json'
FOCUS_FILE = VAR + '/foco.json'
REFLEXOES = [          # reserva (sem internet ou sem cota): originais, no tom do TARS
    'O dia tem oitenta e seis mil e quatrocentos segundos e você não controla nenhum deles de antemão. Controle só o próximo.',
    'O que atrapalha o caminho é o caminho. Hoje, escolha qual obstáculo vai virar treino.',
    'Você não manda no trânsito, no clima nem na opinião dos outros. Sobra a sua resposta. Use-a bem.',
    'Falhar é caro só para quem não anota a lição. Anote, melhore, repita mais forte.',
    'Reclamar gasta o mesmo tempo que resolver. A diferença é o resultado.',
    'Perfeito é inimigo do feito. Comece feio, termine melhor. Eu confio na segunda versão.',
    'Medo é só informação com má reputação. Leia, decida e siga.',
    'Hoje vai dar errado em algum ponto. Ótimo: é daí que sai a parte útil do dia.',
    'Não adie o difícil para a versão de você de amanhã. Ela também está ocupada.',
    'Descanso faz parte da engenharia. Máquina que nunca para, quebra no pior momento.',
    'Faça uma coisa importante antes do meio-dia. O resto do dia fica mais leve e você, mais convencido.',
    'Quem aprende a gostar do desconforto controlado fica difícil de abalar. Treine hoje.',
]


def _today():
    return time.strftime('%Y-%m-%d')


def intencao_get():
    try:
        j = json.load(open(INTENT_FILE))
        return j.get('texto', '') if j.get('data') == _today() else ''
    except (OSError, ValueError):
        return ''


def intencao_set(texto):
    texto = re.sub(r'\s+', ' ', texto or '').strip()[:160]
    os.makedirs(VAR, exist_ok=True)
    with open(INTENT_FILE, 'w') as f:
        json.dump({'data': _today(), 'texto': texto}, f, ensure_ascii=False)
    return {'ok': True, 'intencao': texto} if texto else {'ok': True, 'limpa': True}


def reflexao_get():
    """Reflexão de hoje: a do cache, ou (sem cache) uma da reserva escolhida pelo dia do ano. -> (texto, é_do_cache)"""
    try:
        j = json.load(open(REFLECT_FILE))
        if j.get('data') == _today() and j.get('texto'):
            return j['texto'], True
    except (OSError, ValueError):
        pass
    return REFLEXOES[time.localtime().tm_yday % len(REFLEXOES)], False


def reflexao_save(texto):
    os.makedirs(VAR, exist_ok=True)
    with open(REFLECT_FILE, 'w') as f:
        json.dump({'data': _today(), 'texto': texto}, f, ensure_ascii=False)


def focus_stats():
    try:
        j = json.load(open(FOCUS_FILE))
        return j.get('sessoes', 0) if j.get('data') == _today() else 0
    except (OSError, ValueError):
        return 0


def focus_done():
    n = focus_stats() + 1
    os.makedirs(VAR, exist_ok=True)
    with open(FOCUS_FILE, 'w') as f:
        json.dump({'data': _today(), 'sessoes': n}, f)
    return n


_UN = ['zero', 'um', 'dois', 'três', 'quatro', 'cinco', 'seis', 'sete', 'oito', 'nove', 'dez', 'onze', 'doze', 'treze', 'quatorze', 'quinze',
       'dezesseis', 'dezessete', 'dezoito', 'dezenove']
_DEZ = ['', '', 'vinte', 'trinta', 'quarenta', 'cinquenta']


def _n2w(n):
    return _UN[n] if n < 20 else _DEZ[n // 10] + ('' if n % 10 == 0 else ' e ' + _UN[n % 10])


def hora_extenso(h, m):
    """20, 29 -> 'vinte horas e vinte e nove minutos' (o Gemini erra horas lidas como 20:29)."""
    hs = {0: 'meia-noite', 1: 'uma hora', 2: 'duas horas', 12: 'meio-dia'}.get(h) or ('%s horas' % _n2w(h))
    if h in (0, 12):
        return hs + ('' if m == 0 else ' e %s minuto%s' % (_n2w(m), '' if m == 1 else 's'))
    if m == 0:
        return hs + ' em ponto'
    return '%s e %s minuto%s' % (hs, _n2w(m), '' if m == 1 else 's')


def briefing():
    """Tudo o que o dono quer saber ao começar o dia, num só pacote."""
    lt = time.localtime()
    out = {'agora_por_extenso': hora_extenso(lt.tm_hour, lt.tm_min), 'periodo': 'madrugada' if lt.tm_hour < 5 else 'manhã' if lt.tm_hour < 12 else 'tarde' if lt.tm_hour < 18 else 'noite', 'dia_da_semana': ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo'][time.localtime().tm_wday]}
    try:
        w = json.load(open(VAR + '/weather.json'))
        hoje = w['proximos_dias'][0]
        out['clima'] = {'cidade': w['cidade'], 'agora_graus': round(w['agora']['temperatura']), 'condicao': w['agora']['condicao'],
                        'max': round(hoje['max']), 'min': round(hoje['min']), 'chance_chuva_pct': hoje['chance_chuva_pct']}
    except (OSError, ValueError, KeyError, IndexError):
        out['clima'] = 'indisponível'
    out['timers_e_alarmes'] = list_timers()
    out['notas_recentes'] = note_list(3).get('notas', [])
    out['intencao_do_dia'] = intencao_get() or 'não definida'
    out['sessoes_de_foco_hoje'] = focus_stats()
    cache = _load_news()
    if not cache.get('g1') or time.time() - cache['g1'].get('quando', 0) > 1800:
        cache = news_refresh(['g1', 'tecnologia'])
    out['manchetes'] = [i['titulo'] for i in cache.get('g1', {}).get('itens', [])[:3]] + \
                       [i['titulo'] for i in cache.get('tecnologia', {}).get('itens', [])[:2]]
    return out


# ------------------------------------------------------------------ notícias (RSS)
NEWS_FILE = VAR + '/news.json'
NEWS_FEEDS = {                         # chave: (nome, endereço)
    'g1': ('G1', 'https://g1.globo.com/rss/g1/'),
    'bbc': ('BBC Brasil', 'https://feeds.bbci.co.uk/portuguese/rss.xml'),
    'tecnologia': ('Tecnoblog', 'https://tecnoblog.net/feed/'),
    'mundo': ('DW Brasil', 'https://rss.dw.com/xml/rss-br-all'),
    'esportes': ('Esportes', 'https://ge.globo.com/rss/ge/'),
}


def news_refresh(keys=None):
    """Baixa os feeds e guarda em NEWS_FILE. -> {'chave': [itens]}"""
    import lsweb
    cache = _load_news()
    for k in (keys or NEWS_FEEDS):
        try:
            itens = [i for i in lsweb.rss(NEWS_FEEDS[k][1], 24) if not i['titulo'].upper().startswith(('VÍDEOS:', 'VÍDEO:'))]
            cache[k] = {'itens': itens[:15], 'quando': time.time()}
        except (ValueError, OSError) as e:
            log('notícias', k, e)
    try:
        os.makedirs(VAR, exist_ok=True)
        with open(NEWS_FILE, 'w') as f:
            json.dump(cache, f, ensure_ascii=False)
    except OSError:
        pass
    return cache


def _load_news():
    try:
        return json.load(open(NEWS_FILE))
    except (OSError, ValueError):
        return {}


def news(fonte='g1', n=5):
    key = next((k for k, (nome, _) in NEWS_FEEDS.items() if (fonte or 'g1').lower() in (k, nome.lower())), None)
    if key is None:
        key = {'tech': 'tecnologia', 'internacional': 'mundo', 'exterior': 'mundo', 'dw': 'mundo', 'esporte': 'esportes', 'futebol': 'esportes'}.get((fonte or '').lower(), 'g1')
    cache = _load_news()
    if key not in cache or time.time() - cache[key].get('quando', 0) > 900:
        cache = news_refresh([key])
    if key not in cache:
        return {'erro': 'não consegui baixar as notícias agora'}
    n = max(1, min(8, int(n or 5)))
    return {'fonte': NEWS_FEEDS[key][0], 'manchetes': [{'titulo': i['titulo'], 'resumo': i['resumo'][:160]} for i in cache[key]['itens'][:n]]}


class Tools:
    """Declarações para o Gemini + execução. `ui` oferece confirm(texto)->bool, event(texto), e os controles do aparelho."""

    def __init__(self, ui):
        self.ui = ui

    def declarations(self):
        S, I = 'STRING', 'INTEGER'

        def fn(name, desc, props=None, req=None):
            d = {'name': name, 'description': desc}
            if props:
                d['parameters'] = {'type': 'OBJECT', 'properties': {k: {'type': v[0], 'description': v[1]} for k, v in props.items()},
                                   'required': req or list(props)}
            return d
        return [{'functionDeclarations': [
            fn('run_command', 'Executa um comando de shell no Linux do Echo como usuário comum. Comandos só de leitura rodam direto; '
               'qualquer outro pede confirmação ao dono na tela. Use para ver arquivos, espaço, processos, rede, instalar programas etc.',
               {'command': (S, 'o comando bash'), 'motivo': (S, 'por que está executando (curto)')}),
            fn('system_status', 'Estado do aparelho: CPU, memória, disco, temperatura, uptime, IP e sinal do WiFi.'),
            fn('discover_devices', 'Varre a rede local e lista os aparelhos (IP, MAC, fabricante, serviços, tipo provável: Chromecast, '
               'Hue, Tuya, TV, impressora, câmera, celular...). Demora uns 15 segundos.'),
            fn('web_search', 'Pesquisa na web (Google) e devolve um resumo com fontes. Use para notícias, fatos atuais, preços, qualquer coisa que você não saiba.',
               {'consulta': (S, 'o que pesquisar')}),
            fn('fetch_url', 'Abre uma página web e devolve o texto dela.', {'url': (S, 'endereço http(s)')}),
            fn('download_file', 'Baixa um arquivo da internet para a pasta Downloads (pede confirmação ao dono).',
               {'url': (S, 'endereço'), 'nome_arquivo': (S, 'nome para salvar')}),
            fn('weather', 'Clima atual e previsão de 3 dias. Sem cidade, usa a localização do Echo.', {'cidade': (S, 'cidade opcional')}, []),
            fn('set_timer', 'Cria um timer/contagem regressiva.', {'segundos': (I, 'duração em segundos'), 'rotulo': (S, 'nome do timer')}),
            fn('set_alarm', 'Cria um alarme para um horário (próxima ocorrência).', {'hora': (S, 'HH:MM, 24 horas'), 'rotulo': (S, 'nome do alarme')}),
            fn('list_timers', 'Lista timers e alarmes ativos.'),
            fn('cancel_timer', 'Cancela um timer/alarme pelo id, ou todos se não passar id.', {'id': (I, 'id opcional')}, []),
            fn('play_radio', 'Toca uma rádio online pelo nome (ex.: Jovem Pan, CBN, Antena 1, lo-fi).', {'estacao': (S, 'nome da rádio')}),
            fn('stop_radio', 'Para a rádio.'),
            fn('note_add', 'Anota um lembrete/nota do dono.', {'texto': (S, 'o texto da nota')}),
            fn('note_list', 'Lê as últimas notas.'),
            fn('spotify', 'Controla o Spotify que está tocando (no Mac ou celular do dono).',
               {'comando': (S, 'toggle, next, prev, volup ou voldown')}),
            fn('set_volume', 'Ajusta o volume do alto-falante do Echo (0 a 100).', {'percentual': (I, '0-100')}),
            fn('cast', 'Controla Chromecast/Google Nest/Google TV da rede: list, play, pause, stop, volume, mute, unmute, play_url.',
               {'acao': (S, 'list|play|pause|stop|volume|mute|unmute|play_url'), 'dispositivo': (S, 'nome (parte dele) do aparelho'),
                'valor': (I, 'volume 0-100'), 'url': (S, 'URL de áudio para play_url')}, ['acao']),
            fn('home_control', 'Casa inteligente local (sem nuvem): lâmpadas WiZ/Yeelight, relés Shelly/Tasmota, tomadas TP-Link Kasa e Chromecast. '
               'acao=list descobre o que dá para controlar; on, off, toggle, brightness (valor 1-100) e status agem no aparelho (nome, fabricante ou IP).',
               {'acao': (S, 'list|on|off|toggle|brightness|status'), 'dispositivo': (S, 'nome, fabricante ou IP'),
                'valor': (I, 'brilho 1-100')}, ['acao']),
            fn('noticias', 'Manchetes de hoje (G1, BBC Brasil, Tecnoblog para tecnologia, DW Brasil para o mundo, Esportes). Use quando o dono pedir notícias ou um resumo do dia.',
               {'fonte': (S, 'g1, bbc, tecnologia, mundo ou esportes'), 'quantidade': (I, 'quantas manchetes, 1 a 8')}, []),
            fn('abrir_navegador', 'Abre o navegador na tela do Echo com um site ou uma busca (o dono vê e navega por toque). Use quando ele pedir para abrir um site, pesquisar "na tela" ou mostrar algo.',
               {'destino': (S, 'endereço do site ou termos de busca')}),
            fn('briefing', 'Pacote do dia: hora, clima, timers, notas, manchetes, intenção do dia e sessões de foco. Use para "bom dia", "resumo do dia", "o que tem para hoje".'),
            fn('intencao_do_dia', 'Define (ou limpa, com texto vazio) a intenção do dono para hoje, mostrada no app Hoje. Use quando ele disser qual é o foco ou a meta do dia.',
               {'texto': (S, 'a intenção, em uma frase curta')}),
            fn('lembrar', 'Guarda um fato duradouro sobre o dono (gostos, rotina, pessoas, preferências) na sua memória, para usar em conversas futuras. Use quando ele pedir "lembra que..." ou disser algo claramente pessoal e duradouro.',
               {'fato': (S, 'o fato, em uma frase curta')}),
            fn('esquecer', 'Apaga da sua memória os fatos que contêm o trecho informado (ou "tudo").', {'trecho': (S, 'trecho do fato ou "tudo"')}),
            fn('install_package', 'Instala um programa do repositório do sistema (apk). Pede confirmação ao dono.', {'pacote': (S, 'nome do pacote')}),
            fn('home_assistant', 'Home Assistant (se configurado): action=list lista entidades (entity=prefixo como light.), '
               'action=call chama um serviço (service=light.turn_on, entity=light.sala).',
               {'action': (S, 'list ou call'), 'entity': (S, 'entidade ou prefixo'), 'service': (S, 'dominio.servico')}, ['action']),
        ]}]

    def call(self, name, a):
        ui = self.ui
        try:
            if name == 'run_command':
                cmd = (a.get('command') or '').strip()
                level, why = classify(cmd)
                ui.event('$ ' + cmd[:70])
                if level == 'block':
                    return {'erro': 'BLOQUEADO: ' + why}
                if level == 'confirm' and not ui.confirm('Executar este comando?\n$ %s\n(%s)' % (cmd, a.get('motivo', why))):
                    return {'erro': 'o dono não autorizou'}
                code, out = run_shell(cmd)
                return {'codigo_saida': code, 'saida': out}
            if name == 'system_status':
                ui.event('lendo o estado do sistema')
                return system_status()
            if name == 'discover_devices':
                ui.event('varrendo a rede...')
                return scan_lan()
            if name == 'web_search':
                ui.event('pesquisando: ' + (a.get('consulta') or '')[:50])
                return web_search(a.get('consulta', ''))
            if name == 'fetch_url':
                ui.event('abrindo ' + (a.get('url') or '')[:50])
                return fetch_url(a.get('url', ''), ui.confirm)
            if name == 'download_file':
                return download_file(a.get('url', ''), a.get('nome_arquivo', ''), ui.confirm)
            if name == 'weather':
                ui.event('consultando o clima')
                return weather(a.get('cidade'))
            if name == 'set_timer':
                return add_timer(a.get('segundos', 60), a.get('rotulo', ''))
            if name == 'set_alarm':
                return add_alarm(a.get('hora', ''), a.get('rotulo', ''))
            if name == 'list_timers':
                return {'ativos': list_timers()}
            if name == 'cancel_timer':
                return cancel_timer(a.get('id'))
            if name == 'play_radio':
                ui.event('rádio: ' + (a.get('estacao') or ''))
                return radio_play(a.get('estacao', ''))
            if name == 'stop_radio':
                return radio_stop()
            if name == 'note_add':
                return note_add(a.get('texto', ''))
            if name == 'note_list':
                return note_list()
            if name == 'spotify':
                return ui.spotify(a.get('comando', 'toggle'))
            if name == 'set_volume':
                return ui.set_volume(int(a.get('percentual', 50)))
            if name == 'cast':
                ui.event('cast: %s %s' % (a.get('acao', ''), a.get('dispositivo', '')))
                return cast_control(a.get('acao', 'list'), a.get('dispositivo'), a.get('valor'), a.get('url'))
            if name == 'home_control':
                ui.event('casa: %s %s' % (a.get('acao', ''), a.get('dispositivo', '')))
                return home_control(a.get('acao', 'list'), a.get('dispositivo'), a.get('valor'))
            if name == 'noticias':
                ui.event('lendo as notícias')
                return news(a.get('fonte') or 'g1', a.get('quantidade') or 5)
            if name == 'abrir_navegador':
                ui.event('navegador: ' + (a.get('destino') or '')[:50])
                return ui.open_browser(a.get('destino', ''))
            if name == 'briefing':
                ui.event('montando o resumo do dia')
                return briefing()
            if name == 'intencao_do_dia':
                return intencao_set(a.get('texto', ''))
            if name == 'lembrar':
                ui.event('guardando na memória')
                return memory_add(a.get('fato', ''))
            if name == 'esquecer':
                return memory_forget(a.get('trecho', ''))
            if name == 'install_package':
                ui.event('pacote: ' + (a.get('pacote') or ''))
                return install_package(a.get('pacote', ''), ui.confirm)
            if name == 'home_assistant':
                return home_assistant(a.get('action', 'list'), a.get('entity'), a.get('service'), a.get('data'))
            return {'erro': 'ferramenta desconhecida: ' + name}
        except Exception as e:                                   # nunca derruba a conversa
            log('erro em', name, repr(e))
            return {'erro': 'falha em %s: %s' % (name, e)}


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == 'classify':
        print(classify(' '.join(sys.argv[2:])))
    elif len(sys.argv) > 1 and sys.argv[1] == 'scan':
        print(json.dumps(scan_lan(), ensure_ascii=False, indent=1))
