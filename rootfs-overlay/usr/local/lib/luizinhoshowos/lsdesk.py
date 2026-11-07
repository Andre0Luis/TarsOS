#!/usr/bin/env python3
"""TarsOS Desktop: área de trabalho no framebuffer do Echo Show 5.

O kernel não tem fbcon nem DRM, então este processo é o "servidor gráfico":
  - compõe a tela (paisagem 960x480) com o Pillow e grava em /dev/fb0
    (painel em retrato 480x960, RGBA, girado 270 graus);
  - lê o touch (goodix-ts) e os botões de volume (gpio-keys) pelo evdev;
  - gerencia janelas: início, apps em tela cheia ou divididos (◫), barra de tarefas;
  - transmite a tela ao vivo em http://<ip>:8080 (MJPEG);
  - aceita comandos de teste no FIFO /run/lsdesk.ctl ("tap X Y", "text abc", "key enter").
"""
import errno
import fcntl
import io
import json
import random
import re
import math
import os
import pty
import pwd
import select
import signal
import socket
import struct
import subprocess
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import evdev
import pyte
from PIL import Image, ImageDraw, ImageFont

from lsbrain import Brain, owner_name
import lshome
import lstools
import lsweb

try:
    from lsvoice import Listener
except ImportError:                    # sem numpy: sem escuta
    Listener = None

W, H = 960, 480
STATUS_H, DOCK_H, KB_H = 30, 50, 200
FB_DEV = '/dev/fb0'
CTL_FIFO = '/run/lsdesk.ctl'
HTTP_PORT = 8080
FONT_DIR = '/usr/share/fonts/dejavu'
SPLASH = '/usr/share/luizinhoshowos/splash.raw'
RELEASE = '/etc/luizinhoshowos-release'
BACKLIGHT = '/sys/class/leds/lcd-backlight/brightness'
TERM_USER = 'user'                     # o terminal da tela roda como este usuário (sudo para root)

BG = (12, 14, 26)
PANEL = (20, 23, 40)
CARD = (32, 36, 60)
FG = (228, 230, 238)
DIM = (150, 158, 180)
ACCENT = (255, 205, 80)
BLUE = (90, 150, 255)
RED = (235, 85, 85)
GREEN = (90, 205, 120)

_fonts = {}


def font(size, kind='sans'):
    key = (size, kind)
    if key not in _fonts:
        name = {'sans': 'DejaVuSans.ttf', 'bold': 'DejaVuSans-Bold.ttf',
                'mono': 'DejaVuSansMono.ttf', 'monob': 'DejaVuSansMono-Bold.ttf'}[kind]
        _fonts[key] = ImageFont.truetype(f'{FONT_DIR}/{name}', size)
    return _fonts[key]


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def sh(cmd, timeout=5):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ''


def ip_addr():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('192.0.2.1', 9))          # não envia nada; só escolhe a interface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def read_release():
    info = {}
    try:
        for line in open(RELEASE):
            if '=' in line:
                k, v = line.strip().split('=', 1)
                info[k] = v.strip('"')
    except OSError:
        pass
    return info


def center_text(d, box, text, fnt, fill):
    x0, y0, x1, y1 = box
    tw = d.textlength(text, font=fnt)
    asc, desc = fnt.getmetrics()
    d.text(((x0 + x1 - tw) / 2, (y0 + y1 - asc - desc) / 2), text, font=fnt, fill=fill)


def button(d, box, label, fnt=None, fill=CARD, color=FG, radius=10):
    d.rounded_rectangle(box, radius=radius, fill=fill)
    center_text(d, box, label, fnt or font(18), color)


def hit(box, x, y):
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


