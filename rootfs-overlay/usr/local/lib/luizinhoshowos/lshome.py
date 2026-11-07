#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The TarsOS Authors
"""TarsOS: casa inteligente pela rede local, sem conta e sem nuvem.

Drivers que falam o protocolo LOCAL de cada fabricante (a mesma rede onde o Google Home enxerga os aparelhos):
  wiz       lâmpadas Philips WiZ          UDP 38899 (JSON)
  yeelight  lâmpadas Xiaomi Yeelight      TCP 55443 (JSON; ligar o "controle LAN" no app da Yeelight)
  shelly    relés Shelly (gen1 e gen2)    HTTP
  tasmota   tomadas/relés com Tasmota     HTTP
  kasa      TP-Link Kasa (protocolo legado) TCP 9999
  cast      Chromecast / Google Nest / Google TV (pychromecast): play/pausa/volume
  tuya      Tuya / Smart Life (lâmpadas, tomadas baratas): protocolo local (porta 6668) via tinytuya.
            Precisa da CHAVE LOCAL de cada aparelho: /etc/luizinhoshowos/tuya.json no formato do `python3 -m tinytuya wizard`
            (lista de {name, id, key, mac, ip?, version?}); campo opcional "tipo": "lampada" (padrão: tomada).

Hue pede o botão do bridge para parear: fica para quando você o tiver.
`identify(ip)` descobre o que o aparelho entende; `control(ip, acao, valor)` executa on/off/toggle/brightness.
"""
import json
import socket
import struct
import threading
import time
import urllib.error
import urllib.request


def _udp(ip, port, payload, timeout=0.8):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(payload, (ip, port))
        return s.recvfrom(4096)[0]
    except OSError:
        return None
    finally:
        s.close()


def _http(url, timeout=1.5):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'TarsOS'}), timeout=timeout) as r:
            return r.read(20000).decode('utf-8', 'replace')
    except (urllib.error.URLError, OSError, ValueError):
        return None


# ------------------------------------------------------------------ WiZ
def wiz_get(ip):
    r = _udp(ip, 38899, b'{"method":"getPilot","params":{}}')
    try:
        return json.loads(r)['result'] if r else None
    except (ValueError, KeyError):
        return None


def wiz_set(ip, params):
    r = _udp(ip, 38899, json.dumps({'id': 1, 'method': 'setPilot', 'params': params}).encode(), 1.5)
    return bool(r)


# ------------------------------------------------------------------ Yeelight
def yee(ip, method, params=None, timeout=2.0):
    try:
        s = socket.create_connection((ip, 55443), timeout=timeout)
        s.sendall((json.dumps({'id': 1, 'method': method, 'params': params or []}) + '\r\n').encode())
        data = s.recv(2048).decode('utf-8', 'replace')
        s.close()
        for line in data.splitlines():
            j = json.loads(line)
            if j.get('id') == 1:
                return j
    except (OSError, ValueError):
        pass
    return None


# ------------------------------------------------------------------ Kasa (protocolo legado, XOR autokey)
def _kasa_enc(text):
    key, out = 171, bytearray()
    for c in text.encode():
        key ^= c
        out.append(key)
    return struct.pack('>I', len(out)) + bytes(out)


def _kasa_dec(data):
    key, out = 171, bytearray()
    for b in data:
        out.append(key ^ b)
        key = b
    return out.decode('utf-8', 'replace')


def kasa(ip, obj, timeout=2.0):
    try:
        s = socket.create_connection((ip, 9999), timeout=timeout)
        s.sendall(_kasa_enc(json.dumps(obj)))
        head = s.recv(4)
        n = struct.unpack('>I', head)[0]
        buf = b''
        while len(buf) < n:
            chunk = s.recv(n - len(buf))
            if not chunk:
                break
            buf += chunk
        s.close()
        return json.loads(_kasa_dec(buf))
    except (OSError, ValueError, struct.error):
        return None


# ------------------------------------------------------------------ Tuya (tinytuya)
TUYA_FILE = '/etc/luizinhoshowos/tuya.json'


