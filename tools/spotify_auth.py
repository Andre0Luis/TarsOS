#!/usr/bin/env python3
"""Autoriza o TarsOS na Web API do Spotify (OAuth PKCE, sem client secret).
Abre o login do Spotify no navegador do Mac, captura o código em http://127.0.0.1:8899/callback,
troca por tokens e grava o refresh token no Echo (/etc/luizinhoshowos/spotify-api.json, modo 600)."""
import base64, hashlib, http.server, json, os, secrets, subprocess, sys, urllib.parse, urllib.request

CLIENT_ID = sys.argv[1]
ECHO = sys.argv[2] if len(sys.argv) > 2 else os.environ.get('TARSOS_HOST', 'tarsos.local')
REDIRECT = 'http://127.0.0.1:8899/callback'
SCOPES = 'user-read-playback-state user-modify-playback-state user-read-currently-playing'
HERE = os.path.dirname(os.path.abspath(__file__))
KEY = os.environ.get('TARSOS_KEY') or next((k for k in (os.path.join(HERE, '..', 'keys', n) for n in ('tarsos_ed25519', 'luizinhoshowos_ed25519')) if os.path.exists(k)), '')
KNOWN = os.path.join(HERE, '..', 'keys', 'known_hosts')

verifier = secrets.token_urlsafe(64)[:96]
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
state = secrets.token_urlsafe(16)
url = 'https://accounts.spotify.com/authorize?' + urllib.parse.urlencode({
    'client_id': CLIENT_ID, 'response_type': 'code', 'redirect_uri': REDIRECT, 'scope': SCOPES,
    'code_challenge_method': 'S256', 'code_challenge': challenge, 'state': state})
result = {}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if not self.path.startswith('/callback'):
            self.send_error(404); return
        ok = q.get('state', [''])[0] == state and 'code' in q
        result['code'] = q.get('code', [None])[0] if ok else None
        result['error'] = q.get('error', [None])[0]
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        msg = '✅ TarsOS autorizado! Pode fechar esta aba.' if ok else '❌ Autorização não concluída.'
        self.wfile.write(f'<html><body style="font-family:system-ui;background:#0c0e1a;color:#e4e6ee;'
                         f'display:grid;place-items:center;height:90vh"><h2>{msg}</h2></body></html>'.encode())


srv = http.server.HTTPServer(('127.0.0.1', 8899), H)
srv.timeout = 300
subprocess.run(['open', url])
print('Navegador aberto no login do Spotify. Aguardando autorização (até 5 min)...', flush=True)
while 'code' not in result and 'error' not in result:
    srv.handle_request()
    if not result:
        break
if not result.get('code'):
    sys.exit(f'falhou: {result.get("error") or "tempo esgotado"}')
data = urllib.parse.urlencode({'grant_type': 'authorization_code', 'code': result['code'], 'redirect_uri': REDIRECT,
                               'client_id': CLIENT_ID, 'code_verifier': verifier}).encode()
tok = json.load(urllib.request.urlopen(urllib.request.Request('https://accounts.spotify.com/api/token', data=data)))
me = json.load(urllib.request.urlopen(urllib.request.Request('https://api.spotify.com/v1/me',
              headers={'Authorization': 'Bearer ' + tok['access_token']})))
cfg = json.dumps({'client_id': CLIENT_ID, 'refresh_token': tok['refresh_token'], 'scope': tok.get('scope', '')})
subprocess.run(['ssh', '-i', KEY, '-o', f'UserKnownHostsFile={KNOWN}', '-o', 'StrictHostKeyChecking=accept-new', f'root@{ECHO}',
                'umask 077; mkdir -p /etc/luizinhoshowos; cat > /etc/luizinhoshowos/spotify-api.json; '
                'chmod 600 /etc/luizinhoshowos/spotify-api.json; echo gravado no Echo'],
               input=cfg.encode(), check=True)
print(f'Autorizado como: {me.get("display_name") or me.get("id")} (plano: {me.get("product", "?")})')
print('Permissões:', tok.get('scope'))