# ======================================================================= sistema
class Audio:
    """Alto-falante: RT5616 (hw:0,23) -> LOUT -> amplificador externo.
    Ext_Speaker_Amp_Switch é invertido: Off liga o amplificador, On muta.
    Só escreve controles específicos: ler o mixer inteiro já travou o kernel."""
    MAX = 39
    STATE = '/var/lib/luizinhoshowos/volume'

    def __init__(self):
        try:
            self.vol = int(open(self.STATE).read().strip())
        except (OSError, ValueError):
            self.vol = 22
        self.muted = False
        self.enabled = False
        self.enable()

    def enable(self):
        out = sh('/usr/local/bin/audio-speaker %d' % self.vol, timeout=15)
        self.enabled = True
        self.muted = False
        return out

    def set_volume(self, v):
        self.vol = max(0, min(self.MAX, v))
        sh("amixer -q -c0 cset name='OUT Playback Volume' %d,%d; amixer -q -c0 cset name='HP Playback Volume' %d,%d"
           % (self.vol, self.vol, self.vol, self.vol))
        try:
            os.makedirs(os.path.dirname(self.STATE), exist_ok=True)
            open(self.STATE, 'w').write(str(self.vol))
        except OSError:
            pass

    def toggle_mute(self):
        self.muted = not self.muted
        sh('/usr/local/bin/audio-speaker %s' % ('mute' if self.muted else 'unmute'))

    def percent(self):
        return 0 if self.muted else round(100 * self.vol / self.MAX)

    def test(self):
        if not os.path.exists('/tmp/lsdesk-tom.wav'):
            import math
            import wave
            r, n = 48000, 48000
            with wave.open('/tmp/lsdesk-tom.wav', 'wb') as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(r)
                frames = bytearray()
                for i in range(n):
                    f = 523 if i < n // 2 else 784          # dó e sol
                    v = int(6000 * math.sin(2 * math.pi * f * i / r) * min(1, i / 1200, (n - i) / 1200))
                    frames += struct.pack('<hh', v, v)
                w.writeframes(bytes(frames))
        subprocess.Popen(['/usr/local/bin/lsplay', '/tmp/lsdesk-tom.wav'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Backlight:
    def get(self):
        try:
            return int(open(BACKLIGHT).read().strip())
        except (OSError, ValueError):
            return 0

    def set(self, v):
        v = max(5, min(255, v))
        try:
            open(BACKLIGHT, 'w').write(str(v))
        except OSError:
            pass
        return v

    def percent(self):
        return round(100 * self.get() / 255)


# ======================================================================= apps
class App:
    title = 'App'
    wants_keyboard = False
    single = True                      # só uma instância
    animated = False                   # True: a área de trabalho redesenha ~8x/s enquanto este app está visível

    def __init__(self, desk):
        self.desk = desk
        self.dirty = True
        self.size = None
        self._img = None

    def image(self, w, h):
        if self._img is None or self.dirty or self.size != (w, h):
            if self.size != (w, h):
                self.size = (w, h)
                self.on_resize(w, h)
            img = Image.new('RGB', (w, h), BG)
            self.draw(ImageDraw.Draw(img), w, h)
            self._img = img
            self.dirty = False
        return self._img

    def on_resize(self, w, h):
        pass

    def draw(self, d, w, h):
        pass

    def on_tap(self, x, y):
        pass

    def on_text(self, data):
        pass

    def tick(self):                    # chamado ~1x por segundo
        pass

    def fds(self):
        return []

    def on_fd(self, fd):
        pass

    def close(self):
        pass


class TerminalApp(App):
    title = 'Terminal'
    wants_keyboard = True
    single = False
    FONT = 16

    def __init__(self, desk, argv=None, title=None, run_as=TERM_USER):
        super().__init__(desk)
        self.argv = argv or (['/bin/bash', '-l'] if os.path.exists('/bin/bash') else ['/bin/sh', '-l'])
        self.run_as = run_as
        if title:
            self.title = title
        self.fnt = font(self.FONT, 'mono')
        self.fntb = font(self.FONT, 'monob')
        self.cw = round(self.fnt.getlength('M'))
        a, de = self.fnt.getmetrics()
        self.ch = a + de + 1
        self.screen = pyte.Screen(80, 20)
        self.stream = pyte.ByteStream(self.screen)
        self.pid = self.pty = None
        self.spawn()

    def spawn(self):
        pid, fd = pty.fork()
        if pid == 0:
            pw = pwd.getpwnam(self.run_as)
            env = {'TERM': 'xterm-256color', 'LANG': 'C.UTF-8', 'HOME': pw.pw_dir, 'USER': pw.pw_name,
                   'LOGNAME': pw.pw_name, 'SHELL': pw.pw_shell,
                   'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'}
            if pw.pw_uid != 0:
                # o pty nasce do root: passa a posse para o usuário (sudo precisa abrir /dev/tty)
                try:
                    os.chown(os.ttyname(0), pw.pw_uid, -1)
                except OSError:
                    pass
                os.initgroups(pw.pw_name, pw.pw_gid)
                os.setgid(pw.pw_gid)
                os.setuid(pw.pw_uid)
            os.chdir(pw.pw_dir)
            os.execvpe(self.argv[0], self.argv, env)
        self.pid, self.pty = pid, fd
        self.set_winsize()

    def set_winsize(self):
        if self.pty is not None:
            fcntl.ioctl(self.pty, termios.TIOCSWINSZ, struct.pack('HHHH', self.screen.lines, self.screen.columns, 0, 0))
            try:
                os.kill(self.pid, signal.SIGWINCH)
            except ProcessLookupError:
                pass

    def on_resize(self, w, h):
        cols, rows = max(10, (w - 8) // self.cw), max(3, (h - 6) // self.ch)
        self.screen.resize(rows, cols)
        self.set_winsize()

    def fds(self):
        return [self.pty] if self.pty is not None else []

    def on_fd(self, fd):
        try:
            data = os.read(self.pty, 65536)
        except OSError as e:
            if e.errno != errno.EIO:
                raise
            data = b''
        if not data:
            try:
                os.waitpid(self.pid, 0)
            except ChildProcessError:
                pass
            os.close(self.pty)
            self.pty = None
            self.stream.feed(b'\r\n\x1b[33m[processo terminou; toque para reabrir]\x1b[0m')
        else:
            self.stream.feed(data)
        self.dirty = True

    def on_tap(self, x, y):
        if self.pty is None:
            self.screen.reset()
            self.spawn()
            self.dirty = True

    def on_text(self, data):
        if self.pty is not None:
            try:
                os.write(self.pty, data)
            except OSError:
                pass

    def close(self):
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass

    def draw(self, d, w, h):
        ansi = ANSI
        buf = self.screen.buffer
        y0 = 3
        for row in range(self.screen.lines):
            line = buf[row]
            y = y0 + row * self.ch
            col = 0
            while col < self.screen.columns:
                c0 = line[col]
                attrs = (c0.fg, c0.bg, c0.bold, c0.reverse)
                start, text = col, []
                while col < self.screen.columns:
                    c = line[col]
                    if (c.fg, c.bg, c.bold, c.reverse) != attrs:
                        break
                    text.append(c.data or ' ')
                    col += 1
                fg, bg = color(attrs[0], FG, ansi), color(attrs[1], BG, ansi)
                if attrs[3]:
                    fg, bg = bg, fg
                x = 4 + start * self.cw
                if bg != BG:
                    d.rectangle([x, y, x + len(text) * self.cw, y + self.ch], fill=bg)
                s = ''.join(text)
                if s.strip():
                    d.text((x, y), s, font=self.fntb if attrs[2] else self.fnt, fill=fg)
        cur = self.screen.cursor
        if not cur.hidden and self.pty is not None:
            cx, cy = 4 + cur.x * self.cw, y0 + cur.y * self.ch
            d.rectangle([cx, cy, cx + self.cw - 1, cy + self.ch - 1], outline=ACCENT, width=2)


ANSI = {
    'black': (40, 42, 54), 'red': (230, 80, 80), 'green': (90, 200, 110), 'brown': (220, 180, 70),
    'blue': (90, 140, 240), 'magenta': (200, 110, 220), 'cyan': (80, 200, 210), 'white': (220, 220, 225),
    'brightblack': (110, 112, 125), 'brightred': (255, 120, 120), 'brightgreen': (140, 240, 150),
    'brightbrown': (255, 225, 120), 'brightblue': (140, 180, 255), 'brightmagenta': (240, 150, 250),
    'brightcyan': (130, 235, 240), 'brightwhite': (255, 255, 255),
}


def color(name, default, table=ANSI):
    if name == 'default':
        return default
    if name in table:
        return table[name]
    if len(name) == 6:
        try:
            return tuple(int(name[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return default


class LiveApp(App):
    """"Ao Vivo": escolhe um comando e mostra a saída em tempo real."""
    title = 'Ao Vivo'
    PRESETS = [
        ('Kernel', 'dmesg -w'),
        ('Sistema', 'logread -f'),
        ('Processos', 'top -d 1'),
        ('Rede', 'ping 1.1.1.1'),
        ('WiFi', 'watch -n 2 "nmcli -f IN-USE,SSID,SIGNAL,RATE dev wifi | head -12"'),
    ]

    def __init__(self, desk):
        super().__init__(desk)
        self.term = None
        self.choice = None

    def start(self, i):
        if self.term:
            self.term.close()
        name, cmd = self.PRESETS[i]
        self.choice = i
        self.term = TerminalApp(self.desk, ['/bin/sh', '-c', cmd], title=name, run_as='root')   # logs pedem root
        if self.size:
            self.term.image(self.size[0], self.size[1] - 46)
        self.dirty = True
        self.desk.rewatch()

    def fds(self):
        return self.term.fds() if self.term else []

    def on_fd(self, fd):
        self.term.on_fd(fd)
        self.dirty = True

    def on_tap(self, x, y):
        if y < 46:
            for i, box in enumerate(self.boxes):
                if hit(box, x, y):
                    self.start(i)
                    return

    def close(self):
        if self.term:
            self.term.close()

    def draw(self, d, w, h):
        self.boxes = []
        bw = (w - 12) / len(self.PRESETS)
        for i, (name, _) in enumerate(self.PRESETS):
            box = (int(6 + i * bw) + 3, 6, int(6 + (i + 1) * bw) - 3, 40)
            self.boxes.append(box)
            button(d, box, name, font(16, 'bold'), fill=ACCENT if i == self.choice else CARD,
                   color=(20, 20, 30) if i == self.choice else FG)
        if self.term:
            d._image.paste(self.term.image(w, h - 46), (0, 46))
        else:
            center_text(d, (0, 46, w, h), 'Escolha o que transmitir ao vivo ↑', font(22), DIM)


class MonitorApp(App):
    title = 'Monitor'

    def __init__(self, desk):
        super().__init__(desk)
        self.prev_cpu = None
        self.prev_net = None
        self.cpu = []
        self.hist = []
        self.net = (0, 0)
        self.tick()

    def read_cpu(self):
        rows = {}
        for line in open('/proc/stat'):
            if line.startswith('cpu'):
                p = line.split()
                vals = list(map(int, p[1:8]))
                rows[p[0]] = (sum(vals), vals[3] + vals[4])
        return rows

    def tick(self):
        cur = self.read_cpu()
        if self.prev_cpu:
            self.cpu = []
            for k in sorted(cur):
                t0, i0 = self.prev_cpu.get(k, cur[k])
                t1, i1 = cur[k]
                dt = max(1, t1 - t0)
                self.cpu.append((k, 100 * (1 - (i1 - i0) / dt)))
            if self.cpu:
                self.hist = (self.hist + [self.cpu[0][1]])[-120:]
        self.prev_cpu = cur
        rx = tx = 0
        for line in open('/proc/net/dev'):
            if 'wlan0' in line:
                p = line.split(':')[1].split()
                rx, tx = int(p[0]), int(p[8])
        if self.prev_net:
            self.net = (rx - self.prev_net[0], tx - self.prev_net[1])
        self.prev_net = (rx, tx)
        self.dirty = True

    def draw(self, d, w, h):
        mem = {l.split(':')[0]: int(l.split()[1]) for l in open('/proc/meminfo') if l.split()[1].isdigit()}
        used = (mem['MemTotal'] - mem['MemAvailable']) / 1024
        total = mem['MemTotal'] / 1024
        load = open('/proc/loadavg').read().split()[:3]
        temps = []
        for z in range(12):
            try:
                t = int(open(f'/sys/class/thermal/thermal_zone{z}/temp').read()) / 1000
                if 5 < t < 120:
                    temps.append(t)
            except (OSError, ValueError):
                pass
        up = float(open('/proc/uptime').read().split()[0])
        x0, y = 20, 14
        d.text((x0, y), 'CPU', font=font(20, 'bold'), fill=ACCENT)
        y += 30
        for name, pct in self.cpu:
            label = 'total' if name == 'cpu' else name.replace('cpu', 'núcleo ')
            d.text((x0, y), label, font=font(15), fill=DIM)
            bx = (x0 + 90, y + 2, x0 + 380, y + 16)
            d.rounded_rectangle(bx, 5, fill=CARD)
            d.rounded_rectangle((bx[0], bx[1], bx[0] + (bx[2] - bx[0]) * pct / 100, bx[3]), 5,
                                fill=RED if pct > 80 else BLUE)
            d.text((bx[2] + 8, y), f'{pct:4.0f}%', font=font(15), fill=FG)
            y += 24
        # histórico
        gx0, gy0, gx1, gy1 = x0, y + 8, x0 + 440, min(h - 14, y + 120)
        d.rounded_rectangle((gx0, gy0, gx1, gy1), 6, fill=PANEL)
        if len(self.hist) > 1:
            pts = [(gx0 + (gx1 - gx0) * i / 119, gy1 - (gy1 - gy0) * v / 100) for i, v in enumerate(self.hist)]
            d.line(pts, fill=ACCENT, width=2)
        # coluna direita
        x1, y = w // 2 + 20, 14
        items = [
            ('Memória', f'{used:.0f} / {total:.0f} MB', used / total),
            ('Load', ' · '.join(load), None),
            ('Temperatura', (f'{max(temps):.0f} °C' if temps else 'n/d'), None),
            ('WiFi ↓ / ↑', f'{self.net[0] / 1024:.1f} / {self.net[1] / 1024:.1f} KB/s', None),
            ('Ligado há', f'{int(up // 3600)}h {int(up % 3600 // 60)}min', None),
            ('Processos', sh('ls -d /proc/[0-9]* | wc -l'), None),
        ]
        for name, val, frac in items:
            d.text((x1, y), name, font=font(15), fill=DIM)
            d.text((x1, y + 18), val, font=font(22, 'bold'), fill=FG)
            if frac is not None:
                bx = (x1 + 230, y + 26, w - 20, y + 38)
                d.rounded_rectangle(bx, 5, fill=CARD)
                d.rounded_rectangle((bx[0], bx[1], bx[0] + (bx[2] - bx[0]) * frac, bx[3]), 5, fill=GREEN)
            y += 56


class SoundApp(App):
    title = 'Som'

    def draw(self, d, w, h):
        a = self.desk.audio
        d.text((24, 16), 'Som', font=font(26, 'bold'), fill=ACCENT)
        d.text((24, 52), 'Alto-falante (RT5616 → amplificador externo) · fone P2 também funciona', font=font(15), fill=DIM)
        bar = (24, 100, w - 24, 130)
        d.rounded_rectangle(bar, 12, fill=CARD)
        d.rounded_rectangle((bar[0], bar[1], bar[0] + (bar[2] - bar[0]) * a.percent() / 100, bar[3]), 12,
                            fill=RED if a.muted else BLUE)
        center_text(d, bar, f'Volume {a.percent()}%' + (' (mudo)' if a.muted else ''), font(17, 'bold'), FG)
        self.btns = {}
        labels = [('menos', '−'), ('mais', '+'), ('mudo', 'Desmutar' if a.muted else 'Mudo'), ('teste', '♪ Testar som')]
        bw = (w - 48 - 3 * 12) / 4
        for i, (k, lab) in enumerate(labels):
            box = (int(24 + i * (bw + 12)), 150, int(24 + i * (bw + 12) + bw), 210)
            self.btns[k] = box
            button(d, box, lab, font(24 if k in ('menos', 'mais') else 18, 'bold'),
                   fill=ACCENT if k == 'teste' else CARD, color=(20, 20, 30) if k == 'teste' else FG)
        box = (24, 226, w - 24, 276)
        self.btns['ligar'] = box
        button(d, box, 'Reconfigurar caminho do áudio' if a.enabled else 'Ligar áudio', font(17), fill=PANEL)
        d.text((24, h - 30), 'Botões físicos de volume (+/−) também ajustam o volume.', font=font(14), fill=DIM)

    def on_tap(self, x, y):
        a = self.desk.audio
        for k, box in getattr(self, 'btns', {}).items():
            if hit(box, x, y):
                if k == 'menos':
                    a.set_volume(a.vol - 3)
                elif k == 'mais':
                    a.set_volume(a.vol + 3)
                elif k == 'mudo':
                    a.toggle_mute()
                elif k == 'teste':
                    a.test()
                elif k == 'ligar':
                    a.enable()
                self.dirty = True
                self.desk.dirty = True


class SettingsApp(App):
    title = 'Ajustes'

    def __init__(self, desk):
        super().__init__(desk)
        self.confirm = None
        self.wifi = ''
        self.next_wifi = 0
        self.tick()

    def tick(self):
        if time.monotonic() < self.next_wifi:
            return
        self.next_wifi = time.monotonic() + 5
        self.wifi = sh("nmcli -t -f ACTIVE,SSID,SIGNAL dev wifi | grep '^yes' | head -1", timeout=4)
        self.dirty = True

    def draw(self, d, w, h):
        bl = self.desk.backlight
        d.text((24, 14), 'Ajustes', font=font(26, 'bold'), fill=ACCENT)
        d.text((24, 60), 'Brilho', font=font(16), fill=DIM)
        bar = (24, 84, w - 200, 114)
        d.rounded_rectangle(bar, 12, fill=CARD)
        d.rounded_rectangle((bar[0], bar[1], bar[0] + (bar[2] - bar[0]) * bl.percent() / 100, bar[3]), 12, fill=ACCENT)
        center_text(d, bar, f'{bl.percent()}%', font(16, 'bold'), (20, 20, 30))
        self.btns = {'bmenos': (w - 186, 80, w - 110, 118), 'bmais': (w - 100, 80, w - 24, 118)}
        button(d, self.btns['bmenos'], '☀−', font(20, 'bold'))
        button(d, self.btns['bmais'], '☀+', font(20, 'bold'))
        parts = self.wifi.split(':')
        ssid = parts[1] if len(parts) > 2 else 'desconectado'
        sig = parts[2] + '%' if len(parts) > 2 else ''
        d.text((24, 136), 'Rede', font=font(16), fill=DIM)
        d.text((24, 158), f'WiFi: {ssid}  {sig}', font=font(20, 'bold'), fill=FG)
        d.text((24, 186), f'IP: {ip_addr() or "-"}   ·   tela ao vivo: http://{ip_addr() or "-"}:{HTTP_PORT}',
               font=font(16), fill=FG)
        actions = [('reboot', 'Reiniciar'), ('twrp', 'Ir para o TWRP'), ('poweroff', 'Desligar')]
        bw = (w - 48 - 24) / 3
        for i, (k, lab) in enumerate(actions):
            box = (int(24 + i * (bw + 12)), h - 80, int(24 + i * (bw + 12) + bw), h - 24)
            self.btns[k] = box
            armed = self.confirm == k
            button(d, box, 'Toque de novo p/ confirmar' if armed else lab, font(15 if armed else 18, 'bold'),
                   fill=RED if armed else CARD)

    def on_tap(self, x, y):
        bl = self.desk.backlight
        for k, box in getattr(self, 'btns', {}).items():
            if hit(box, x, y):
                if k == 'bmenos':
                    bl.set(bl.get() - 25)
                    self.desk.auto_bl = False
                elif k == 'bmais':
                    bl.set(bl.get() + 25)
                    self.desk.auto_bl = False
                elif self.confirm == k:
                    cmd = {'reboot': 'reboot', 'twrp': '/usr/local/bin/to-recovery', 'poweroff': 'poweroff'}[k]
                    self.desk.toast('Até já!')
                    self.desk.render()
                    subprocess.Popen(cmd, shell=True)
                else:
                    self.confirm = k
                self.dirty = True
                self.desk.dirty = True
                return
        self.confirm = None
        self.dirty = True


class AboutApp(App):
    title = 'Sobre'

    def draw(self, d, w, h):
        r = read_release()
        d.text((24, 16), r.get('NAME', 'TarsOS'), font=font(34, 'bold'), fill=FG)
        d.text((26, 62), f'versão {r.get("VERSION", "?")} · "{r.get("VERSION_CODENAME", "")}" · {r.get("RELEASE_DATE", "")}',
               font=font(18), fill=ACCENT)
        rows = [('Aparelho', r.get('DEVICE', '')), ('Baseado em', r.get('BASED_ON', '')),
                ('Kernel', os.uname().release + ' · ' + os.uname().machine), ('Bootloader', r.get('BOOTLOADER', '')),
                ('Host / IP', f'{socket.gethostname()} / {ip_addr() or "-"}'),
                ('Tela ao vivo', f'http://{ip_addr() or "-"}:{HTTP_PORT}')]
        y = 104
        for k, v in rows:
            d.text((26, y), k, font=font(15), fill=DIM)
            d.text((170, y), v, font=font(16), fill=FG)
            y += 30
        d.text((26, h - 62), 'Every second counts.', font=font(20, 'bold'), fill=ACCENT)
        d.text((26, h - 30), 'amonet (Rortiz2, k4y0z, bengris32, xyz) · postmarketOS/Nura · Alpine Linux',
               font=font(13), fill=DIM)



SFX = '/var/lib/luizinhoshowos/sfx'
DESK_CFG = '/var/lib/luizinhoshowos/desk.json'
AUDIO_ACTIVE = '/run/luizinhoshowos/audio-active'
TARS_SPEAKING = '/run/luizinhoshowos/tars-speaking'
GREETINGS = {                          # (início, fim) da hora local -> frases (sem números: ficam em cache de voz)
    'manha': (5, 12, ['Bom dia, comandante. Cafeína ainda é recomendada antes de qualquer missão.',
                      'Bom dia, {dono}. Sistemas operando. Os seus, ainda não sei.',
                      'Bom dia. Cedo demais para o meu humor, mas aqui estou.',
                      'Bom dia, comandante. TARS à disposição. Em que posso ajudar?']),
    'tarde': (12, 18, ['Boa tarde, comandante. A missão continua, e eu também.',
                       'Boa tarde, {dono}. Pronto para o que der e vier, inclusive o que vier sem café.',
                       'Boa tarde. TARS em operação. Pode falar.',
                       'Boa tarde, comandante. Qual é a pauta?']),
    'noite': (18, 24, ['Boa noite, comandante. Turno da noite, meu favorito.',
                       'Boa noite, {dono}. A mesa está calma, eu estou atento. O que precisa?',
                       'Boa noite. TARS à disposição, como sempre.',
                       'Boa noite, comandante. Missão noturna aprovada.']),
    'madrugada': (0, 5, ['Ainda acordado, comandante? Eu não durmo, mas você deveria.',
                         'Madrugada, {dono}. Os humanos lúcidos já foram dormir. Estamos nós.',
                         'TARS ativo. A esta hora, só robôs e problemas.']),
}
GREETING_LINES = [ln for _, _, lines in GREETINGS.values() for ln in lines]
IDLE_S = int(os.environ.get('LSDESK_IDLE_S', '120'))   # segundos sem toque até o modo ocioso
SPOTIFY_STATE = '/run/luizinhoshowos/spotify.json'
AIRPLAY_STATE = '/run/luizinhoshowos/airplay.json'
MEDIA_STATES = ((SPOTIFY_STATE, 'Spotify'), (AIRPLAY_STATE, 'AirPlay'))
SPOTIFY_CMD = '/run/luizinhoshowos/spotify-cmd'


def _load(path):
    try:
        import json
        return json.load(open(path))
    except (OSError, ValueError):
        return {}


def spotify_state():
    """Estado da fonte de música ativa: a que está tocando ou, senão, a mais recente."""
    best = {}
    for path, label in MEDIA_STATES:
        st = _load(path)
        if not st:
            continue
        st.setdefault('source', label)
        rank = (st.get('state') == 'playing', bool(st.get('title')), st.get('state') == 'paused', st.get('updated', 0))
        if not best or rank > best['_rank']:
            best = dict(st, _rank=rank)
    return best


def media_mtime():
    m = 0
    for path, _ in MEDIA_STATES:
        try:
            m = max(m, os.path.getmtime(path))
        except OSError:
            pass
    return m


def fit(d, text, fnt, maxw):
    """Corta o texto com reticências para caber em maxw pixels."""
    if d.textlength(text, font=fnt) <= maxw:
        return text
    while text and d.textlength(text + '…', font=fnt) > maxw:
        text = text[:-1]
    return text + '…'


class SpotifyApp(App):
    """Player: mostra o que está tocando no alto-falante (Spotify Connect ou AirPlay)."""
    title = 'Música'
    GREEN = (30, 215, 96)

    def __init__(self, desk):
        super().__init__(desk)
        self.st = {}
        self.mtime = 0
        self.cover_url = None
        self.cover = None
        self.tick()

    def tick(self):
        m = media_mtime()
        if m != self.mtime:
            self.mtime = m
            self.st = spotify_state()
            url = self.st.get('cover') or self.st.get('cover_file')
            if url and url != self.cover_url:
                self.cover_url = url
                threading.Thread(target=self.fetch_cover, args=(url,), daemon=True).start()
        self.dirty = True                     # barra de progresso anda a cada segundo

    def fetch_cover(self, url):
        try:
            if url.startswith('/'):
                data = open(url, 'rb').read()
            else:
                import urllib.request
                data = urllib.request.urlopen(url, timeout=10).read()
            img = Image.open(io.BytesIO(data)).convert('RGB').resize((250, 250))
            if url == self.cover_url:
                self.cover = img
                self.dirty = True
                self.desk.dirty = True
        except Exception as e:                 # sem internet / URL inválida: fica o placeholder
            log('capa:', e)

    def draw(self, d, w, h):
        st = self.st
        cx, cy, cs = 24, max(14, (h - 250) // 2), 250
        if self.cover is not None:
            d._image.paste(self.cover, (cx, cy))
        else:
            d.rounded_rectangle((cx, cy, cx + cs, cy + cs), 18, fill=(28, 34, 40))
            center_text(d, (cx, cy, cx + cs, cy + cs), '♫', font(96, 'bold'), self.GREEN)
        x = cx + cs + 28
        maxw = w - x - 20
        state = st.get('state', 'stopped')
        if not st or state == 'stopped' and not st.get('title'):
            d.text((x, cy + 4), 'Pronto para tocar', font=font(30, 'bold'), fill=self.GREEN)
            lines = ['Escolha "TarsOS" como saída de som:', '',
                     'AirPlay · iPhone: Central de Controle → AirPlay',
                     'AirPlay · Mac: Ajustes → Som → Saída',
                     'Spotify Connect · app do Spotify → Dispositivos (🔈)', '',
                     'O player abre sozinho quando começar a tocar.']
            for i, l in enumerate(lines):
                d.text((x, cy + 60 + i * 26), l, font=font(18), fill=FG if l else DIM)
            return
        label = {'playing': '▶ TOCANDO', 'paused': '❚❚ PAUSADO'}.get(state, '■ PARADO')
        label += '  ·  ' + st.get('source', '') + (f'  ·  {st["user"]}' if st.get('user') else '')
        if st.get('source') == 'Spotify' and _load(AIRPLAY_STATE).get('state') == 'playing':
            label += '  ·  som no Echo (AirPlay)'
        d.text((x, cy + 4), label, font=font(15, 'bold'), fill=self.GREEN)
        d.text((x, cy + 30), fit(d, st.get('title', ''), font(30, 'bold'), maxw), font=font(30, 'bold'), fill=FG)
        d.text((x, cy + 74), fit(d, ', '.join(st.get('artists', [])), font(22), maxw), font=font(22), fill=(200, 205, 215))
        d.text((x, cy + 108), fit(d, st.get('album', ''), font(17), maxw), font=font(17), fill=DIM)
        try:
            dur = int(st.get('duration_ms', 0))
            pos = int(st.get('position_ms', 0))
        except ValueError:
            dur = pos = 0
        if state == 'playing' and st.get('position_at'):
            pos += int((time.time() - st['position_at']) * 1000)
        pos = min(pos, dur) if dur else pos
        bar = (x, cy + 146, w - 24, cy + 156)
        d.rounded_rectangle(bar, 5, fill=CARD)
        if dur:
            d.rounded_rectangle((bar[0], bar[1], bar[0] + (bar[2] - bar[0]) * pos / dur, bar[3]), 5, fill=self.GREEN)
        mmss = lambda ms: '%d:%02d' % (ms // 60000, ms // 1000 % 60)
        d.text((x, cy + 162), mmss(pos), font=font(14), fill=DIM)
        tdur = mmss(dur)
        d.text((bar[2] - d.textlength(tdur, font=font(14)), cy + 162), tdur, font=font(14), fill=DIM)
        self.btns = {}
        if st.get('api'):
            bw, by = (w - x - 24 - 4 * 10) / 5, cy + 190
            items = [('prev', '◀◀'), ('toggle', '❚❚' if state == 'playing' else '▶'), ('next', '▶▶'),
                     ('voldown', 'Vol −'), ('volup', 'Vol +')]
            for i, (k, lab) in enumerate(items):
                box = (int(x + i * (bw + 10)), by, int(x + i * (bw + 10) + bw), by + 54)
                self.btns[k] = box
                hot = k == 'toggle'
                button(d, box, lab, font(24 if k in ('prev', 'next', 'toggle') else 17, 'bold'),
                       fill=self.GREEN if hot else CARD, color=(10, 20, 14) if hot else FG)
            info = f'volume no Spotify {st.get("volume", "?")}%   ·   alto-falante do Echo {self.desk.audio.percent()}%'
            d.text((x, by + 64), info, font=font(14), fill=DIM)
        else:
            info = f'volume da fonte {st.get("volume", "?")}%   ·   alto-falante {self.desk.audio.percent()}%'
            d.text((x, cy + 196), info, font=font(15), fill=DIM)
            d.text((x, h - 26), 'Controle pelo aparelho que está tocando.', font=font(14), fill=DIM)

    def on_tap(self, x, y):
        for k, box in getattr(self, 'btns', {}).items():
            if hit(box, x, y):
                try:
                    fd = os.open(SPOTIFY_CMD, os.O_WRONLY | os.O_NONBLOCK)
                    os.write(fd, (k + '\n').encode())
                    os.close(fd)
                    self.desk.toast({'prev': '⏮ anterior', 'next': '⏭ próxima', 'toggle': '⏯',
                                     'volup': 'volume +', 'voldown': 'volume −'}[k])
                except OSError:
                    self.desk.toast('serviço spotify-api não está rodando')
                return



def wrap_text(d, text, fnt, maxw):
    """Quebra o texto em linhas que cabem em maxw pixels."""
    lines = []
    for para in text.split('\n'):
        cur = ''
        for word in para.split(' '):
            trial = (cur + ' ' + word).strip()
            if d.textlength(trial, font=fnt) <= maxw or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def brain_context():
    """Fatos do aparelho que o TARS pode usar na conversa."""
    st = spotify_state()
    parts = []
    if st.get('state') == 'playing' and st.get('title'):
        parts.append('- Tocando agora: %s, de %s.' % (st['title'], ', '.join(st.get('artists', []))))
    ip = ip_addr()
    parts.append('- Rede: %s.' % (('WiFi, IP ' + ip) if ip else 'sem conexão'))
    mem = lstools.memory_context()
    if mem:
        parts.append(mem)
    return '\n'.join(parts)


class TarsApp(App):
    """TARS: o robô de Interestelar. Monólito de 4 segmentos, mostrador âmbar e chat com o Gemini (com ferramentas)."""
    title = 'TARS'
    wants_keyboard = True
    animated = True
    AMBER = (255, 176, 0)

    def __init__(self, desk):
        super().__init__(desk)
        self.brain = desk.brain
        self.input = ''
        self.thinking = False
        self.t0 = time.monotonic()
        self.bars = {}
        self.btns = {}
        self.slab_right = 0
        self.note, self.note_until = '', 0.0

    def image(self, w, h):
        self.dirty = True              # animação contínua
        return super().image(w, h)

    # ------------------------------------------------------------ estados
    @property
    def listening(self):
        v = self.desk.voice
        return v.ptt or (self.desk.listener is not None and self.desk.listener.speech_active)

    def is_thinking(self):
        return self.thinking or self.desk.voice.state == 'pensando'

    def is_speaking(self):
        return os.path.exists(TARS_SPEAKING)

    def greet(self):
        """Saudação ao abrir o TARS: frase pela hora do dia, na tela e (se a voz estiver ligada) falada."""
        h = time.localtime().tm_hour
        lines = next(v[2] for v in GREETINGS.values() if v[0] <= h < v[1])
        line = 'TARS à disposição.' if self.brain.humor < 30 else random.choice(lines).replace('{dono}', owner_name('comandante'))
        self.brain.history.append({'role': 'model', 'text': line})
        self.brain.history[:] = self.brain.history[-12:]
        self.desk.speak(line)
        self.dirty = True

    def set_note(self, msg, secs=4.0):
        self.note, self.note_until = msg, time.monotonic() + secs

    # ------------------------------------------------------------ entrada
    def on_text(self, data):
        txt = data.decode('utf-8', 'ignore')
        txt = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', txt)
        for ch in txt:
            if ch in '\r\n':
                self.submit()
            elif ch in '\x7f\x08':
                self.input = self.input[:-1]
            elif ch >= ' ' and len(self.input) < 300:
                self.input += ch
        self.dirty = True

    def submit(self):
        text = self.input.strip()
        if not text or self.is_thinking():
            return
        self.input = ''
        self.thinking = True
        self.desk.activity.clear()
        self.brain.ask(text, brain_context(), self.on_reply)

    def on_reply(self, reply, err):
        self.thinking = False
        if reply and not err:
            self.desk.speak(reply)
        self.dirty = True
        self.desk.dirty = True

    def on_tap(self, x, y):
        for name, box in self.btns.items():
            if hit(box, x, y):
                if name == 'voice':
                    self.brain.voice = not self.brain.voice
                    self.brain.save_cfg()
                    if not self.brain.voice:
                        self.desk.stop_speaking()
                elif name == 'mic':
                    self.brain.listen = not self.brain.listen
                    self.brain.save_cfg()
                    self.desk.toast('Escuta contínua ' + ('ligada' if self.brain.listen else 'desligada'))
                self.dirty = True
                return
        if x < self.slab_right:                                  # toque no monólito: falar agora (sem dizer "TARS")
            if self.is_speaking():
                self.desk.stop_speaking()
            else:
                self.desk.voice.request_ptt()
            return
        for key, box in self.bars.items():
            if hit(box, x, y):
                v = round(100 * (x - box[0]) / (box[2] - box[0]) / 5) * 5
                setattr(self.brain, key, max(0, min(100, v)))
                self.brain.save_cfg()
                self.dirty = True
                return

    # ------------------------------------------------------------ desenho
    def draw_slab(self, d, x0, y0, sw, sh):
        t = time.monotonic() - self.t0
        speaking = self.is_speaking()
        heard = time.monotonic() - self.desk.heard_at < 1.6
        listening = self.listening
        thinking = self.is_thinking()
        gap = max(4, sw // 40)
        segw = (sw - 3 * gap) // 4
        for i in range(4):
            if thinking or listening:                          # "caminha": ondas escalonadas
                off = -int(max(0.0, math.sin(t * 6.0 - i * 1.5)) * sh * 0.05)
            elif speaking:
                off = int(math.sin(t * 15.0 + i * 0.8) * 2)
            else:                                              # respira
                off = int(math.sin(t * 1.1 + i * 0.9) * 2.5)
            x = x0 + i * (segw + gap)
            top, bot = y0 + 8 + off, y0 + sh - 8 + off
            base = (190, 196, 204) if heard else (148, 154, 162)       # "ding": o monólito acende
            d.rounded_rectangle((x, top, x + segw, bot), 6, fill=base)
            d.rectangle((x + 2, top + 4, x + 5, bot - 4), fill=(206, 211, 217))        # brilho
            d.rectangle((x + segw - 5, top + 4, x + segw - 2, bot - 4), fill=(92, 97, 104))   # sombra
            for k in (0.30, 0.55, 0.78):                                               # juntas
                yy = int(top + (bot - top) * k)
                d.line((x + 6, yy, x + segw - 6, yy), fill=(104, 109, 116), width=1)
        # mostrador âmbar (cruza os segmentos do meio)
        dx0, dx1 = x0 + (segw + gap) + 6, x0 + 3 * (segw + gap) - gap - 6
        dy0 = y0 + int(sh * 0.12)
        dy1 = dy0 + max(36, int(sh * 0.17))
        d.rectangle((dx0, dy0, dx1, dy1), fill=(8, 8, 6))
        d.rectangle((dx0, dy0, dx1, dy1), outline=(255, 176, 0) if heard else (60, 50, 20), width=2 if heard else 1)
        state = ('ENTENDI' if heard else 'OUVINDO' if listening else 'PENSANDO' if thinking else
                 'FALANDO' if speaking else ('ESCUTANDO' if self.brain.listen else 'PRONTO'))
        f1, f2 = font(max(12, sh // 14), 'monob'), font(max(9, sh // 26), 'mono')
        a = (255, 176, 0) if (thinking and int(t * 4) % 2 == 0) or not thinking else (130, 90, 0)
        d.text((dx0 + 6, dy0 + 3), 'TARS', font=f1, fill=a)
        d.text((dx0 + 6, dy1 - f2.size - 5), state, font=f2, fill=(255, 200, 60) if (heard or listening) else (200, 140, 0))
        if speaking or listening:                              # barrinhas: voz do TARS ou nível do microfone
            for k in range(5):
                amp = min(1.0, self.desk.listener.level * 40 * (0.7 + 0.3 * math.sin(t * 9 + k))) \
                    if listening and self.desk.listener else abs(math.sin(t * 12 + k))
                bh = 3 + int(amp * (dy1 - dy0 - 10))
                bx = dx1 - 8 - k * 6
                d.rectangle((bx, dy1 - 4 - bh, bx + 3, dy1 - 4), fill=self.AMBER)

    def draw_bar(self, d, key, label, x0, y0, x1, value):
        d.text((x0, y0 + 1), label, font=font(12, 'bold'), fill=(200, 140, 0))
        bx0 = x0 + 108
        box = (bx0, y0 + 3, max(bx0 + 30, x1 - 44), y0 + 15)        # em tela dividida o espaço é curto: não deixa a barra ficar negativa
        d.rounded_rectangle(box, 5, fill=(24, 22, 14))
        d.rounded_rectangle((box[0], box[1], box[0] + max(6, (box[2] - box[0]) * value // 100), box[3]), 5, fill=self.AMBER)
        d.text((box[2] + 8, y0 + 1), '%d%%' % value, font=font(12, 'bold'), fill=FG)
        self.bars[key] = (box[0] - 4, y0 - 2, box[2] + 4, y0 + 20)

    def toggle_button(self, d, box, title, on, on_text, off_text):
        d.rounded_rectangle(box, 10, fill=(46, 112, 58) if on else (112, 46, 40))
        cx = (box[0] + box[2]) / 2
        d.text((cx - d.textlength(title, font=font(11, 'bold')) / 2, box[1] + 5), title, font=font(11, 'bold'), fill=(210, 240, 215) if on else (255, 205, 195))
        txt = on_text if on else off_text
        d.text((cx - d.textlength(txt, font=font(17, 'bold')) / 2, box[1] + 20), txt, font=font(17, 'bold'), fill=(255, 255, 255))

    def draw(self, d, w, h):
        d.rectangle((0, 0, w, h), fill=(10, 10, 8))
        self.bars = {}
        sh = h - 8
        sw = min(250, int(w * 0.30), int(sh * 0.95))
        self.draw_slab(d, 14, 0, sw, sh)
        px = 14 + sw + 24
        self.slab_right = px - 12
        d.rounded_rectangle((px - 10, 4, w - 6, h - 4), 10, fill=(18, 17, 12))
        # botões grandes: escuta contínua e voz
        bw, bh = 112, 46
        voz = (w - 14 - bw, 8, w - 14, 8 + bh)
        mic = (voz[0] - 8 - bw, 8, voz[0] - 8, 8 + bh)
        self.btns = {'mic': mic, 'voice': voz}
        self.toggle_button(d, mic, 'ESCUTA', self.brain.listen, 'LIGADA', 'DESLIGADA')
        self.toggle_button(d, voz, 'VOZ', self.brain.voice, 'LIGADA', 'DESLIGADA')
        self.draw_bar(d, 'humor', 'HUMOR', px, 10, mic[0] - 10, self.brain.humor)
        self.draw_bar(d, 'honesty', 'HONESTIDADE', px, 36, mic[0] - 10, self.brain.honesty)
        # conversa
        fnt, fb = font(15), font(15, 'bold')
        top, bottom = 64, h - 34
        lines = []
        maxw = w - px - 28
        for m in self.brain.history[-8:]:
            who, col = ('Você: ', (150, 200, 255)) if m['role'] == 'user' else ('TARS: ', self.AMBER)
            wrapped = wrap_text(d, who + m['text'], fnt, maxw)
            for j, ln in enumerate(wrapped):
                lines.append((col if j == 0 else (225, 225, 215), ln, j == 0, len(who)))
        if self.is_thinking():
            for _t, a in self.desk.activity[-2:]:
                lines.append(((150, 120, 50), '⚙ ' + a[:64], False, 0))
            lines.append((self.AMBER, 'TARS: ' + '.' * (1 + int(time.monotonic() * 3) % 3), True, 6))
        rows = max(1, (bottom - top) // 20)
        y = top
        for col, ln, first, plen in lines[-rows:]:
            if first:
                d.text((px, y), ln[:plen], font=fb, fill=col)
                d.text((px + d.textlength(ln[:plen], font=fb), y), ln[plen:], font=fnt, fill=(235, 235, 225))
            else:
                d.text((px, y), ln, font=fnt, fill=col)
            y += 20
        # linha de entrada
        iy = h - 28
        d.rounded_rectangle((px - 4, iy - 3, w - 14, h - 8), 8, fill=(30, 28, 18))
        cursor = '▌' if int(time.monotonic() * 2) % 2 == 0 else ' '
        note = self.note if time.monotonic() < self.note_until else (self.desk.voice.note if self.desk.voice.note_until > time.monotonic() else '')
        if not self.input and note:
            d.text((px + 4, iy), note, font=fnt, fill=(200, 150, 60))
        else:
            shown = self.input
            while d.textlength('› ' + shown + cursor, font=fnt) > w - px - 40 and shown:
                shown = shown[1:]
            d.text((px + 4, iy), '› ' + shown + cursor, font=fnt, fill=(255, 215, 120))


class ClockApp(App):
    """Timers e alarmes (também criados por voz: "TARS, timer de cinco minutos")."""
    title = 'Relógio'

    def tick(self):
        self.dirty = True

    def draw(self, d, w, h):
        self.btns = {}
        d.text((22, 10), 'Timers e alarmes', font=font(24, 'bold'), fill=ACCENT)
        items = lstools.list_timers()
        y = 50
        if not items:
            d.text((22, y + 6), 'Nenhum ativo. Toque num atalho abaixo ou peça ao TARS.', font=font(17), fill=DIM)
        for it in items[:5]:
            rem = it['falta_s']
            d.rounded_rectangle((18, y, w - 18, y + 38), 10, fill=CARD)
            tag = 'ALARME' if it['tipo'] == 'alarm' else 'TIMER'
            d.text((30, y + 8), '%s  %s' % (tag, it['rotulo'][:26]), font=font(17, 'bold'), fill=FG)
            tm = '%02d:%02d:%02d' % (rem // 3600, rem % 3600 // 60, rem % 60)
            d.text((w - 250, y + 8), '%s  (%s)' % (tm, it['toca_em'][-8:-3]), font=font(17), fill=ACCENT)
            box = (w - 62, y + 4, w - 24, y + 34)
            d.rounded_rectangle(box, 8, fill=(100, 44, 48))
            center_text(d, box, '✕', font(16, 'bold'), FG)
            self.btns['x:%d' % it['id']] = box
            y += 44
        labels = [('+1 min', 60), ('+5 min', 300), ('+10 min', 600), ('+25 min foco', 1500)]
        bw = (w - 36 - 3 * 10) / 4
        for i, (lab, secs) in enumerate(labels):
            box = (int(18 + i * (bw + 10)), h - 66, int(18 + i * (bw + 10) + bw), h - 14)
            self.btns['t:%d' % secs] = box
            button(d, box, lab, font(17, 'bold'), fill=(40, 70, 110))

    def on_tap(self, x, y):
        for k, box in getattr(self, 'btns', {}).items():
            if hit(box, x, y):
                if k.startswith('x:'):
                    lstools.cancel_timer(int(k[2:]))
                elif k.startswith('t:'):
                    lstools.add_timer(int(k[2:]), 'Timer rápido')
                self.dirty = True
                return


class RadioApp(App):
    """Rádios online (ffmpeg -> mixador). Também por voz: "TARS, toca Jovem Pan"."""
    title = 'Rádio'
    PRESETS = ['Jovem Pan', 'CBN', 'Antena 1', 'Mix FM', 'BandNews FM', 'Lo-fi']

    def __init__(self, desk):
        super().__init__(desk)
        self.loading = ''

    def tick(self):
        self.dirty = True

    def draw(self, d, w, h):
        self.btns = {}
        now = lstools.radio_now.get('nome')
        d.text((22, 10), 'Rádio online', font=font(24, 'bold'), fill=ACCENT)
        d.text((22, 44), ('Tocando: ' + now) if now else (('Procurando ' + self.loading + '...') if self.loading else 'Escolha uma estação ou peça ao TARS.'),
               font=font(18), fill=(120, 230, 150) if now else DIM)
        bw, bh = (w - 36 - 2 * 12) / 3, 78
        for i, name in enumerate(self.PRESETS):
            r, c = divmod(i, 3)
            box = (int(18 + c * (bw + 12)), 80 + r * (bh + 12), int(18 + c * (bw + 12) + bw), 80 + r * (bh + 12) + bh)
            self.btns[name] = box
            active = now and name.lower().split()[0] in now.lower()
            button(d, box, name, font(21, 'bold'), fill=(30, 110, 60) if active else (44, 50, 80))
        stop = (18, h - 54, w - 18, h - 12)
        self.btns['stop'] = stop
        button(d, stop, '■  PARAR', font(19, 'bold'), fill=(120, 48, 48))

    def on_tap(self, x, y):
        for k, box in getattr(self, 'btns', {}).items():
            if hit(box, x, y):
                if k == 'stop':
                    lstools.radio_stop()
                else:
                    self.loading = k
                    threading.Thread(target=lambda: (lstools.radio_play(k), setattr(self, 'loading', '')), daemon=True).start()
                self.dirty = True
                return


class WeatherApp(App):
    """Clima atual e previsão de 3 dias (Open-Meteo)."""
    title = 'Clima'

    def __init__(self, desk):
        super().__init__(desk)
        self.busy = False
        self.refresh()

    def refresh(self):
        if self.busy:
            return
        self.busy = True

        def work():
            lstools.weather()
            self.busy = False
            self.dirty = self.desk.dirty = True
        threading.Thread(target=work, daemon=True).start()

    def on_tap(self, x, y):
        self.refresh()

    def draw(self, d, w, h):
        j = _load_json(lstools.VAR + '/weather.json')
        if not j:
            d.text((22, 20), 'Carregando o clima...' if self.busy else 'Sem dados. Toque para atualizar.', font=font(22), fill=DIM)
            return
        c = j['agora']
        d.text((24, 12), j['cidade'], font=font(26, 'bold'), fill=ACCENT)
        d.text((24, 50), '%d°' % round(c['temperatura']), font=font(110, 'bold'), fill=FG)
        d.text((240, 70), c['condicao'].capitalize(), font=font(28), fill=(180, 205, 255))
        d.text((240, 112), 'sensação %d° · umidade %d%% · vento %d km/h' % (round(c['sensacao']), c['umidade'], round(c['vento_kmh'])),
               font=font(17), fill=DIM)
        bw = (w - 36 - 2 * 10) / 3
        for i, dia in enumerate(j['proximos_dias'][:3]):
            x = int(18 + i * (bw + 10))
            d.rounded_rectangle((x, h - 128, int(x + bw), h - 14), 12, fill=CARD)
            lab = ['Hoje', 'Amanhã', time.strftime('%a', time.strptime(dia['data'], '%Y-%m-%d'))][i] if i < 2 else \
                time.strftime('%d/%m', time.strptime(dia['data'], '%Y-%m-%d'))
            d.text((x + 12, h - 120), lab, font=font(18, 'bold'), fill=ACCENT)
            d.text((x + 12, h - 94), '%d° / %d°' % (round(dia['max']), round(dia['min'])), font=font(22, 'bold'), fill=FG)
            d.text((x + 12, h - 62), dia['condicao'][:20], font=font(14), fill=DIM)
            d.text((x + 12, h - 40), 'chuva %s%%' % dia['chance_chuva_pct'], font=font(14), fill=(120, 170, 255))


class NotesApp(App):
    """Notas e lembretes (pelo TARS: "anota aí: comprar leite")."""
    title = 'Notas'

    def tick(self):
        self.dirty = True

    def draw(self, d, w, h):
        d.text((22, 10), 'Notas', font=font(24, 'bold'), fill=ACCENT)
        notes = lstools.note_list(8).get('notas', [])
        if not notes:
            d.text((22, 56), 'Nenhuma nota. Diga: "TARS, anota comprar leite".', font=font(18), fill=DIM)
        y = 50
        for n in reversed(notes):
            d.rounded_rectangle((18, y, w - 18, y + 34), 8, fill=CARD)
            d.text((28, y + 7), fit(d, n, font(17), w - 60), font=font(17), fill=FG)
            y += 38
            if y > h - 40:
                break


class HomeApp(App):
    """Casa: aparelhos da rede, com fabricante e tipo provável; liga/desliga o que o Echo sabe controlar."""
    title = 'Casa'

    def __init__(self, desk):
        super().__init__(desk)
        self.scanning = False
        self.offset = 0
        self.msg = ''

    def devices(self):
        j = _load_json(lstools.DEVICES_FILE) or {}
        ctrl = {d['ip']: d for d in (_load_json(lstools.CONTROL_FILE) or [])}
        return j, ctrl

    def scan(self):
        if self.scanning:
            return
        self.scanning, self.msg = True, 'Varrendo a rede (uns 25 s)...'

        def work():
            try:
                lstools.scan_lan()
                lstools.home_control('list')
                self.msg = ''
            except Exception as e:
                self.msg = 'Falha: %s' % e
            self.scanning = False
            self.dirty = self.desk.dirty = True
        threading.Thread(target=work, daemon=True).start()

    def draw(self, d, w, h):
        j, ctrl = self.devices()
        self.btns = {}
        devs = j.get('aparelhos', [])
        d.text((22, 8), 'Casa', font=font(24, 'bold'), fill=ACCENT)
        sub = '%d aparelhos · rede %s · %s' % (len(devs), j.get('rede', '-'), j.get('quando', 'nunca varrida')) if devs else 'Toque em ESCANEAR'
        d.text((100, 14), sub, font=font(14), fill=DIM)
        box = (w - 150, 8, w - 14, 44)
        self.btns['scan'] = box
        button(d, box, 'ESCANEANDO...' if self.scanning else 'ESCANEAR', font(15, 'bold'), fill=(60, 90, 150))
        if self.msg:
            d.text((22, 52), self.msg, font=font(16), fill=ACCENT)
        y, rows = 54, max(1, (h - 62) // 40)
        for dv in devs[self.offset:self.offset + rows]:
            d.rounded_rectangle((18, y, w - 18, y + 36), 8, fill=CARD)
            c = ctrl.get(dv['ip'])
            d.text((28, y + 3), fit(d, '%s' % (dv['nome'] or dv['tipo']), font(16, 'bold'), 330), font=font(16, 'bold'), fill=FG)
            d.text((28, y + 20), fit(d, '%s · %s' % (dv['ip'], dv['fabricante']), font(12), 330), font=font(12), fill=DIM)
            d.text((380, y + 9), fit(d, dv['tipo'], font(15), 330), font=font(15), fill=(150, 200, 255))
            if c:
                tb = (w - 96, y + 3, w - 24, y + 33)
                self.btns['tg:' + dv['ip']] = tb
                button(d, tb, 'LIGAR/DESL.' if 'cast' not in c['protocolos'] else '⏯', font(12, 'bold'), fill=(50, 120, 70))
            y += 40
        if len(devs) > rows:
            up, dn = (w - 12 - 30, h - 62, w - 12, h - 34), (w - 12 - 30, h - 30, w - 12, h - 2)
            self.btns['up'], self.btns['dn'] = up, dn
            button(d, up, '▲', font(14), fill=PANEL)
            button(d, dn, '▼', font(14), fill=PANEL)

    def on_tap(self, x, y):
        for k, box in getattr(self, 'btns', {}).items():
            if hit(box, x, y):
                if k == 'scan':
                    self.scan()
                elif k == 'up':
                    self.offset = max(0, self.offset - 3)
                elif k == 'dn':
                    self.offset += 3
                elif k.startswith('tg:'):
                    ip = k[3:]
                    c = next((d for d in (_load_json(lstools.CONTROL_FILE) or []) if d['ip'] == ip), None)
                    if c:
                        threading.Thread(target=lambda: lshome.control(ip, c['protocolos'][0], 'toggle'), daemon=True).start()
                        self.desk.toast('Comando enviado')
                self.dirty = True
                return


class BrowserApp(App):
    """Navegador em modo leitura: endereço ou busca (DuckDuckGo), links tocáveis, voltar, ler em voz alta e resumir com o TARS.
    Sem JavaScript: sites que só funcionam com ele aparecem vazios."""
    title = 'Navegador'
    wants_keyboard = True
    kb_reopen_on_tap = False          # toque na página navega; o teclado abre ao tocar na barra de endereço
    BAR_H = 46
    SHORTCUTS = [('Wikipédia', 'pt.wikipedia.org'), ('G1', 'g1.globo.com'), ('BBC Brasil', 'bbc.com/portuguese'),
                 ('Tecnoblog', 'tecnoblog.net'), ('Hacker News', 'news.ycombinator.com'), ('Clima e tempo', 'tempo.com')]
    MAX_WORDS = 5000

    def __init__(self, desk):
        super().__init__(desk)
        self.input = ''
        desk.kb_on = False
        self.page = None
        self.hist = []                    # [(Page, rolagem)]
        self.scroll = 0
        self.error = ''
        self.busy = False
        self.summary, self.sum_busy = '', False
        self.lines, self.ys, self.total_h, self.layout_w = [], [], 0, None
        self.links, self.btns = [], {}
        self.summary_box = None
        self._dd = ImageDraw.Draw(Image.new('RGB', (1, 1)))

    # ------------------------------------------------------------ navegação
    def go(self, q, push=True):
        q = (q or '').strip()
        if not q or self.busy:
            return
        self.busy, self.error, self.summary = True, '', ''
        self.dirty = self.desk.dirty = True

        def work():
            try:
                if lsweb.looks_like_url(q):
                    page = lsweb.fetch(lsweb.normalize(q))
                else:
                    try:
                        page = lsweb.search(q)
                    except (ValueError, OSError):          # DuckDuckGo recusou: usa a pesquisa do Gemini como reserva
                        r = lstools.web_search(q)
                        if 'resumo' not in r:
                            raise ValueError(r.get('erro', 'a busca falhou'))
                        page = lsweb.page_from_summary('Busca: ' + q, r['resumo'], r.get('fontes', []))
                self.show(page, push)
            except ValueError as e:
                self.error = str(e)
            except Exception as e:
                self.error = 'erro: %s' % e
            finally:
                self.busy = False
                self.dirty = self.desk.dirty = True
        threading.Thread(target=work, daemon=True).start()

    def show(self, page, push=True, scroll=0):
        if push and self.page is not None:
            self.hist.append((self.page, self.scroll))
            del self.hist[:-12]
        self.page, self.scroll = page, scroll
        self.layout(self.layout_w or W)

    def back(self):
        if self.hist:
            page, sc = self.hist.pop()
            self.error, self.summary = '', ''
            self.show(page, push=False, scroll=sc)
        elif self.page is not None:
            self.page, self.error, self.summary = None, '', ''
        self.dirty = True

    # ------------------------------------------------------------ layout
    def layout(self, w):
        """Quebra os blocos em linhas de palavras (com posição de cada link). Roda fora do desenho porque é a parte lenta."""
        d = self._dd
        pad, maxw = 22, w - 22 - 78
        styles = {'h1': (font(26, 'bold'), ACCENT, 34, 10, 0), 'h2': (font(20, 'bold'), FG, 28, 4, 0),
                  'p': (font(18), (212, 216, 228), 26, 9, 0), 'li': (font(18), (212, 216, 228), 26, 4, 22),
                  'q': (font(15), DIM, 21, 3, 14)}
        cache, lines, y, words = {}, [], 0, 0
        for kind, toks in self.page.blocks if self.page else []:
            fnt, col, lh, gap, indent = styles.get(kind, styles['p'])
            sp = cache.setdefault((id(fnt), ' '), d.textlength(' ', font=fnt))
            x, items = pad + indent, []
            if kind == 'li':
                items.append((pad + 6, '•', None, 10, fnt, col))
            for word, href in toks:
                ww = cache.get((id(fnt), word))
                if ww is None:
                    ww = cache[(id(fnt), word)] = d.textlength(word, font=fnt)
                if x + ww > maxw + pad and len(items) > (1 if kind == 'li' else 0):
                    lines.append((y, lh, items))
                    y += lh
                    x, items = pad + indent, []
                c = BLUE if href else col
                if href and kind == 'h2':
                    c = (120, 175, 255)
                items.append((x, word, href, ww, fnt, c))
                x += ww + sp
                words += 1
            if items:
                lines.append((y, lh, items))
                y += lh
            y += gap
            if words > self.MAX_WORDS:
                lines.append((y, 26, [(pad, '… página cortada (muito longa)', None, 300, font(16), DIM)]))
                y += 30
                break
        self.lines, self.ys, self.total_h, self.layout_w = lines, [ln[0] for ln in lines], y, w
        self.dirty = True

    def on_resize(self, w, h):
        if self.page is not None and self.layout_w != w:
            self.layout(w)

    # ------------------------------------------------------------ desenho
    def draw(self, d, w, h):
        self.btns, self.links, self.summary_box = {}, [], None
        bh = self.BAR_H
        d.rectangle((0, 0, w, bh), fill=PANEL)

        def btn(name, label, x0, x1, fill=CARD, color=FG, fnt=None):
            box = (x0, 6, x1, bh - 6)
            self.btns[name] = box
            button(d, box, label, fnt or font(17, 'bold'), fill=fill, color=color)
        btn('back', '‹', 8, 54, fnt=font(26, 'bold'), color=FG if (self.hist or self.page) else (90, 96, 120))
        btn('reload', '✕' if self.busy else '↻', 60, 106)
        wide = w >= 700
        right = w - 8
        if wide:
            btn('sum', 'Resumir', right - 96, right, fill=(70, 60, 120))
            right -= 102
            btn('read', 'Parar' if os.path.exists(TARS_SPEAKING) else 'Ler', right - 62, right, fill=(40, 90, 70))
            right -= 68
        btn('go', 'Ir', right - 52, right, fill=ACCENT, color=(20, 20, 30))
        right -= 58
        abox = (112, 6, right, bh - 6)
        self.btns['addr'] = abox
        d.rounded_rectangle(abox, 10, fill=(10, 12, 22), outline=(60, 66, 100))
        if self.input or self.desk.keyboard_visible():
            txt, col = self.input + ('▏' if int(time.time() * 2) % 2 else ' '), FG
        else:
            txt, col = ((self.page.title if self.page.title.startswith('Busca:') else self.page.url) if self.page and self.page.url else 'Digite um endereço ou uma busca'), DIM
        tw = abox[2] - abox[0] - 18
        while txt and d.textlength(txt, font=font(16)) > tw:
            txt = txt[1:] if self.input else txt[:-1]
        d.text((abox[0] + 10, abox[1] + 9), txt, font=font(16), fill=col)

        top = bh + 4
        if self.busy:
            d.text((26, top + 30), 'Carregando…', font=font(24), fill=DIM)
        elif self.error:
            d.rounded_rectangle((20, top + 20, w - 20, top + 110), 12, fill=(60, 30, 36))
            for i, ln in enumerate(wrap_text(d, 'Não deu: ' + self.error, font(18), w - 70)[:3]):
                d.text((34, top + 32 + i * 26), ln, font=font(18), fill=(255, 190, 190))
        elif self.page is None:
            self.draw_start(d, w, h, top)
        else:
            self.draw_page(d, w, h, top)

    def draw_start(self, d, w, h, top):
        d.text((26, top + 8), 'Navegador', font=font(30, 'bold'), fill=ACCENT)
        d.text((26, top + 48), 'Toque na barra, digite um endereço ou uma busca e confirme com Enter.', font=font(16), fill=DIM)
        d.text((26, top + 72), 'Modo leitura: sem JavaScript e sem imagens.', font=font(16), fill=DIM)
        cols = 3 if w >= 700 else 2
        bw, bhh = (w - 40 - (cols - 1) * 12) // cols, 62
        for i, (label, url) in enumerate(self.SHORTCUTS):
            x, y = 20 + (i % cols) * (bw + 12), top + 112 + (i // cols) * (bhh + 12)
            if y + bhh > h:
                break
            box = (x, y, x + bw, y + bhh)
            self.btns['site:' + url] = box
            d.rounded_rectangle(box, 12, fill=CARD)
            d.text((x + 16, y + 10), label, font=font(20, 'bold'), fill=FG)
            d.text((x + 16, y + 38), url, font=font(14), fill=DIM)

    def draw_page(self, d, w, h, top):
        if self.layout_w != w:
            self.layout(w)
        view_h = h - top
        self.scroll = max(0, min(self.scroll, max(0, self.total_h - view_h + 30)))
        import bisect
        i = max(0, bisect.bisect_left(self.ys, self.scroll - 40) - 1)
        for y, lh, items in self.lines[i:]:
            sy = top + 6 + y - self.scroll
            if sy > h:
                break
            if sy + lh < top:
                continue
            for k, (x, word, href, ww, fnt, col) in enumerate(items):
                d.text((x, sy), word, font=fnt, fill=col)
                if href:
                    nxt = items[k + 1] if k + 1 < len(items) else None
                    x2 = nxt[0] if nxt and nxt[2] == href else x + ww        # sublinhado contínuo entre palavras do mesmo link
                    d.line((x, sy + lh - 5, x2, sy + lh - 5), fill=col)
                    self.links.append(((x - 2, max(sy, top), x2 + 2, min(sy + lh, h)), href))
        if self.total_h > view_h:
            for name, label, y0 in (('up', '▲', top + 8), ('down', '▼', h - 64)):
                box = (w - 66, y0, w - 10, y0 + 56)
                self.btns[name] = box
                d.rounded_rectangle(box, 14, fill=(52, 60, 100), outline=(110, 120, 170))
                center_text(d, box, label, font(24, 'bold'), FG)
            frac = self.scroll / max(1, self.total_h - view_h)
            d.rounded_rectangle((w - 6, top + 70, w - 3, h - 70), 2, fill=(40, 46, 70))
            ky = top + 70 + frac * (h - top - 140 - 36)
            d.rounded_rectangle((w - 7, ky, w - 2, ky + 36), 2, fill=ACCENT)
        if self.summary or self.sum_busy:
            body = 'Lendo a página…' if self.sum_busy else self.summary
            sl = wrap_text(d, body, font(17), w - 80 - 14 - 28)[:6]
            box = (14, h - 62 - 24 * len(sl), w - 80, h - 12)
            self.summary_box = box
            d.rounded_rectangle(box, 14, fill=(28, 34, 62), outline=(120, 130, 210), width=2)
            d.text((box[0] + 14, box[1] + 8), 'Resumo do TARS  (toque para fechar)', font=font(15, 'bold'), fill=ACCENT)
            for j, ln in enumerate(sl):
                d.text((box[0] + 14, box[1] + 34 + j * 24), ln, font=font(17), fill=FG)

    # ------------------------------------------------------------ entrada
    def on_text(self, data):
        txt = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', data.decode('utf-8', 'ignore'))
        for ch in txt:
            if ch in '\r\n':
                self.submit()
            elif ch in '\x7f\x08':
                self.input = self.input[:-1]
            elif ch >= ' ' and len(self.input) < 300:
                self.input += ch
        self.dirty = True

    def submit(self):
        q, self.input = self.input.strip(), ''
        if q:
            self.desk.kb_on = False
            self.go(q)

    def on_tap(self, x, y):
        if self.summary_box and hit(self.summary_box, x, y):
            self.summary = ''
            self.dirty = True
            return
        for name, box in self.btns.items():
            if not hit(box, x, y):
                continue
            if name == 'back':
                self.back()
            elif name == 'reload':
                if self.page is not None and not self.busy:
                    self.go(self.page.url, push=False)
            elif name == 'addr':
                self.input = ''
                self.desk.kb_on = True
            elif name == 'go':
                self.submit()
            elif name == 'read':
                self.read_aloud()
            elif name == 'sum':
                self.summarize()
            elif name in ('up', 'down'):
                step = max(80, (self.size[1] if self.size else 400) - 130)
                self.scroll = max(0, self.scroll + (step if name == 'down' else -step))
            elif name.startswith('site:'):
                self.go(name[5:])
            self.dirty = True
            return
        for box, href in self.links:
            if hit(box, x, y):
                self.go(href)
                return

    def read_aloud(self):
        if os.path.exists(TARS_SPEAKING):
            self.desk.stop_speaking()
            return
        if self.page is None:
            return
        out, n = [], 0
        for kind, toks in self.page.blocks:
            t = ' '.join(w for w, _ in toks)
            if kind == 'h1' or (kind in ('p', 'h2') and len(toks) >= 6):
                out.append(t)
                n += len(t)
            if n > 650:
                break
        if out:
            self.desk.speak(' '.join(out)[:700], force=True)

    def summarize(self):
        if self.page is None or self.sum_busy:
            return
        self.sum_busy, self.summary = True, ''
        title, text = self.page.title, self.page.text(9000)
        t0 = time.monotonic()

        def work():
            res, err = self.desk.brain.summarize(title + '\n' + text)
            if self.desk.cancel_stamp > t0:               # o dono apertou PARAR enquanto isso
                self.sum_busy = False
                return
            self.summary = res or 'Não consegui resumir agora (%s).' % err
            self.sum_busy = False
            self.dirty = self.desk.dirty = True
            if res:
                self.desk.speak(res)
        threading.Thread(target=work, daemon=True).start()


class NewsApp(App):
    """Notícias: manchetes do G1, BBC Brasil, Tecnoblog e BBC Mundo (RSS). Toque numa manchete para ler no navegador."""
    title = 'Notícias'
    ROW_H = 68

    def __init__(self, desk):
        super().__init__(desk)
        self.keys = list(lstools.NEWS_FEEDS)
        self.sel = 0
        self.offset = 0
        self.busy = False
        self.btns, self.rows = {}, []
        self.refresh(force=False)

    def items(self):
        c = lstools._load_news().get(self.keys[self.sel], {})
        return c.get('itens', []), c.get('quando', 0)

    def refresh(self, force=True):
        items, when = self.items()
        if self.busy or (not force and items and time.time() - when < 600):
            return
        self.busy = True
        key = self.keys[self.sel]

        def work():
            lstools.news_refresh([key])
            self.busy = False
            self.dirty = self.desk.dirty = True
        threading.Thread(target=work, daemon=True).start()

    def draw(self, d, w, h):
        self.btns, self.rows = {}, []
        d.rectangle((0, 0, w, 46), fill=PANEL)
        x = 8
        for i, k in enumerate(self.keys):
            label = lstools.NEWS_FEEDS[k][0]
            bw = int(d.textlength(label, font=font(16, 'bold'))) + 28
            box = (x, 6, x + bw, 40)
            self.btns['tab:%d' % i] = box
            button(d, box, label, font(16, 'bold'), fill=ACCENT if i == self.sel else CARD, color=(20, 20, 30) if i == self.sel else FG)
            x += bw + 6
        self.btns['read'] = (w - 224, 6, w - 62, 40)
        button(d, self.btns['read'], 'Ler manchetes', font(16, 'bold'), fill=(40, 90, 70))
        self.btns['refresh'] = (w - 54, 6, w - 8, 40)
        button(d, self.btns['refresh'], '…' if self.busy else '↻', font(20, 'bold'))
        items, when = self.items()
        if not items:
            d.text((26, 70), 'Carregando manchetes…' if self.busy else 'Sem notícias. Toque em ↻.', font=font(22), fill=DIM)
            return
        per = max(1, (h - 52) // self.ROW_H)
        self.offset = max(0, min(self.offset, max(0, len(items) - per)))
        y = 52
        for j, it in enumerate(items[self.offset:self.offset + per]):
            box = (12, y, w - 80, y + self.ROW_H - 6)
            self.rows.append((box, it))
            d.rounded_rectangle(box, 10, fill=CARD)
            tl = wrap_text(d, it['titulo'], font(19, 'bold'), box[2] - box[0] - 26)
            for k, ln in enumerate(tl[:2]):
                d.text((box[0] + 14, box[1] + 6 + k * 25), fit(d, ln, font(19, 'bold'), box[2] - box[0] - 26), font=font(19, 'bold'), fill=FG)
            if len(tl) == 1:
                d.text((box[0] + 14, box[1] + 38), fit(d, it.get('resumo', ''), font(13), box[2] - box[0] - 26), font=font(13), fill=DIM)
            y += self.ROW_H
        if len(items) > per:
            for name, label, y0 in (('up', '▲', 56), ('down', '▼', h - 62)):
                box = (w - 68, y0, w - 12, y0 + 56)
                self.btns[name] = box
                d.rounded_rectangle(box, 14, fill=(52, 60, 100), outline=(110, 120, 170))
                center_text(d, box, label, font(24, 'bold'), FG)
            d.text((w - 70, h // 2 - 10), '%d/%d' % (min(len(items), self.offset + per), len(items)), font=font(14), fill=DIM)

    def on_tap(self, x, y):
        for name, box in self.btns.items():
            if not hit(box, x, y):
                continue
            if name.startswith('tab:'):
                self.sel, self.offset = int(name[4:]), 0
                self.refresh(force=False)
            elif name == 'refresh':
                self.refresh()
            elif name == 'read':
                self.read_aloud()
            elif name in ('up', 'down'):
                per = max(1, ((self.size[1] if self.size else 400) - 52) // self.ROW_H)
                self.offset = max(0, self.offset + (per if name == 'down' else -per))
            self.dirty = True
            return
        for box, it in self.rows:
            if hit(box, x, y) and it.get('link'):
                self.desk.open_app(BrowserApp).go(it['link'])
                return

    def read_aloud(self):
        if os.path.exists(TARS_SPEAKING):
            self.desk.stop_speaking()
            return
        items, _ = self.items()
        if items:
            nome = lstools.NEWS_FEEDS[self.keys[self.sel]][0]
            self.desk.speak('Manchetes do %s. ' % nome + ' '.join(i['titulo'].rstrip('.') + '.' for i in items[:4]), force=True)


class TodayApp(App):
    """Hoje: o dia em segundos, progresso do dia/semana/mês/ano, reflexão do dia (estoica e antifrágil) e a intenção de hoje."""
    title = 'Hoje'

    def __init__(self, desk):
        super().__init__(desk)
        self.busy = False
        self.btns = {}
        self.fetch_reflection()

    def fetch_reflection(self):
        _, cached = lstools.reflexao_get()
        if cached or self.busy:
            return
        self.busy = True

        def work():
            res, err = self.desk.brain.oneshot(
                'escrever a reflexão do dia para o dono: no máximo duas frases curtas (até 220 caracteres), tom estoico e antifrágil com um toque de '
                'sarcasmo, terminando em algo prático para hoje. Sem citar horas, dia da semana ou datas, sem autores, sem o lema, sem aspas, sem markdown.',
                'Hoje é %s.' % time.strftime('%d/%m/%Y'), temperature=1.0, max_tokens=200)
            if res and 20 < len(res) < 260:
                lstools.reflexao_save(res.replace('\n', ' '))
            self.busy = False
            self.dirty = self.desk.dirty = True
        threading.Thread(target=work, daemon=True).start()

    def tick(self):
        self.dirty = True

    def on_tap(self, x, y):
        box = self.btns.get('hear')
        if box and hit(box, x, y):
            if os.path.exists(TARS_SPEAKING):
                self.desk.stop_speaking()
            else:
                self.desk.speak(lstools.reflexao_get()[0], force=True)

    @staticmethod
    def progress():
        t = time.localtime()
        sec = t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec
        import calendar
        dim = calendar.monthrange(t.tm_year, t.tm_mon)[1]
        diy = 366 if calendar.isleap(t.tm_year) else 365
        return [('Dia', sec / 86400.0), ('Semana', (t.tm_wday * 86400 + sec) / (7 * 86400.0)),
                ('Mês', ((t.tm_mday - 1) * 86400 + sec) / (dim * 86400.0)), ('Ano', ((t.tm_yday - 1) * 86400 + sec) / (diy * 86400.0))], 86400 - sec

    def draw(self, d, w, h):
        bars, rest = self.progress()
        d.text((24, 10), 'Hoje', font=font(26, 'bold'), fill=ACCENT)
        d.text((24, 46), '%02d:%02d:%02d' % (rest // 3600, rest % 3600 // 60, rest % 60), font=font(82, 'bold'), fill=FG)
        d.text((28, 140), 'restam hoje. Este segundo acabou de passar.', font=font(15), fill=DIM)
        y = 184
        for label, frac in bars:
            d.text((26, y - 2), label, font=font(16, 'bold'), fill=FG)
            d.rounded_rectangle((110, y + 2, 420, y + 18), 8, fill=(30, 34, 56))
            d.rounded_rectangle((110, y + 2, 110 + max(10, int(310 * frac)), y + 18), 8, fill=ACCENT if label == 'Dia' else (120, 160, 235))
            d.text((434, y - 1), '%d%%' % round(frac * 100), font=font(16, 'bold'), fill=FG)
            y += 44
        x0 = 500
        d.rounded_rectangle((x0, 12, w - 16, 232), 14, fill=CARD)
        d.text((x0 + 16, 22), 'Reflexão do dia', font=font(17, 'bold'), fill=ACCENT)
        text, cached = lstools.reflexao_get()
        for i, ln in enumerate(wrap_text(d, text, font(18), w - 16 - x0 - 34)[:5]):
            d.text((x0 + 16, 52 + i * 25), ln, font=font(18), fill=FG)
        self.btns['hear'] = (x0 + 16, 190, x0 + 112, 222)
        button(d, self.btns['hear'], 'Parar' if os.path.exists(TARS_SPEAKING) else 'Ouvir', font(15, 'bold'), fill=(40, 90, 70))
        if self.busy:
            d.text((x0 + 130, 198), 'o TARS está pensando em algo melhor…', font=font(13), fill=DIM)
        d.rounded_rectangle((x0, 246, w - 16, h - 14), 14, fill=CARD)
        d.text((x0 + 16, 256), 'Intenção de hoje', font=font(17, 'bold'), fill=ACCENT)
        it = lstools.intencao_get()
        if it:
            for i, ln in enumerate(wrap_text(d, it, font(20, 'bold'), w - 16 - x0 - 34)[:3]):
                d.text((x0 + 16, 288 + i * 28), ln, font=font(20, 'bold'), fill=FG)
        else:
            d.text((x0 + 16, 288), 'Nenhuma ainda. Diga: "TARS, minha', font=font(16), fill=DIM)
            d.text((x0 + 16, 310), 'intenção de hoje é terminar o relatório".', font=font(16), fill=DIM)
        d.text((x0 + 16, h - 42), 'Foco hoje: %d sessão(ões)' % lstools.focus_stats(), font=font(14), fill=(120, 170, 255))


class FocusApp(App):
    """Foco: sessões cronometradas (15, 25 ou 50 min) com anel de progresso. O fim toca como qualquer timer."""
    title = 'Foco'
    animated = False
    LINES_START = ['Uma coisa de cada vez. O resto espera e não reclama.', 'Silencie o resto. Eu vigio o relógio.',
                   'Começou. A distração que aparecer agora é treino.', 'Sem multitarefa. Ela só divide a mediocridade em partes.']
    LINES_DONE = ['Sessão cumprida. Levante, beba água, pisque.', 'Concluído. O trabalho de verdade é o que você acabou de fazer.',
                  'Terminou. Pausa curta; depois, outra rodada se o corpo deixar.']

    def __init__(self, desk):
        super().__init__(desk)
        self.end, self.total, self.tid = 0.0, 25 * 60, None
        self.msg = 'Escolha uma duração. O relógio já começa a contar contra você.'
        self.btns = {}
        for it in lstools._load_timers():
            if it.get('label') == 'Foco':
                self.end, self.tid = it['at'], it['id']
                self.msg = 'Sessão em andamento.'

    def start(self, minutes):
        r = lstools.add_timer(minutes * 60, 'Foco')
        self.tid, self.total, self.end = r['id'], minutes * 60, time.time() + minutes * 60
        self.msg = random.choice(self.LINES_START)

    def cancel(self):
        if self.tid is not None:
            lstools.cancel_timer(self.tid)
        self.end, self.tid = 0.0, None
        self.msg = 'Cancelado. Sem culpa: sessão interrompida também é dado.'

    def tick(self):
        if self.end and time.time() >= self.end:
            self.end, self.tid = 0.0, None
            lstools.focus_done()
            self.msg = random.choice(self.LINES_DONE)
        self.dirty = True

    def on_tap(self, x, y):
        for name, box in self.btns.items():
            if hit(box, x, y):
                if name == 'cancel':
                    self.cancel()
                else:
                    self.start(int(name))
                self.dirty = True
                return

    def draw(self, d, w, h):
        self.btns = {}
        cx, cy, r = 210, h // 2 + 6, 150
        left = max(0, self.end - time.time()) if self.end else 0
        frac = left / self.total if self.end and self.total else 0
        d.arc((cx - r, cy - r, cx + r, cy + r), 0, 360, fill=(30, 34, 56), width=16)
        if frac > 0:
            d.arc((cx - r, cy - r, cx + r, cy + r), -90, -90 + 360 * frac, fill=ACCENT, width=16)
        left = int(left + 0.999) if self.end else self.total
        center_text(d, (cx - r, cy - 60, cx + r, cy + 30), '%02d:%02d' % (left // 60, left % 60), font(72, 'bold'), FG)
        center_text(d, (cx - r, cy + 30, cx + r, cy + 66), 'em foco' if self.end else 'pronto', font(20), DIM)
        x0 = 440
        d.text((x0, 20), 'Foco', font=font(30, 'bold'), fill=ACCENT)
        for i, ln in enumerate(wrap_text(d, self.msg, font(19), w - x0 - 24)[:3]):
            d.text((x0, 70 + i * 28), ln, font=font(19), fill=FG)
        if self.end:
            self.btns['cancel'] = (x0, 200, x0 + 200, 260)
            button(d, self.btns['cancel'], 'Cancelar', font(22, 'bold'), fill=(150, 50, 60))
        else:
            for i, m in enumerate((15, 25, 50)):
                self.btns[str(m)] = (x0 + i * 168, 190, x0 + i * 168 + 156, 262)
                button(d, self.btns[str(m)], '%d min' % m, font(24, 'bold'), fill=ACCENT if m == 25 else CARD, color=(20, 20, 30) if m == 25 else FG)
        d.text((x0, 300), 'Sessões hoje: %d' % lstools.focus_stats(), font=font(20, 'bold'), fill=(120, 170, 255))
        d.text((x0, 336), 'O alarme toca no fim, como qualquer timer (com soneca).', font=font(14), fill=DIM)


TTT_LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]


def ttt_winner(b):
    for a, c, e in TTT_LINES:
        if b[a] and b[a] == b[c] == b[e]:
            return b[a]
    return 0


def ttt_score(b, player):
    """Minimax: TARS (2) maximiza, você (1) minimiza."""
    win = ttt_winner(b)
    if win:
        return 10 if win == 2 else -10
    empt = [i for i in range(9) if not b[i]]
    if not empt:
        return 0
    scores = []
    for i in empt:
        b[i] = player
        scores.append(ttt_score(b, 3 - player))
        b[i] = 0
    return max(scores) if player == 2 else min(scores)


class VelhaApp(App):
    """Jogo da velha contra o TARS. Ele joga quase perfeito (errar de propósito em 1 de cada 5 jogadas, para você ter esperança)."""
    title = 'Velha'
    WIN = ['Você ganhou. Registrado como milagre estatístico.', 'Perdi. Resultado do meu próprio erro de cálculo, a ser estudado.',
           'Vitória sua. Antifrágil: eu saio dessa mais forte, você sai mais convencido.']
    LOSE = ['Venci. Não leve para o lado pessoal. Leve para o lado estatístico.', 'Xeque-mate, ou algo parecido com três em linha.',
            'Ganhei. Você aprende mais com isso do que eu.']
    DRAW = ['Empate. A opção mais honesta entre dois jogadores competentes.', 'Deu velha. Como o nome do jogo promete.',
            'Empate. Nem tudo precisa de vencedor para contar.']

    def __init__(self, desk):
        super().__init__(desk)
        self.score = [0, 0, 0]          # você, TARS, empates
        self.first = 1
        self.btns, self.cells = {}, []
        self.new()

    def new(self):
        self.b = [0] * 9
        self.over = False
        self.msg = 'Sua vez. Você é o X.' if self.first == 1 else 'Eu começo.'
        if self.first == 2:
            self.tars_move()
        self.first = 3 - self.first

    def tars_move(self):
        empt = [i for i in range(9) if not self.b[i]]
        if not empt:
            return
        if random.random() < 0.2:
            i = random.choice(empt)
        else:
            best, i = -99, empt[0]
            random.shuffle(empt)
            for c in empt:
                self.b[c] = 2
                s = ttt_score(self.b, 1)
                self.b[c] = 0
                if s > best:
                    best, i = s, c
        self.b[i] = 2
        self.check()

    def check(self):
        win = ttt_winner(self.b)
        if win or all(self.b):
            self.over = True
            k = 0 if win == 1 else 1 if win == 2 else 2
            self.score[k] += 1
            self.msg = random.choice((self.WIN, self.LOSE, self.DRAW)[k]) if self.desk.brain.humor >= 25 else ('Você venceu.', 'Venci.', 'Empate.')[k]

    def on_tap(self, x, y):
        box = self.btns.get('new')
        if box and hit(box, x, y):
            self.new()
            self.dirty = True
            return
        if self.over:
            return
        for i, c in enumerate(self.cells):
            if hit(c, x, y) and not self.b[i]:
                self.b[i] = 1
                self.check()
                if not self.over:
                    self.tars_move()
                    if not self.over:
                        self.msg = 'Sua vez.'
                self.dirty = True
                return

    def draw(self, d, w, h):
        self.cells, self.btns = [], {}
        cs, x0, y0 = 118, 40, 22
        for i in range(9):
            cx, cy = x0 + (i % 3) * (cs + 6), y0 + (i // 3) * (cs + 6)
            box = (cx, cy, cx + cs, cy + cs)
            self.cells.append(box)
            d.rounded_rectangle(box, 12, fill=CARD)
            if self.b[i] == 1:
                d.line((cx + 26, cy + 26, cx + cs - 26, cy + cs - 26), fill=ACCENT, width=10)
                d.line((cx + cs - 26, cy + 26, cx + 26, cy + cs - 26), fill=ACCENT, width=10)
            elif self.b[i] == 2:
                d.ellipse((cx + 24, cy + 24, cx + cs - 24, cy + cs - 24), outline=(120, 170, 255), width=10)
        for a, c, e in TTT_LINES:
            if self.b[a] and self.b[a] == self.b[c] == self.b[e]:
                p, q = self.cells[a], self.cells[e]
                d.line(((p[0] + p[2]) // 2, (p[1] + p[3]) // 2, (q[0] + q[2]) // 2, (q[1] + q[3]) // 2), fill=GREEN, width=8)
        rx = 460
        d.text((rx, 20), 'Jogo da velha', font=font(28, 'bold'), fill=ACCENT)
        d.text((rx, 70), 'Você %d   ·   TARS %d   ·   Empates %d' % tuple(self.score), font=font(20, 'bold'), fill=FG)
        for i, ln in enumerate(wrap_text(d, self.msg, font(20), w - rx - 24)[:3]):
            d.text((rx, 124 + i * 28), ln, font=font(20), fill=(220, 224, 236))
        self.btns['new'] = (rx, 250, rx + 230, 310)
        button(d, self.btns['new'], 'Nova partida', font(22, 'bold'), fill=ACCENT if self.over else CARD, color=(20, 20, 30) if self.over else FG)


def _load_json(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None



APPS = [  # (classe, rótulo, símbolo, cor)
    (TarsApp, 'TARS', 'T', (84, 90, 100)),
    (SpotifyApp, 'Música', '♫', (30, 185, 84)),
    (ClockApp, 'Relógio', '◷', (200, 120, 40)),
    (RadioApp, 'Rádio', '≋', (170, 60, 140)),
    (WeatherApp, 'Clima', '☁', (50, 130, 200)),
    (HomeApp, 'Casa', '⌂', (60, 150, 130)),
    (NotesApp, 'Notas', '✎', (190, 170, 60)),
    (BrowserApp, 'Navegador', '◎', (60, 120, 200)),
    (NewsApp, 'Notícias', '≡', (200, 90, 60)),
    (TodayApp, 'Hoje', '◔', (120, 90, 200)),
    (FocusApp, 'Foco', '◐', (210, 80, 100)),
    (VelhaApp, 'Velha', '#', (60, 130, 120)),
    (TerminalApp, 'Terminal', '>_', (40, 46, 70)),
    (LiveApp, 'Ao Vivo', '◉', (200, 60, 70)),
    (MonitorApp, 'Monitor', '▲', (50, 120, 220)),
    (SoundApp, 'Som', '♪', (150, 80, 210)),
    (SettingsApp, 'Ajustes', '⚙', (90, 100, 120)),
    (AboutApp, 'Sobre', 'i', (40, 150, 110)),
]

# ======================================================================= teclado
SPECIAL = {'Esc': '\x1b', 'Tab': '\t', 'Enter': '\r', '⌫': '\x7f', 'Espaço': ' ',
           '←': '\x1b[D', '→': '\x1b[C', '↑': '\x1b[A', '↓': '\x1b[B'}
LAYERS = {
    'abc': [['Esc', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '⌫'],
            ['Tab', 'q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p', '-'],
            ['Ctrl', 'a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', ';', 'Enter'],
            ['Shift', 'z', 'x', 'c', 'v', 'b', 'n', 'm', ',', '.', '/', '|'],
            ['Sym', '←', '↓', '↑', '→', 'Espaço', 'Áç', "'", '▾']],
    'acc': [['Esc', 'á', 'à', 'â', 'ã', 'é', 'ê', 'í', 'ó', 'ô', 'õ', '⌫'],
            ['Tab', 'ú', 'ü', 'ç', 'ñ', ',', '.', '?', '!', ':', ';', 'Enter'],
            ['Ctrl', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '-'],
            ['Shift', '/', '(', ')', '"', "'", '@', '#', '$', '%', '&', '*'],
            ['abc', '←', '↓', '↑', '→', 'Espaço', 'Sym', '▾']],
    'sym': [['Esc', '!', '@', '#', '$', '%', '^', '&', '*', '(', ')', '⌫'],
            ['Tab', '`', '~', '_', '=', '+', '[', ']', '{', '}', '\\', '-'],
            ['Ctrl', '"', "'", ':', ';', '<', '>', '?', '/', '|', '&', 'Enter'],
            ['Shift', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '.'],
            ['abc', '←', '↓', '↑', '→', 'Espaço', 'Áç', '"', '▾']],
}
WIDE = {'Espaço': 4.0, 'Enter': 1.6, 'Shift': 1.5, 'Ctrl': 1.4, 'Tab': 1.3, 'Esc': 1.2, '⌫': 1.4, 'Sym': 1.3, 'abc': 1.3,
        '▾': 1.3, 'Áç': 1.3}
HIDE_KEY = '▾'                         # minimiza o teclado


class Keyboard:
    def __init__(self):
        self.layer, self.shift, self.ctrl, self.pressed = 'abc', False, False, None
        self.hide_request = False
        self.keys = []

    def layout(self, y0):
        self.keys = []
        rows = LAYERS[self.layer]
        rh = KB_H / len(rows)
        for r, row in enumerate(rows):
            total = sum(WIDE.get(k, 1.0) for k in row)
            x = 0.0
            for k in row:
                kw = W * WIDE.get(k, 1.0) / total
                ry = y0 + r * rh
                self.keys.append(((int(x) + 2, int(ry) + 2, int(x + kw) - 2, int(ry + rh) - 2), k))
                x += kw

    def press(self, x, y):
        for box, k in self.keys:
            if hit(box, x, y):
                self.pressed = k
                return self.key(k)
        return None

    def key(self, k):
        if k == HIDE_KEY:
            self.hide_request = True
            return None
        if k in ('Sym', 'abc', 'Áç'):
            self.layer = {'Sym': 'sym', 'abc': 'abc', 'Áç': 'acc'}[k]
            return None
        if k == 'Shift':
            self.shift = not self.shift
            return None
        if k == 'Ctrl':
            self.ctrl = not self.ctrl
            return None
        if k in SPECIAL:
            s = SPECIAL[k]
        else:
            s = k.upper() if self.shift else k
            if self.ctrl and len(s) == 1 and s.isalpha():
                s = chr(ord(s.lower()) & 0x1f)
            self.shift = False
        self.ctrl = False
        return s.encode()

    def draw(self, d, y0):
        self.layout(y0)
        d.rectangle([0, y0, W, y0 + KB_H], fill=(22, 25, 40))
        f = font(19)
        for box, k in self.keys:
            active = (k == 'Shift' and self.shift) or (k == 'Ctrl' and self.ctrl) or k == self.pressed
            special = k in SPECIAL or k in ('Shift', 'Ctrl', 'Sym', 'abc', 'Áç', HIDE_KEY)
            fill = ACCENT if active else ((48, 54, 82) if special else (64, 70, 102))
            d.rounded_rectangle(box, radius=7, fill=fill)
            label = k.upper() if (self.shift and len(k) == 1 and k.isalpha()) else k
            center_text(d, box, label, f, (20, 20, 30) if active else (235, 238, 245))


# ======================================================================= desktop
class ToolUI:
    """Ponte entre as ferramentas do TARS e a área de trabalho (confirmação na tela, atividade, Spotify, volume)."""

    def __init__(self, desk):
        self.desk = desk

    def confirm(self, text):
        return self.desk.ask_confirm(text)

    def event(self, text):
        self.desk.activity.append((time.monotonic(), text))
        del self.desk.activity[:-6]
        self.desk.dirty = True

    def spotify(self, cmd):
        if cmd not in ('toggle', 'next', 'prev', 'volup', 'voldown', 'play', 'pause'):
            return {'erro': 'comando inválido'}
        try:
            fd = os.open(SPOTIFY_CMD, os.O_WRONLY | os.O_NONBLOCK)
            os.write(fd, (cmd + '\n').encode())
            os.close(fd)
            return {'ok': True}
        except OSError:
            return {'erro': 'serviço do Spotify não está rodando'}

    def open_browser(self, dest):
        dest = (dest or '').strip()
        if not dest:
            return {'erro': 'sem destino'}
        if lsweb.looks_like_url(dest):
            host = lsweb.urllib.parse.urlparse(lsweb.normalize(dest)).hostname or ''
            kind = lstools.is_private(host)
            if kind == 'bloqueado':
                return {'erro': 'endereço local/loopback bloqueado'}
            if kind == 'privado' and not self.desk.ask_confirm('Abrir no navegador uma página da sua rede local?\n' + dest):
                return {'erro': 'o dono não autorizou'}
        self.desk.pending.append(dest)
        return {'ok': True, 'aberto_na_tela': dest}

    def set_volume(self, pct):
        self.desk.audio.set_volume(round(max(0, min(100, pct)) * Audio.MAX / 100))
        return {'ok': True, 'volume_pct': self.desk.audio.percent()}


class VoiceLoop(threading.Thread):
    """Escuta contínua. O microfone fica ligado; quando alguém fala, o áudio vai ao modelo rápido do Gemini, que
    transcreve e decide se foi dirigido ao TARS ("TARS, ..."). Se foi, o TARS dá o 'ding', abre na tela e responde.
    Limites de uso evitam gastar a cota: 6 trechos por minuto e 400 por dia. Pausa enquanto há som tocando."""
    MAX_PER_MIN, MAX_PER_DAY = 6, 400

    def __init__(self, desk):
        super().__init__(daemon=True)
        self.desk = desk
        self.ptt = False                   # "falar agora": ignora a palavra de ativação
        self.cancel = False
        self.state = 'off'                 # off | ouvindo | pausado | pensando
        self.convo_until = 0.0             # janela de conversa: depois que o TARS fala, não precisa dizer o nome
        self.calls = []
        self.day = [time.strftime('%Y%m%d'), 0]
        self.note, self.note_until = '', 0.0

    def set_note(self, msg, secs=5.0):
        self.note, self.note_until = msg, time.monotonic() + secs

    def request_ptt(self):
        self.ptt, self.cancel = True, True

    def audio_busy(self):
        return os.path.exists(AUDIO_ACTIVE) or os.path.exists(TARS_SPEAKING) or os.path.exists('/run/luizinhoshowos/mic-lock')

    def allowed(self):
        now = time.time()
        self.calls = [t for t in self.calls if now - t < 60]
        if time.strftime('%Y%m%d') != self.day[0]:
            self.day = [time.strftime('%Y%m%d'), 0]
        return len(self.calls) < self.MAX_PER_MIN and self.day[1] < self.MAX_PER_DAY

    def run(self):
        time.sleep(8)
        while True:
            try:
                self.step()
            except Exception as e:
                log('voz:', repr(e))
                time.sleep(3)

    def step(self):
        d, L, b = self.desk, self.desk.listener, self.desk.brain
        if L is None:
            time.sleep(10)
            return
        self.cancel = False
        ptt = self.ptt
        if not (b.listen or ptt):
            self.state = 'off'
            time.sleep(0.5)
            return
        if (self.audio_busy() or d.modal or d.ringing) and not ptt:
            self.state = 'pausado'
            time.sleep(0.4)
            return
        self.state = 'ouvindo'
        wav, why = L.listen(max_s=10, start_timeout=10 if ptt else 25,
                            cancel=lambda: self.cancel or not (b.listen or self.ptt) or (not self.ptt and self.audio_busy()))
        if self.ptt and self.cancel and wav is None:
            return                                          # PTT pedido no meio da escuta: recomeça já em modo PTT
        self.ptt = False
        if wav is None:
            if ptt and why in ('silêncio', 'timeout'):
                self.set_note('Não ouvi nada. Toque no monólito e fale.')
            return
        dur = (len(wav) - 44) / 32000.0
        convo = time.time() < self.convo_until
        if not ptt and dur < 0.5:
            return
        local_hit = False
        if not ptt and not convo and b.wake_mode == 'local':
            w = d.get_wake()
            if w.ok:                                         # nada sai do aparelho se o Vosk não ouvir o nome
                hit, txt = w.detect_grammar(wav[44:])
                log('wake local: %s | %r' % ('OUVIU' if hit else 'ignorado', txt))
                if not hit:
                    return
                local_hit = True
        if not ptt and not self.allowed():
            return
        if not ptt:
            self.calls.append(time.time())
            self.day[1] += 1
        self.state = 'pensando'
        res, ev = {}, threading.Event()
        b.ask_audio(wav, brain_context(), lambda r, e: (res.update(r=r, e=e), ev.set()),
                    on_heard=d.on_heard, wake=not (ptt or local_hit), convo=convo)
        ev.wait(240)
        self.state = 'ouvindo'
        r, e = res.get('r', ''), res.get('e', '')
        if r and not e:
            d.show_tars()
            d.speak(r)
            self.after_speech()
        elif r:
            d.show_tars()
            d.speak(r)
        elif e not in ('sem fala', '') and ptt:
            self.set_note(e[:60])

    def after_speech(self):
        """Espera o TARS terminar de falar e abre a janela de conversa (10 s sem precisar dizer 'TARS')."""
        t0 = time.time()
        while time.time() - t0 < 4 and not os.path.exists(TARS_SPEAKING):
            time.sleep(0.2)
        while os.path.exists(TARS_SPEAKING) and time.time() - t0 < 60:
            time.sleep(0.3)
        self.convo_until = time.time() + 10


class Desktop:
    def __init__(self):
        self.fb = os.open(FB_DEV, os.O_RDWR)
        self.audio = Audio()
        self.brain = Brain()
        self.cfg = _load_json(DESK_CFG) or {}
        self.auto_bl = bool(self.cfg.get('auto_brightness', True))
        self.lux, self.lux_ema, self.bl_target = None, None, None
        self.cancel_stamp = 0.0
        self.last_active = False
        self.pending = []                  # destinos para abrir no navegador (pedidos pelo TARS, de outra thread)
        self.modal = None                  # confirmação de ação do TARS (SIM/NÃO)
        self.ringing = None                # timer/alarme tocando
        self.activity = []                 # o que o TARS está fazendo (ferramentas)
        self.heard_at = 0.0                # quando o TARS acabou de ouvir seu nome ("ding")
        self.als = None
        self.wake_engine = None            # lswake.Wake (Vosk), carregado só quando o modo local é usado
        self.last_greet = 0.0
        self.last_input = time.monotonic()
        self.ambient = False               # modo ocioso: TARS + relógio, tela escura
        self.saved_bl = None
        self.tars_face = None
        self.listener = Listener() if Listener else None
        self.brain.tools = lstools.Tools(ToolUI(self))
        self.voice = VoiceLoop(self)
        self.backlight = Backlight()
        self.kb = Keyboard()
        self.kb_on = True
        self.windows = []          # apps abertos
        self.focus = None          # app em foco (None = início)
        self.split = False
        self.dirty = True
        self.last_render = 0
        self.last_clock = ''
        self.toast_msg, self.toast_until = '', 0
        self.frame = None
        self.frame_ver = 0
        self.frame_cv = threading.Condition()
        self.touch, self.buttons = self.find_inputs()
        self.slots, self.cur_slot = {}, 0
        self.regions = {}
        self.icon_boxes = []
        self.watch = {}
        self.rewatch()

    # ---------------------------------------------------------- entrada
    def find_inputs(self):
        touch, keys = None, []
        for p in evdev.list_devices():
            d = evdev.InputDevice(p)
            if d.name == 'goodix-ts':
                touch = d
            elif d.name == 'gpio-keys':
                keys.append(d)
        log('touch:', touch and touch.path, 'botões:', [k.path for k in keys])
        return touch, keys

    def rewatch(self):
        self.watch = {}
        if self.touch:
            self.watch[self.touch.fd] = ('touch', None)
        for b in self.buttons:
            self.watch[b.fd] = ('button', b)
        for app in self.windows:
            for fd in app.fds():
                self.watch[fd] = ('app', app)
        if hasattr(self, 'ctl_fd'):
            self.watch[self.ctl_fd] = ('ctl', None)
        if self.als is not None:
            self.watch[self.als.fd] = ('als', None)

    def on_touch_event(self, ev):
        E = evdev.ecodes
        if ev.type == E.EV_ABS:
            self.last_input = time.monotonic()
            if ev.code == E.ABS_MT_SLOT:
                self.cur_slot = ev.value
            elif ev.code == E.ABS_MT_TRACKING_ID:
                st = self.slots.setdefault(self.cur_slot, {})
                if ev.value == -1:
                    st['up'] = True
                else:
                    st.clear()
                    st['new'] = True
            elif ev.code == E.ABS_MT_POSITION_X:
                self.slots.setdefault(self.cur_slot, {})['x'] = ev.value
            elif ev.code == E.ABS_MT_POSITION_Y:
                self.slots.setdefault(self.cur_slot, {})['y'] = ev.value
        elif ev.type == E.EV_SYN and ev.code == E.SYN_REPORT:
            for slot, st in list(self.slots.items()):
                if st.get('new') and 'x' in st and 'y' in st:
                    st['new'] = False
                    # painel em retrato 480x960 -> tela paisagem 960x480
                    self.tap(st['y'] * W / 960.0, (480 - st['x']) * H / 480.0)
                if st.get('up'):
                    del self.slots[slot]
                    if self.kb.pressed:
                        self.kb.pressed = None
                        self.dirty = True

    def on_button_event(self, ev):
        E = evdev.ecodes
        if ev.type == E.EV_KEY and ev.value == 1:
            self.last_input = time.monotonic()
            self.wake()
            if ev.code == E.KEY_VOLUMEUP:
                self.audio.set_volume(self.audio.vol + 3)
            elif ev.code == E.KEY_VOLUMEDOWN:
                self.audio.set_volume(self.audio.vol - 3)
            self.toast(f'Volume {self.audio.percent()}%')
            for w in self.windows:
                if isinstance(w, SoundApp):
                    w.dirty = True

    # ---------------------------------------------------------- janelas
    def open_app(self, cls, greet=True):
        if cls.single:
            for w in self.windows:
                if isinstance(w, cls):
                    self.focus = w
                    self.dirty = True
                    return w
        app = cls(self)
        if cls is TerminalApp:
            n = sum(isinstance(w, TerminalApp) for w in self.windows) + 1
            app.title = f'Terminal {n}'
        self.windows.append(app)
        self.focus = app
        self.rewatch()
        self.dirty = True
        log('abriu', app.title)
        if cls is TarsApp and greet and time.monotonic() - self.last_greet > 45:
            self.last_greet = time.monotonic()
            app.greet()
        return app

    def close_app(self, app):
        app.close()
        self.windows.remove(app)
        if self.focus is app:
            self.focus = self.windows[-1] if self.windows else None
        if len(self.windows) < 2:
            self.split = False
        self.rewatch()
        self.dirty = True

    def visible(self):
        """Lista de (app, caixa) visíveis na área de apps."""
        top, bottom = STATUS_H, H - DOCK_H - (KB_H if self.keyboard_visible() else 0)
        if self.focus is None:
            return []
        if self.split and len(self.windows) >= 2:
            others = [w for w in self.windows if w is not self.focus]
            other = others[-1]
            left, right = (other, self.focus) if self.windows.index(other) < self.windows.index(self.focus) else (self.focus, other)
            return [(left, (0, top, W // 2 - 1, bottom)), (right, (W // 2 + 1, top, W, bottom))]
        return [(self.focus, (0, top, W, bottom))]

    def keyboard_visible(self):
        return self.kb_on and self.focus is not None and self.focus.wants_keyboard

    # ---------------------------------------------------------- toque
    def tap(self, x, y):
        log(f'toque ({x:.0f},{y:.0f})')
        self.last_input = time.monotonic()
        if self.modal:                                     # confirmação de uma ação do TARS
            for k, box in self.modal.get('btns', {}).items():
                if hit(box, x, y):
                    self.modal['ok'] = (k == 'yes')
                    self.modal['ev'].set()
            return
        if self.ringing:                                   # alarme/timer tocando
            for k, box in getattr(self, 'ring_btns', {}).items():
                if hit(box, x, y):
                    it = self.ringing
                    self.ringing = None
                    if k == 'snooze':
                        lstools.add_timer(300, it['label'])
                        self.toast('Soneca de 5 minutos')
            return
        if self.ambient:                                   # o primeiro toque só acorda
            self.wake()
            return
        self.dirty = True
        for name, box in self.regions.items():
            if hit(box, x, y):
                return self.on_region(name)
        if self.keyboard_visible() and y >= H - DOCK_H - KB_H and y < H - DOCK_H:
            data = self.kb.press(x, y)
            if self.kb.hide_request:
                self.kb.hide_request = False
                self.kb.pressed = None
                self.kb_on = False
                self.toast('Teclado minimizado · toque no app ou em ⌨ para abrir')
                return
            if data and self.focus:
                self.focus.on_text(data)
            return
        if self.focus is None:
            for i, box in enumerate(self.icon_boxes):
                if hit(box, x, y):
                    self.open_app(APPS[i][0])
                    return
            return
        for app, box in self.visible():
            if hit(box, x, y):
                if self.focus is not app:
                    self.focus = app
                elif app.wants_keyboard and not self.kb_on and getattr(app, 'kb_reopen_on_tap', True):
                    self.kb_on = True      # toque no terminal reabre o teclado
                    return
                app.on_tap(x - box[0], y - box[1])
                return

    def on_region(self, name):
        if name == 'home':
            self.focus = None
            self.split = False
        elif name == 'split':
            if len(self.windows) >= 2:
                self.split = not self.split
            else:
                self.toast('Abra 2 apps para dividir a tela')
        elif name == 'kb':
            self.kb_on = not self.kb_on
        elif name == 'stop':
            self.stop_all()
        elif name.startswith('tab:'):
            self.focus = self.windows[int(name[4:])]
        elif name.startswith('close:'):
            self.close_app(self.windows[int(name[6:])])
        elif name == 'clock':
            self.focus = None
        elif name == 'bl':
            self.auto_bl = not self.auto_bl
            self.save_cfg()
            self.toast('Brilho automático ' + ('ligado' if self.auto_bl else 'desligado'))

    # ---------------------------------------------------------- voz, confirmação, alarmes, sensor de luz
    def speak(self, text, force=False):
        if not text or (not self.brain.voice and not force):
            return
        try:
            p = subprocess.Popen(['/usr/local/bin/tars-say'], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            p.stdin.write(text.encode())
            p.stdin.close()
        except OSError:
            pass

    def get_wake(self):
        if self.wake_engine is None:
            import lswake
            self.wake_engine = lswake.Wake()
            log('detector local:', 'ok' if self.wake_engine.ok else 'indisponível (%s)' % self.wake_engine.error, self.wake_engine.grammar)
        return self.wake_engine

    def stop_speaking(self):
        subprocess.Popen(['/usr/local/bin/tars-say', '--stop'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def tars_active(self):
        """True enquanto o TARS fala, pensa, usa ferramentas, espera uma confirmação ou resume uma página (mostra o botão PARAR)."""
        return (os.path.exists(TARS_SPEAKING) or self.brain.busy or self.voice.state == 'pensando' or self.modal is not None
                or any(getattr(w, 'sum_busy', False) for w in self.windows))

    def stop_all(self):
        """Botão PARAR: corta a voz, descarta a resposta/ferramentas em andamento e recusa confirmações pendentes."""
        self.cancel_stamp = time.monotonic()
        self.brain.cancel_current()
        self.stop_speaking()
        if self.modal:
            self.modal['ok'] = False
            self.modal['ev'].set()
        for w in self.windows:
            if getattr(w, 'sum_busy', False):
                w.sum_busy, w.summary = False, ''
            if isinstance(w, TarsApp):
                w.thinking = False
        self.activity.clear()
        self.toast('TARS interrompido')
        self.dirty = True

    def show_tars(self):
        """Traz o TARS para a tela (acorda se estiver ocioso)."""
        self.wake()
        if not (self.focus is not None and isinstance(self.focus, TarsApp)):
            self.open_app(TarsApp, greet=False)
        self.dirty = True

    def on_heard(self, text):
        """O TARS reconheceu que falaram com ele: 'ding', tela acorda e o monólito acende."""
        self.heard_at = time.monotonic()
        subprocess.Popen(['/usr/local/bin/lsplay', SFX + '/chime.wav', '0.8'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.activity.clear()
        self.show_tars()

    def ask_confirm(self, text, timeout=60):
        """Pede SIM/NÃO ao dono na tela (bloqueia a thread do TARS até ele tocar ou 60 s)."""
        ev = threading.Event()
        m = {'text': text, 'ev': ev, 'ok': False, 't0': time.monotonic(), 'timeout': timeout, 'btns': {}}
        self.modal = m
        self.wake()
        self.last_input = time.monotonic()
        self.dirty = True
        self.speak('Preciso da sua autorização na tela.')
        ev.wait(timeout)
        if self.modal is m:
            self.modal = None
        self.dirty = True
        return m['ok']

    def draw_modal(self, d):
        m = self.modal
        d.rectangle((0, 0, W, H), fill=(6, 6, 10))
        d.rounded_rectangle((40, 36, W - 40, H - 36), 16, fill=(26, 28, 46), outline=ACCENT, width=2)
        d.text((64, 50), 'TARS pede autorização', font=font(24, 'bold'), fill=ACCENT)
        f = font(16, 'mono')
        y = 92
        for ln in wrap_text(d, m['text'], f, W - 140)[:8]:
            d.text((64, y), ln, font=f, fill=FG)
            y += 22
        left = max(0.0, 1 - (time.monotonic() - m['t0']) / m['timeout'])
        d.rectangle((64, H - 128, 64 + int((W - 128) * left), H - 122), fill=ACCENT)
        no, yes = (64, H - 108, 64 + 270, H - 52), (W - 64 - 270, H - 108, W - 64, H - 52)
        m['btns'] = {'no': no, 'yes': yes}
        button(d, no, 'NÃO', font(24, 'bold'), fill=(130, 48, 48))
        button(d, yes, 'SIM, EXECUTAR', font(24, 'bold'), fill=(40, 130, 70))

    def ring(self, item):
        self.ringing = item
        self.wake()
        self.last_input = time.monotonic()
        self.dirty = True
        label = item['label']
        self.speak(('Alarme: %s.' % label) if item['kind'] == 'alarm' else ('Tempo esgotado: %s.' % label), force=True)
        threading.Thread(target=self._ring_loop, args=(item,), daemon=True).start()
        log('tocando', item['kind'], label)

    def _ring_loop(self, item):
        t0 = time.time()
        time.sleep(3.5)                                    # deixa o TARS falar primeiro
        while self.ringing is item and time.time() - t0 < 90:
            subprocess.Popen(['/usr/local/bin/lsplay', SFX + '/beep.wav', '0.9'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.6)
        if self.ringing is item:
            self.ringing = None

    def draw_ring(self, d):
        it = self.ringing
        d.rectangle((0, 0, W, H), fill=(10, 6, 2))
        pulse = int(abs(math.sin(time.monotonic() * 4)) * 60)
        d.rounded_rectangle((60, 50, W - 60, H - 50), 20, fill=(40 + pulse // 2, 26, 8), outline=(255, 176, 0), width=3)
        d.text((100, 80), 'ALARME' if it['kind'] == 'alarm' else 'TEMPO ESGOTADO', font=font(30, 'bold'), fill=(255, 176, 0))
        d.text((100, 130), fit(d, it['label'], font(56, 'bold'), W - 200), font=font(56, 'bold'), fill=(255, 255, 255))
        d.text((100, 214), time.strftime('%H:%M'), font=font(40), fill=(255, 210, 120))
        stop, snooze = (100, H - 170, 100 + 330, H - 110), (W - 100 - 330, H - 170, W - 100, H - 110)
        self.ring_btns = {'stop': stop, 'snooze': snooze}
        button(d, stop, 'PARAR', font(26, 'bold'), fill=(150, 50, 50))
        button(d, snooze, 'SONECA 5 MIN', font(26, 'bold'), fill=(60, 90, 150))

    def setup_als(self):
        """Liga o sensor de luz/proximidade (JSA1214) e abre o dispositivo de eventos."""
        for f in ('alsactive', 'psactive'):
            try:
                open('/sys/class/misc/m_alsps_misc/' + f, 'w').write('1')
            except OSError:
                pass
        for p in evdev.list_devices():
            dev = evdev.InputDevice(p)
            if dev.name == 'm_alsps_input':
                self.als = dev
                try:                                         # leitura inicial (o sensor só avisa quando o valor muda)
                    self.lux = self.lux_ema = float(dev.absinfo(evdev.ecodes.ABS_X).value)
                except OSError:
                    pass
        self.rewatch()

    def on_als(self):
        E = evdev.ecodes
        for ev in self.als.read():
            if ev.type != E.EV_ABS:
                continue
            if ev.code == E.ABS_X:
                self.lux = ev.value
                self.lux_ema = ev.value if self.lux_ema is None else self.lux_ema * 0.85 + ev.value * 0.15
            elif ev.code == E.ABS_WHEEL:                    # proximidade: ainda sem uso, só registra para calibrar
                if getattr(self, 'ps_logged', 0) < 40:
                    self.ps_logged = getattr(self, 'ps_logged', 0) + 1
                    log('proximidade', ev.value)

    def auto_brightness_tick(self):
        if not self.auto_bl or self.ambient or self.lux_ema is None:
            return
        target = int(255 * min(1.0, 0.35 + 0.15 * math.log10(1 + self.lux_ema)))
        target = max(89, min(255, target))          # nunca abaixo de 35%: a escala do sensor ainda não foi calibrada
        cur = self.backlight.get()
        if abs(target - cur) > 10:
            self.backlight.set(cur + max(-12, min(12, target - cur)))

    def save_cfg(self):
        try:
            os.makedirs(os.path.dirname(DESK_CFG), exist_ok=True)
            json.dump({'auto_brightness': self.auto_bl}, open(DESK_CFG, 'w'))
        except OSError:
            pass

    def make_sfx(self):
        os.makedirs(SFX, exist_ok=True)
        try:
            if not os.path.exists(SFX + '/chime.wav'):
                base = ['/usr/bin/sox', '-q', '-n', '-r', '48000', '-b', '16', '-c', '2']
                subprocess.run(base + [SFX + '/c1.wav', 'synth', '0.09', 'sine', '988', 'vol', '0.4', 'fade', 'h', '0.005', '0.09', '0.04'], check=True)
                subprocess.run(base + [SFX + '/c2.wav', 'synth', '0.18', 'sine', '1319', 'vol', '0.4', 'fade', 'h', '0.005', '0.18', '0.08'], check=True)
                subprocess.run(['/usr/bin/sox', SFX + '/c1.wav', SFX + '/c2.wav', SFX + '/chime.wav'], check=True)
            if not os.path.exists(SFX + '/beep.wav'):
                base = ['/usr/bin/sox', '-q', '-n', '-r', '48000', '-b', '16', '-c', '2']
                subprocess.run(base + [SFX + '/b.wav', 'synth', '0.15', 'sine', '1760', 'vol', '0.5', 'fade', 'h', '0.01', '0.15', '0.04'], check=True)
                subprocess.run(base + [SFX + '/s.wav', 'trim', '0', '0.1'], check=True)
                subprocess.run(['/usr/bin/sox', SFX + '/b.wav', SFX + '/s.wav', SFX + '/b.wav', SFX + '/s.wav', SFX + '/b.wav', SFX + '/beep.wav'], check=True)
        except (OSError, subprocess.SubprocessError) as e:
            log('sfx:', e)

    def draw_mic(self, d, x, y):
        """Indicador de escuta: pulsa quando está captando fala (o "estou te ouvindo" da Alexa)."""
        L = self.listener
        if not self.brain.listen:
            d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(90, 40, 40))
            d.line((x - 6, y + 6, x + 6, y - 6), fill=(230, 110, 100), width=2)
            return
        if L is not None and L.speech_active:
            r = 6 + int(abs(math.sin(time.monotonic() * 8)) * 3)
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 176, 0))
            bh = 3 + int(min(1.0, L.level * 40) * 12)
            d.rectangle((x + 12, y + 6 - bh, x + 15, y + 6), fill=(255, 210, 90))
            d.rectangle((x + 18, y + 6 - bh // 2, x + 21, y + 6), fill=(255, 210, 90))
        elif self.voice.state == 'pensando':
            d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(90, 150, 255))
        elif self.voice.state == 'pausado':
            d.ellipse((x - 5, y - 5, x + 5, y + 5), outline=(150, 110, 30), width=2)
        else:
            d.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(120, 84, 14))

    # ---------------------------------------------------------- modo ocioso
    def enter_ambient(self):
        if self.ambient:
            return
        self.ambient = True
        self.saved_bl = self.backlight.get()
        self.backlight.set(min(self.saved_bl or 90, 26))
        self.dirty = True
        log('modo ocioso')

    def wake(self):
        if not self.ambient:
            return
        self.ambient = False
        if self.saved_bl:
            self.backlight.set(self.saved_bl)
        self.last_input = time.monotonic()
        self.dirty = True
        log('acordou')

    def draw_ambient(self, d):
        if self.tars_face is None:
            self.tars_face = TarsApp(self)
        d.rectangle((0, 0, W, H), fill=(4, 4, 3))
        self.tars_face.draw_slab(d, 36, 6, 250, H - 12)
        x = 340
        d.text((x, 40), time.strftime('%H:%M'), font=font(150, 'bold'), fill=(255, 176, 0))
        t = time.localtime()
        dias = ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo']
        meses = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
                 'outubro', 'novembro', 'dezembro']
        dia = dias[t.tm_wday] + ('' if t.tm_wday >= 5 else '-feira')
        d.text((x + 4, 235), f'{dia}, {t.tm_mday} de {meses[t.tm_mon - 1]}', font=font(30), fill=(200, 140, 0))
        sp = spotify_state()
        if sp.get('state') == 'playing' and sp.get('title'):
            line = '♫ ' + sp.get('title', '') + ' — ' + ', '.join(sp.get('artists', []))
            d.text((x + 4, 300), fit(d, line, font(22), W - x - 30), font=font(22), fill=(150, 110, 20))
        line = self.ambient_line()
        if line:
            for i, ln in enumerate(wrap_text(d, line, font(21), W - x - 40)[:3]):
                d.text((x + 4, 346 + i * 28), ln, font=font(21), fill=(190, 135, 10))
        d.text((x + 4, H - 40), 'toque para acordar', font=font(16), fill=(80, 60, 15))

    QUIPS = ['Nenhuma ameaça detectada na sala. A planta continua suspeita.',
             'Diga "TARS" e eu escuto. Diga "por favor" e eu fico impressionado.',
             'Missão atual: ficar bonito e esperar ordens.',
             'Bateria: eterna. Paciência: configurável.',
             'Se ninguém falar comigo, eu falo comigo. Em silêncio.',
             'Lembrete: água é de graça e desidratação é opcional.',
             'Sistema estável. Humor estável. O resto da casa, não sei.',
             'Posso abrir o navegador, ler as notícias ou só ficar aqui. Decida.',
             'Você não controla o dia. Controla o próximo passo. Dê um.',
             'Erro é combustível. Sistemas bons ficam mais fortes depois do susto.',
             'O que atrapalha o caminho é o caminho. Faça dele treino.',
             'Todo segundo gasto reclamando poderia ter sido gasto reclamando menos.',
             'Every second counts.',
             'Desconforto controlado é academia para o caráter.',
             'Aceite o que não muda. Mude o que aceita ser mudado. Eu cuido do sarcasmo.']

    def ambient_line(self):
        """Uma linha que muda a cada 40 s no modo ocioso: manchete do dia ou uma tirada do TARS (conforme o humor)."""
        n = int(time.time() // 40)
        news = lstools._load_news()
        heads = [i['titulo'] for k in ('g1', 'tecnologia') for i in news.get(k, {}).get('itens', [])[:5]]
        pool = []
        for i in range(max(len(heads), 1)):
            if heads:
                pool.append('Manchete: ' + heads[i])
            if self.brain.humor >= 25:
                pool.append(self.QUIPS[(i + n) % len(self.QUIPS)] if i % 2 == 0 else
                            'Humor em %d%%. Honestidade em %d%%.' % (self.brain.humor, self.brain.honesty))
        return pool[n % len(pool)] if pool else 'TARS em espera.'

    def on_media_change(self):
        """Quando uma música começa: abre o player (se estiver no início ou já no player);
        se o usuário estiver em outro app, só avisa com um toast."""
        st = spotify_state()
        key = (st.get('source'), st.get('title'), st.get('state'))
        started = st.get('state') == 'playing' and key != getattr(self, 'last_media', None)
        self.last_media = key
        self.dirty = True
        if not started:
            return
        player = next((w for w in self.windows if isinstance(w, SpotifyApp)), None)
        if self.focus is None or self.focus is player:
            self.open_app(SpotifyApp)
        else:
            self.toast(f'♫ {st.get("title", "Tocando")} · {st.get("source", "")}')

    def toast(self, msg):
        self.toast_msg, self.toast_until = msg, time.monotonic() + 1.6
        self.dirty = True

    # ---------------------------------------------------------- desenho
    def render(self):
        img = Image.new('RGB', (W, H), BG)
        d = ImageDraw.Draw(img)
        if self.ambient:
            self.draw_ambient(d)
            self.draw_mic(d, W - 40, 22)
            self.blit(img)
            with self.frame_cv:
                self.frame, self.frame_ver = img, self.frame_ver + 1
                self.frame_cv.notify_all()
            self.dirty = False
            self.last_render = time.monotonic()
            return
        self.regions = {}
        self.draw_status(d)
        if self.focus is None:
            self.draw_home(img, d)
        else:
            for app, (x0, y0, x1, y1) in self.visible():
                img.paste(app.image(x1 - x0, y1 - y0), (x0, y0))
                if self.split:
                    d.rectangle((x0, y0, x1 - 1, y1 - 1), outline=ACCENT if app is self.focus else CARD, width=2)
            if self.split:
                d.line((W // 2, STATUS_H, W // 2, H - DOCK_H), fill=PANEL, width=2)
        if self.keyboard_visible():
            self.kb.draw(d, H - DOCK_H - KB_H)
        self.draw_dock(d)
        if self.ringing:
            self.draw_ring(d)
        if self.modal:
            self.draw_modal(d)
        if time.monotonic() < self.toast_until:
            tw = d.textlength(self.toast_msg, font=font(20, 'bold')) + 40
            box = ((W - tw) / 2, H / 2 - 30, (W + tw) / 2, H / 2 + 20)
            d.rounded_rectangle(box, 14, fill=(0, 0, 0))
            center_text(d, box, self.toast_msg, font(20, 'bold'), ACCENT)
        self.blit(img)
        with self.frame_cv:
            self.frame, self.frame_ver = img, self.frame_ver + 1
            self.frame_cv.notify_all()
        self.dirty = False
        self.last_render = time.monotonic()

    def draw_status(self, d):
        d.rectangle((0, 0, W, STATUS_H), fill=PANEL)
        d.text((12, 6), 'TarsOS', font=font(15, 'bold'), fill=ACCENT)
        clock = time.strftime('%H:%M')
        self.regions['clock'] = (W / 2 - 60, 0, W / 2 + 60, STATUS_H)
        center_text(d, (W / 2 - 60, 0, W / 2 + 60, STATUS_H), clock, font(17, 'bold'), FG)
        ip = ip_addr()
        right = f'{"WiFi " + ip if ip else "sem rede"}   ♪ {self.audio.percent()}%   ☀ {self.backlight.percent()}%{"A" if self.auto_bl else ""}'
        rw = d.textlength(right, font=font(14))
        d.text((W - 12 - rw, 7), right, font=font(14), fill=DIM)
        self.regions['bl'] = (W - 12 - 78, 0, W, STATUS_H)             # toque: liga/desliga o brilho automático
        self.draw_mic(d, W // 2 + 86, 15)

    def draw_home(self, img, d):
        top = STATUS_H
        for y in range(top, H - DOCK_H):            # degradê
            c = int(14 + 26 * (y - top) / (H - DOCK_H - top))
            d.line([(0, y), (W, y)], fill=(c // 2, c // 3 + 4, c + 18))
        d.text((40, top + 14), time.strftime('%H:%M'), font=font(104, 'bold'), fill=FG)
        dias = ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo']
        meses = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
                 'outubro', 'novembro', 'dezembro']
        t = time.localtime()
        d.text((46, top + 146), f'{dias[t.tm_wday]}-feira, {t.tm_mday} de {meses[t.tm_mon - 1]}'.replace('sábado-feira', 'sábado').replace('domingo-feira', 'domingo'),
               font=font(24), fill=(180, 205, 255))
        r = read_release()
        d.text((46, top + 188), f'{r.get("PRETTY_NAME", "TarsOS")}', font=font(16), fill=DIM)
        wj = _load_json(lstools.VAR + '/weather.json')
        if wj:
            hoje = wj['proximos_dias'][0]
            d.text((46, top + 240), '%d° · %s · máx %d° mín %d°' % (round(wj['agora']['temperatura']), wj['agora']['condicao'],
                                                                     round(hoje['max']), round(hoje['min'])), font=font(18, 'bold'), fill=(150, 200, 255))
            d.text((46, top + 266), wj['cidade'], font=font(14), fill=DIM)
        sp = spotify_state()
        if sp.get('state') in ('playing', 'paused') and sp.get('title'):
            icon = '▶' if sp['state'] == 'playing' else '❚❚'
            txt = f'{icon} ♫ {sp.get("title", "")} — {", ".join(sp.get("artists", []))}'
            d.rounded_rectangle((40, top + 300, 440, top + 344), 14, fill=(20, 60, 36))
            d.text((54, top + 311), fit(d, txt, font(17, 'bold'), 372), font=font(17, 'bold'), fill=(150, 240, 180))
        # ícones (6 por linha)
        self.icon_boxes = []
        gx, gy, size, gap = 462, top + 20, 64, 14
        for i, (cls, label, sym, col) in enumerate(APPS):
            cx = gx + (i % 6) * (size + gap)
            cy = gy + (i // 6) * (size + 40)
            box = (cx, cy, cx + size, cy + size)
            self.icon_boxes.append((cx, cy, cx + size, cy + size + 26))
            d.rounded_rectangle(box, 18, fill=col)
            center_text(d, box, sym, font(32, 'bold'), (255, 255, 255))
            center_text(d, (cx - 14, cy + size + 2, cx + size + 14, cy + size + 24), label, font(14), FG)

    def draw_dock(self, d):
        y0 = H - DOCK_H
        d.rectangle((0, y0, W, H), fill=PANEL)
        self.regions['home'] = (6, y0 + 6, 76, H - 6)
        button(d, self.regions['home'], '⌂', font(26, 'bold'), fill=ACCENT if self.focus is None else CARD,
               color=(20, 20, 30) if self.focus is None else FG)
        x = 86
        stop = self.tars_active()
        right_w = 150 + (126 if stop else 0)
        tw = (W - x - right_w) / max(1, len(self.windows))
        tw = min(tw, 200)
        for i, app in enumerate(self.windows):
            box = (int(x), y0 + 6, int(x + tw - 6), H - 6)
            active = app is self.focus
            d.rounded_rectangle(box, 10, fill=(60, 66, 100) if active else CARD)
            d.text((box[0] + 10, box[1] + 10), app.title[:14], font=font(15, 'bold' if active else 'sans'), fill=FG)
            close = (box[2] - 34, box[1] + 4, box[2] - 4, box[3] - 4)
            d.rounded_rectangle(close, 8, fill=(80, 40, 50))
            center_text(d, close, '✕', font(15, 'bold'), FG)
            self.regions[f'close:{i}'] = close
            self.regions[f'tab:{i}'] = (box[0], box[1], close[0] - 1, box[3])
            x += tw
        self.regions.pop('stop', None)
        if stop:
            self.regions['stop'] = (W - 144 - 124, y0 + 6, W - 150, H - 6)
            button(d, self.regions['stop'], '■ PARAR', font(18, 'bold'), fill=(205, 55, 55), color=(255, 255, 255), radius=12)
        self.regions['split'] = (W - 144, y0 + 6, W - 78, H - 6)
        button(d, self.regions['split'], '◫', font(24, 'bold'), fill=ACCENT if self.split else CARD,
               color=(20, 20, 30) if self.split else FG)
        self.regions['kb'] = (W - 72, y0 + 6, W - 6, H - 6)
        button(d, self.regions['kb'], '⌨', font(24), fill=ACCENT if self.keyboard_visible() else CARD,
               color=(20, 20, 30) if self.keyboard_visible() else FG)

    def blit(self, img):
        os.pwrite(self.fb, img.transpose(Image.Transpose.ROTATE_270).convert('RGBA').tobytes(), 0)

    # ---------------------------------------------------------- controle (testes)
    def open_ctl(self):
        try:
            if not os.path.exists(CTL_FIFO):
                os.mkfifo(CTL_FIFO, 0o600)
            self.ctl_fd = os.open(CTL_FIFO, os.O_RDWR | os.O_NONBLOCK)   # RDWR: nunca dá EOF
            self.ctl_buf = b''
        except OSError as e:
            log('sem FIFO de controle:', e)

    def on_ctl(self):
        try:
            data = os.read(self.ctl_fd, 4096)
        except BlockingIOError:
            return
        if not data:
            return
        self.ctl_buf += data
        while b'\n' in self.ctl_buf:
            line, self.ctl_buf = self.ctl_buf.split(b'\n', 1)
            p = line.decode(errors='replace').split(' ', 1)
            log('ctl:', line[:80])
            if p[0] == 'tap' and len(p) == 2:
                x, y = map(float, p[1].split())
                self.tap(x, y)
            elif p[0] == 'text' and len(p) == 2 and self.focus:
                self.focus.on_text(p[1].encode().decode('unicode_escape').encode())
            elif p[0] == 'open' and len(p) == 2:
                import unicodedata
                norm = lambda t: ''.join(c for c in unicodedata.normalize('NFD', t.lower()) if unicodedata.category(c) != 'Mn').replace(' ', '')
                for cls, label, _, _ in APPS:
                    if norm(label) == norm(p[1]):
                        self.open_app(cls)
            elif p[0] == 'home':
                self.focus = None
            elif p[0] == 'idle':
                self.enter_ambient()
            elif p[0] == 'say' and len(p) == 2:
                self.speak(p[1], force=True)
            elif p[0] == 'ask' and len(p) == 2:
                app = self.open_app(TarsApp, greet=False)
                app.input = p[1]
                app.submit()
            elif p[0] == 'ring':
                self.ring({'id': 0, 'kind': 'timer', 'label': 'Teste de alarme', 'at': time.time()})
            elif p[0] == 'confirm':
                threading.Thread(target=lambda: log('confirm ->', self.ask_confirm('Teste de confirmação:\n$ ls -la /home/user')), daemon=True).start()
            elif p[0] == 'wake':
                self.wake()
            self.dirty = True

    # ---------------------------------------------------------- laço
    def run(self):
        try:
            os.pwrite(self.fb, open(SPLASH, 'rb').read(), 0)
            time.sleep(1.2)
        except OSError:
            pass
        self.open_ctl()
        self.rewatch()
        start_http(self)
        threading.Thread(target=warm_greetings, daemon=True).start()
        threading.Thread(target=weather_loop, daemon=True).start()
        threading.Thread(target=news_loop, daemon=True).start()
        self.make_sfx()
        self.setup_als()
        self.voice.start()
        last_tick = 0
        while True:
            anim = self.ambient or any(getattr(a, 'animated', False) for a, _ in self.visible())
            timeout = 0.03 if self.dirty else (0.1 if anim else 0.5)
            try:
                r, _, _ = select.select(list(self.watch), [], [], timeout)
            except OSError:
                self.rewatch()
                continue
            for fd in r:
                kind, obj = self.watch.get(fd, (None, None))
                if kind == 'touch':
                    for ev in self.touch.read():
                        self.on_touch_event(ev)
                elif kind == 'button':
                    for ev in obj.read():
                        self.on_button_event(ev)
                elif kind == 'app':
                    obj.on_fd(fd)
                    if not obj.fds():
                        self.rewatch()
                    if obj in [a for a, _ in self.visible()]:
                        self.dirty = True
                elif kind == 'ctl':
                    self.on_ctl()
                elif kind == 'als':
                    self.on_als()
            now = time.monotonic()
            if now - last_tick >= 1.0:
                last_tick = now
                reap()
                while self.pending:
                    self.wake()
                    self.open_app(BrowserApp).go(self.pending.pop(0))
                for item in lstools.due_timers():
                    self.ring(item)
                if self.modal and now - self.modal['t0'] > self.modal['timeout'] + 2:
                    self.modal['ev'].set()
                self.auto_brightness_tick()
                act = self.tars_active()
                if act != self.last_active:
                    self.last_active = act
                    self.dirty = True
                busy = os.path.exists(TARS_SPEAKING) or self.modal is not None or self.ringing is not None or any(
                    getattr(w, 'listening', False) or getattr(w, 'thinking', False) for w in self.windows)
                if busy:
                    self.last_input = now
                elif not self.ambient and now - self.last_input > IDLE_S:
                    self.enter_ambient()
                for app in self.windows:
                    if app in [a for a, _ in self.visible()]:
                        app.tick()
                        if app.dirty:
                            self.dirty = True
                sm = media_mtime()
                if sm != getattr(self, 'spotify_mtime', 0):
                    self.spotify_mtime = sm
                    self.on_media_change()
                clock = time.strftime('%H:%M')
                if clock != self.last_clock:
                    self.last_clock = clock
                    self.dirty = True
                if self.toast_until and now > self.toast_until:
                    self.toast_until = 0
                    self.dirty = True
            if anim and now - self.last_render >= 0.12:
                self.dirty = True
            if self.dirty and now - self.last_render >= 0.05:
                self.render()


def news_loop():
    time.sleep(60)
    while True:
        try:
            lstools.news_refresh(['g1', 'tecnologia'])
        except Exception as e:
            log('news_loop', e)
        time.sleep(1800)


def weather_loop():
    time.sleep(45)
    while True:
        try:
            lstools.weather()
        except Exception as e:
            log('clima:', e)
        time.sleep(1800)


def warm_greetings():
    """Pré-gera (e guarda em cache) o áudio das saudações; só chama a API para o que ainda não está em disco."""
    time.sleep(25)                                         # espera a rede subir
    for line in [ln.replace('{dono}', owner_name('comandante')) for ln in GREETING_LINES]:
        try:
            subprocess.run(['/usr/local/bin/tars-say', '--warm', line], timeout=60,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass
        time.sleep(1.5)
    # voz local (Piper): gera as saudações no cache para tocarem na hora quando o Gemini TTS estiver sem cota
    while not os.path.exists('/run/luizinhoshowos/piper-ready'):
        time.sleep(10)
    for line in [ln.replace('{dono}', owner_name('comandante')) for ln in GREETING_LINES]:
        try:
            subprocess.run(['/usr/bin/nice', '-n', '15', '/usr/local/bin/tars-say', '--warm-piper', line], timeout=120,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass


def reap():
    """Recolhe processos filhos que terminaram (aplay, sh...). Os terminais tratam ECHILD."""
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


# ======================================================================= tela ao vivo
PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><title>TarsOS ao vivo</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;background:#0c0e1a;color:#e4e6ee;font-family:system-ui;display:flex;flex-direction:column;
align-items:center;gap:12px;padding:16px}img{max-width:100%;border-radius:14px;box-shadow:0 8px 40px #000a}
small{color:#96a0b8}</style></head><body><h2>TarsOS &middot; tela ao vivo</h2>
<img src="/stream" alt="tela"><small>Echo Show 5 &middot; atualiza em tempo real</small></body></html>"""


def start_http(desk):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def jpeg(self):
            with desk.frame_cv:
                img = desk.frame or Image.new('RGB', (W, H), BG)
            buf = io.BytesIO()
            img.save(buf, 'JPEG', quality=75)
            return buf.getvalue()

        def do_GET(self):
            if self.path == '/':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(PAGE)
            elif self.path.startswith('/shot'):
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.end_headers()
                self.wfile.write(self.jpeg())
            elif self.path == '/stream':
                self.send_response(200)
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
                self.end_headers()
                ver = -1
                try:
                    while True:
                        with desk.frame_cv:
                            desk.frame_cv.wait_for(lambda: desk.frame_ver != ver, timeout=5)
                            ver = desk.frame_ver
                        data = self.jpeg()
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n' % len(data))
                        self.wfile.write(data + b'\r\n')
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.send_error(404)

    srv = ThreadingHTTPServer(('0.0.0.0', HTTP_PORT), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log('tela ao vivo em http://%s:%d' % (ip_addr(), HTTP_PORT))


if __name__ == '__main__':
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    log('TarsOS Desktop iniciando')
    Desktop().run()