def tuya_entries():
    try:
        data = json.load(open(TUYA_FILE))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _tuya_id_of(ip):
    try:
        for d in json.load(open('/var/lib/luizinhoshowos/devices.json')).get('aparelhos', []):
            if d['ip'] == ip:
                return (d.get('tuya') or {}).get('id')
    except (OSError, ValueError):
        pass
    return None


def tuya_entry(ip):
    mac = _mac_of(ip)
    did = _tuya_id_of(ip)
    for e in tuya_entries():
        if did and e.get('id') == did:                  # o ID do aparelho é o casamento mais confiável (o IP pode mudar)
            return e
        if e.get('ip') == ip or (mac and e.get('mac', '').lower() == mac) or e.get('ip') in (None, '') and e.get('mac', '').lower() == mac:
            return e
    return None


def _mac_of(ip):
    try:
        for line in open('/proc/net/arp').read().splitlines()[1:]:
            f = line.split()
            if f[0] == ip:
                return f[3].lower()
    except OSError:
        pass
    return None


def tuya_control(ip, action, value=None):
    e = tuya_entry(ip)
    if not e:
        return {'erro': 'aparelho Tuya sem chave local. Gere as chaves com "python3 -m tinytuya wizard" (precisa de uma conta em '
                        'iot.tuya.com ligada ao app Smart Life) e salve o devices.json em /etc/luizinhoshowos/tuya.json'}
    import tinytuya
    ver = float(e.get('version') or e.get('ver') or 3.3)
    cls = tinytuya.BulbDevice if e.get('tipo') == 'lampada' else tinytuya.OutletDevice
    d = cls(e['id'], ip, e['key'], version=ver)
    d.set_socketTimeout(4)
    d.set_socketPersistent(False)
    st = d.status()
    if not st or 'dps' not in st:
        return {'erro': 'sem resposta do aparelho Tuya (%s)' % (st or {}).get('Error', 'desconhecido')}
    key = '20' if (e.get('tipo') == 'lampada' and '20' in st['dps']) else '1'
    cur = bool(st['dps'].get(key))
    if action == 'status':
        return {'ligada': cur, 'dps': st['dps']}
    if action == 'brightness' and e.get('tipo') == 'lampada':
        d.turn_on()
        d.set_brightness_percentage(max(1, min(100, int(value or 50))))
        return {'ok': True}
    want = (not cur) if action == 'toggle' else action == 'on'
    d.turn_on() if want else d.turn_off()
    return {'ok': True, 'ligada': want}


# ------------------------------------------------------------------ identificação
def identify(ip):
    """-> lista de protocolos que o IP entende, ex.: ['wiz'] ou ['cast']."""
    found = []
    if wiz_get(ip):
        found.append('wiz')
    try:
        socket.create_connection((ip, 55443), 0.5).close()
        if yee(ip, 'get_prop', ['power']):
            found.append('yeelight')
    except OSError:
        pass
    sh = _http('http://%s/shelly' % ip)
    if sh and ('"type"' in sh or '"gen"' in sh or '"mac"' in sh):
        found.append('shelly')
    ta = _http('http://%s/cm?cmnd=Power' % ip)
    if ta and '"POWER' in ta.upper():
        found.append('tasmota')
    try:
        socket.create_connection((ip, 9999), 0.5).close()
        if kasa(ip, {'system': {'get_sysinfo': {}}}):
            found.append('kasa')
    except OSError:
        pass
    try:
        socket.create_connection((ip, 8009), 0.5).close()
        found.append('cast')
    except OSError:
        pass
    try:
        socket.create_connection((ip, 6668), 0.5).close()
        found.append('tuya')                      # porta local da Tuya aberta (precisa de chave para controlar)
    except OSError:
        pass
    return found


# ------------------------------------------------------------------ controle
def _truthy(v):
    return v in (True, 1, 'on', 'ON', 'On')


