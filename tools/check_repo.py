#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The TarsOS Authors
"""Políticas do repositório (rodam no CI e na sua máquina):  python3 tools/check_repo.py

1. todo arquivo de código tem o cabeçalho SPDX;
2. nada de segredos, chaves, imagens de boot, firmware, modelos ou pastas pessoais versionados;
3. nenhum padrão de chave/token no texto;
4. nenhum endereço de rede privada (RFC 1918) fora do que foi explicitamente permitido;
5. nenhum comando que grave as partições lk/preloader/tee1/tee2 (brick permanente).
Sai com código 1 se algo falhar e lista cada problema."""
import fnmatch
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

CODE_EXT = ('.py', '.sh', '.start')
CODE_DIRS = ('rootfs-overlay/etc/init.d/', 'rootfs-overlay/usr/local/bin/', 'rootfs-overlay/etc/profile.d/', 'tools/', 'tests/', 'build/')
NO_SPDX = {'rootfs-overlay/usr/local/bin/tarsos'}           # link simbólico para showos

FORBIDDEN = ['keys/*', '*.pem', '*.key', '*_ed25519', '*_ed25519.pub', 'known_hosts', '.env', '.env.*', 'images/*', 'out/*', 'backup/*', 'ref/*',
             '*.img', '*.img.xz', '*.bin', '*.zip', '*.onnx', '*.tflite', 'tuya.json', 'devices.json', 'gemini.json', 'spotify-api.json',
             'spotify.json', 'homeassistant.json', 'tars-memory.json', 'tars-history.json', 'wake-samples/*', 'install.log', 'MANUAL.md']
ALLOWED_FORBIDDEN = {'docs/images/*'}                      # imagens da documentação são permitidas

SECRET_PATTERNS = [
    (r'AIza[0-9A-Za-z_\-]{35}', 'chave de API do Google'),
    (r'\bgh[pousr]_[A-Za-z0-9]{30,}', 'token do GitHub'),
    (r'github_pat_[A-Za-z0-9_]{40,}', 'token do GitHub'),
    (r'-----BEGIN [A-Z ]*PRIVATE KEY-----', 'chave privada'),
    (r'\bAKIA[0-9A-Z]{16}\b', 'chave da AWS'),
    (r'\bxox[baprs]-[A-Za-z0-9-]{10,}', 'token do Slack'),
    (r'\bsk-[A-Za-z0-9]{32,}', 'chave de API (sk-...)'),
]
PRIVATE_IP = re.compile(r'\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b')
FLASH_LK = re.compile(r'(?:\b(?:flash|erase)\s+(?:lk|preloader|tee1|tee2)\b|\bof=\S*(?:by-name/)?(?:lk|preloader|tee1|tee2)\b)')
# o próprio verificador e os testes citam esses padrões de propósito
SELF_EXEMPT = ('tools/check_repo.py', 'tests/')

TEXT_EXT = ('.py', '.sh', '.md', '.yml', '.yaml', '.txt', '.json', '.conf', '.start', '.toml', '.cfg', '.ini')


def tracked_files():
    try:
        out = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split('\n')
        files = [f for f in out if f]
        if files:
            return files
    except (OSError, subprocess.CalledProcessError):
        pass
    files = []
    for d, dirs, fs in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in ('.git', '__pycache__', 'node_modules', '.venv', 'dist')]
        for f in fs:
            files.append(os.path.relpath(os.path.join(d, f), ROOT))
    return files


def is_text(path):
    base = os.path.basename(path)
    return path.endswith(TEXT_EXT) or base in ('Dockerfile', 'NOTICE', 'LICENSE', '.gitignore', 'VERSION') or '/etc/' in path or '/usr/local/bin/' in path


def main():
    problems = []
    files = tracked_files()
    for rel in files:
        full = os.path.join(ROOT, rel)
        if os.path.islink(full) or not os.path.isfile(full):
            continue
        base = os.path.basename(rel)
        # 2) arquivos proibidos
        if not any(fnmatch.fnmatch(rel, a) for a in ALLOWED_FORBIDDEN):
            for pat in FORBIDDEN:
                if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(base, pat):
                    problems.append('%s: arquivo proibido no repositório (%s)' % (rel, pat))
                    break
        try:
            text = open(full, encoding='utf-8').read()
        except (UnicodeDecodeError, OSError):
            continue
        # 1) SPDX
        needs_spdx = rel not in NO_SPDX and (rel.endswith(CODE_EXT) or base == 'Dockerfile' or
                                             (rel.startswith(CODE_DIRS) and not rel.endswith(('.md', '.txt', '.json'))))
        if needs_spdx and 'SPDX-License-Identifier: Apache-2.0' not in '\n'.join(text.split('\n')[:6]):
            problems.append('%s: sem o cabeçalho SPDX-License-Identifier: Apache-2.0 nas primeiras linhas' % rel)
        if not is_text(rel):
            continue
        # 3) segredos
        for pat, what in SECRET_PATTERNS:
            if re.search(pat, text):
                problems.append('%s: parece conter %s' % (rel, what))
        # 4) IPs privados
        for n, line in enumerate(text.split('\n'), 1):
            if PRIVATE_IP.search(line) and 'check_repo: ip-ok' not in line and not rel.startswith('tests/'):
                problems.append('%s:%d: endereço de rede privada (%s)' % (rel, n, PRIVATE_IP.search(line).group(0)))
        # 5) partições proibidas
        if not rel.startswith(SELF_EXEMPT):
            for n, line in enumerate(text.split('\n'), 1):
                if FLASH_LK.search(line) and not re.search(r'nunca|never|n[ãa]o|proibid|brick', line, re.I):
                    problems.append('%s:%d: comando que grava lk/preloader/tee (brick permanente): %s' % (rel, n, line.strip()[:80]))
    if problems:
        print('✗ %d problema(s):' % len(problems))
        for p in problems:
            print('  -', p)
        return 1
    print('✓ políticas do repositório ok (%d arquivos versionados verificados)' % len(files))
    return 0


if __name__ == '__main__':
    sys.exit(main())