def control(ip, proto, action, value=None):
    """action: on | off | toggle | brightness | status. brightness em 0-100. -> dict"""
    action = (action or 'status').lower()
    if proto == 'wiz':
        st = wiz_get(ip)
        if st is None:
            return {'erro': 'a lâmpada WiZ não respondeu'}
        cur = _truthy(st.get('state'))
        if action == 'status':
            return {'ligada': cur, 'brilho': st.get('dimming')}
        if action == 'brightness':
            return {'ok': wiz_set(ip, {'state': True, 'dimming': max(10, min(100, int(value or 50)))})}
        return {'ok': wiz_set(ip, {'state': (not cur) if action == 'toggle' else action == 'on'})}
    if proto == 'yeelight':
        if action == 'status':
            j = yee(ip, 'get_prop', ['power', 'bright'])
            return {'ligada': (j or {}).get('result', [''])[0] == 'on', 'brilho': (j or {}).get('result', ['', ''])[1]} if j else {'erro': 'sem resposta'}
        if action == 'brightness':
            return {'ok': bool(yee(ip, 'set_bright', [max(1, min(100, int(value or 50))), 'smooth', 400]))}
        if action == 'toggle':
            return {'ok': bool(yee(ip, 'toggle'))}
        return {'ok': bool(yee(ip, 'set_power', ['on' if action == 'on' else 'off', 'smooth', 400]))}
    if proto == 'shelly':
        sh = _http('http://%s/shelly' % ip) or ''
        gen2 = '"gen"' in sh and '"gen":1' not in sh.replace(' ', '')
        if gen2:
            if action == 'status':
                return {'resposta': _http('http://%s/rpc/Switch.GetStatus?id=0' % ip)}
            if action == 'toggle':
                return {'resposta': _http('http://%s/rpc/Switch.Toggle?id=0' % ip)}
            return {'resposta': _http('http://%s/rpc/Switch.Set?id=0&on=%s' % (ip, 'true' if action == 'on' else 'false'))}
        if action == 'status':
            return {'resposta': _http('http://%s/relay/0' % ip)}
        return {'resposta': _http('http://%s/relay/0?turn=%s' % (ip, 'toggle' if action == 'toggle' else action))}
    if proto == 'tasmota':
        cmd = {'on': 'Power%20On', 'off': 'Power%20Off', 'toggle': 'Power%20Toggle', 'status': 'Power'}.get(action, 'Power')
        if action == 'brightness':
            cmd = 'Dimmer%20' + str(max(0, min(100, int(value or 50))))
        return {'resposta': _http('http://%s/cm?cmnd=%s' % (ip, cmd))}
    if proto == 'kasa':
        info = kasa(ip, {'system': {'get_sysinfo': {}}})
        if not info:
            return {'erro': 'sem resposta'}
        cur = info['system']['get_sysinfo'].get('relay_state', 0) == 1
        if action == 'status':
            return {'ligada': cur, 'nome': info['system']['get_sysinfo'].get('alias')}
        want = (not cur) if action == 'toggle' else action == 'on'
        return {'ok': bool(kasa(ip, {'system': {'set_relay_state': {'state': 1 if want else 0}}}))}
    if proto == 'tuya':
        return tuya_control(ip, action, value)
    if proto == 'cast':
        import lstools
        mapa = {'on': 'play', 'off': 'pause', 'toggle': 'pause', 'status': 'list', 'brightness': 'volume'}
        return lstools.cast_control(mapa.get(action, 'list'), None if action == 'status' else ip, value)
    return {'erro': 'protocolo desconhecido'}


def scan_controllable(devices):
    """Recebe a lista de aparelhos do scan e devolve os que sabemos controlar (com o protocolo)."""
    out, lock = [], threading.Lock()

    def work(d):
        protos = identify(d['ip'])
        if protos:
            with lock:
                out.append(dict(d, protocolos=protos))
    threads = [threading.Thread(target=work, args=(d,)) for d in devices if 'ESTE APARELHO' not in d.get('tipo', '')]
    for i in range(0, len(threads), 12):
        batch = threads[i:i + 12]
        [t.start() for t in batch]
        [t.join(20) for t in batch]
    return out


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        print(identify(sys.argv[1]))
