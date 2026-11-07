#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The TarsOS Authors
"""TarsOS Terminal: terminal com teclado touch direto no framebuffer.

O kernel do Echo Show 5 não tem fbcon, então este programa faz tudo sozinho:
  - roda um shell num pseudo-terminal (pty) e emula VT100/xterm com o pyte;
  - desenha a tela (paisagem 960x480) com o Pillow e grava em /dev/fb0
    (painel em retrato 480x960, RGBA, girado 270 graus);
  - lê o touch (goodix-ts) e os botões de volume pelo evdev.

Atalhos físicos: Volume + mostra/esconde o teclado; Volume - envia Ctrl+C.
Variáveis: LST_FONT_SIZE (padrão 17), LST_TOUCH (mapeamento, ver touch_to_screen).
"""
import errno
import fcntl
import os
import pty
import select
import signal
import socket
import struct
import sys
import termios
import time

import evdev
import pyte
from PIL import Image, ImageDraw, ImageFont

FB_DEV = '/dev/fb0'
W, H = 960, 480                        # tela física (paisagem)
FONT_DIR = '/usr/share/fonts/dejavu'
FONT_SIZE = int(os.environ.get('LST_FONT_SIZE', '17'))
STATUS_H = 24
KB_H = 220
SPLASH = '/usr/share/luizinhoshowos/splash.raw'
SHELL = ['/bin/bash', '-l'] if os.path.exists('/bin/bash') else ['/bin/sh', '-l']

BG = (12, 14, 26)
FG = (225, 228, 235)
ACCENT = (255, 205, 80)

# 16 cores ANSI (estilo xterm, um pouco suavizadas)
ANSI = {
    'black': (40, 42, 54), 'red': (230, 80, 80), 'green': (90, 200, 110), 'brown': (220, 180, 70),
    'blue': (90, 140, 240), 'magenta': (200, 110, 220), 'cyan': (80, 200, 210), 'white': (220, 220, 225),
    'brightblack': (110, 112, 125), 'brightred': (255, 120, 120), 'brightgreen': (140, 240, 150),
    'brightbrown': (255, 225, 120), 'brightblue': (140, 180, 255), 'brightmagenta': (240, 150, 250),
    'brightcyan': (130, 235, 240), 'brightwhite': (255, 255, 255),
}


def color(name, default):
    if name == 'default':
        return default
    if name in ANSI:
        return ANSI[name]
    if len(name) == 6:                 # pyte usa hex sem '#' para 256 cores / truecolor
        try:
            return tuple(int(name[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return default


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


# ---------------------------------------------------------------- teclado touch
SPECIAL = {
    'Esc': '\x1b', 'Tab': '\t', 'Enter': '\r', '⌫': '\x7f', 'Espaço': ' ',
    '←': '\x1b[D', '→': '\x1b[C', '↑': '\x1b[A', '↓': '\x1b[B',
}
LAYERS = {
    'abc': [
        ['Esc', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '⌫'],
        ['Tab', 'q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p', '-'],
        ['Ctrl', 'a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', ';', 'Enter'],
        ['Shift', 'z', 'x', 'c', 'v', 'b', 'n', 'm', ',', '.', '/', '|'],
        ['Sym', '←', '↓', '↑', '→', 'Espaço', '~', "'", '⌨'],
    ],
    'sym': [
        ['Esc', '!', '@', '#', '$', '%', '^', '&', '*', '(', ')', '⌫'],
        ['Tab', '`', '~', '_', '=', '+', '[', ']', '{', '}', '\\', '-'],
        ['Ctrl', '"', "'", ':', ';', '<', '>', '?', '/', '|', '&', 'Enter'],
        ['Shift', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '.'],
        ['abc', '←', '↓', '↑', '→', 'Espaço', '$', '*', '⌨'],
    ],
}
WIDE = {'Espaço': 4.0, 'Enter': 1.6, 'Shift': 1.5, 'Ctrl': 1.4, 'Tab': 1.3, 'Esc': 1.2, '⌫': 1.4,
        'Sym': 1.3, 'abc': 1.3, '⌨': 1.3}


class Keyboard:
    def __init__(self, font):
        self.font = font
        self.layer = 'abc'
        self.shift = False
        self.ctrl = False
        self.pressed = None
        self.keys = []                  # (rect, label)
        self.layout()

    def layout(self):
        self.keys = []
        rows = LAYERS[self.layer]
        rh = KB_H / len(rows)
        for r, row in enumerate(rows):
            total = sum(WIDE.get(k, 1.0) for k in row)
            x = 0.0
            for k in row:
                w = W * WIDE.get(k, 1.0) / total
                y0 = H - KB_H + r * rh
                self.keys.append(((int(x) + 2, int(y0) + 2, int(x + w) - 2, int(y0 + rh) - 2), k))
                x += w

    def hit(self, x, y):
        for rect, k in self.keys:
            if rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]:
                return k
        return None

    def press(self, k):
        """Converte a tecla em bytes para o shell (ou muda o estado do teclado)."""
        if k in ('Sym', 'abc'):
            self.layer = 'sym' if k == 'Sym' else 'abc'
            self.layout()
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

    def draw(self, d):
        d.rectangle([0, H - KB_H, W, H], fill=(22, 25, 40))
        for rect, k in self.keys:
            active = (k == 'Shift' and self.shift) or (k == 'Ctrl' and self.ctrl) or k == self.pressed
            special = k in SPECIAL or k in ('Shift', 'Ctrl', 'Sym', 'abc', '⌨')
            fill = (255, 205, 80) if active else ((48, 54, 82) if special else (64, 70, 102))
            d.rounded_rectangle(rect, radius=7, fill=fill)
            label = k.upper() if (self.shift and len(k) == 1 and k.isalpha()) else k
            tw = d.textlength(label, font=self.font)
            cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
            d.text((cx - tw / 2, cy - self.font.size * 0.62), label, font=self.font,
                   fill=(20, 20, 30) if active else (235, 238, 245))


# ---------------------------------------------------------------- terminal
class Terminal:
    def __init__(self):
        self.font = ImageFont.truetype(f'{FONT_DIR}/DejaVuSansMono.ttf', FONT_SIZE)
        self.font_bold = ImageFont.truetype(f'{FONT_DIR}/DejaVuSansMono-Bold.ttf', FONT_SIZE)
        self.ui_font = ImageFont.truetype(f'{FONT_DIR}/DejaVuSans-Bold.ttf', 15)
        self.key_font = ImageFont.truetype(f'{FONT_DIR}/DejaVuSans.ttf', 20)
        self.cw = round(self.font.getlength('M'))
        asc, desc = self.font.getmetrics()
        self.ch = asc + desc + 1
        self.kb = Keyboard(self.key_font)
        self.kb_visible = True
        self.fb = os.open(FB_DEV, os.O_RDWR)
        self.dirty = True
        self.last_render = 0.0
        self.last_clock = ''
        self.touch_dev, self.key_devs = self.find_inputs()
        self.slot_xy = {}
        self.cur_slot = 0
        self.down_slot_pos = {}
        self.spawn_shell()

    # -- dimensões do terminal conforme o teclado está visível ou não
    def term_area(self):
        bottom = H - (KB_H if self.kb_visible else 0)
        return STATUS_H, bottom

    def grid(self):
        top, bottom = self.term_area()
        return max(10, W // self.cw), max(3, (bottom - top - 4) // self.ch)

    def spawn_shell(self):
        cols, rows = self.grid()
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        pid, fd = pty.fork()
        if pid == 0:
            env = dict(os.environ, TERM='xterm-256color', HOME='/root', LANG='C.UTF-8',
                       PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
            os.chdir('/root')
            os.execvpe(SHELL[0], SHELL, env)
        self.pid, self.pty = pid, fd
        self.set_winsize(cols, rows)
        log('shell iniciado pid', pid, 'grade', cols, 'x', rows)

    def set_winsize(self, cols, rows):
        fcntl.ioctl(self.pty, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    def resize(self):
        cols, rows = self.grid()
        self.screen.resize(rows, cols)
        self.set_winsize(cols, rows)
        try:
            os.kill(self.pid, signal.SIGWINCH)
        except ProcessLookupError:
            pass
        self.dirty = True

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

    # -- mapeamento do touch (coordenadas do painel em retrato) para a tela paisagem
    def touch_to_screen(self, rx, ry):
        mode = os.environ.get('LST_TOUCH', 'default')
        if mode == 'default':          # painel 480x960 girado 270 graus
            return ry * W / 960.0, (480 - rx) * H / 480.0
        if mode == 'flip':
            return (960 - ry) * W / 960.0, rx * H / 480.0
        return rx, ry

    # -- eventos
    def on_touch(self, ev):
        if ev.type != evdev.ecodes.EV_ABS:
            if ev.type == evdev.ecodes.EV_SYN and ev.code == evdev.ecodes.SYN_REPORT:
                self.on_frame()
            return
        c = ev.code
        if c == evdev.ecodes.ABS_MT_SLOT:
            self.cur_slot = ev.value
        elif c == evdev.ecodes.ABS_MT_TRACKING_ID:
            st = self.slot_xy.setdefault(self.cur_slot, {})
            if ev.value == -1:
                st['up'] = True
            else:
                st.clear()
                st['new'] = True
        elif c == evdev.ecodes.ABS_MT_POSITION_X:
            self.slot_xy.setdefault(self.cur_slot, {})['x'] = ev.value
        elif c == evdev.ecodes.ABS_MT_POSITION_Y:
            self.slot_xy.setdefault(self.cur_slot, {})['y'] = ev.value

    def on_frame(self):
        for slot, st in list(self.slot_xy.items()):
            if st.get('new') and 'x' in st and 'y' in st:
                st['new'] = False
                x, y = self.touch_to_screen(st['x'], st['y'])
                log(f'toque bruto=({st["x"]},{st["y"]}) tela=({x:.0f},{y:.0f})')
                self.on_tap(x, y)
            if st.get('up'):
                del self.slot_xy[slot]
                if self.kb.pressed:
                    self.kb.pressed = None
                    self.dirty = True

    def on_tap(self, x, y):
        if self.kb_visible and y >= H - KB_H:
            k = self.kb.hit(x, y)
            if not k:
                return
            self.kb.pressed = k
            self.dirty = True
            if k == '⌨':
                self.toggle_keyboard()
                return
            data = self.kb.press(k)
            if data:
                self.write(data)
        elif not self.kb_visible:
            self.toggle_keyboard()

    def on_button(self, ev):
        if ev.type == evdev.ecodes.EV_KEY and ev.value == 1:
            if ev.code == evdev.ecodes.KEY_VOLUMEUP:
                self.toggle_keyboard()
            elif ev.code == evdev.ecodes.KEY_VOLUMEDOWN:
                self.write(b'\x03')

    def toggle_keyboard(self):
        self.kb_visible = not self.kb_visible
        self.kb.pressed = None
        self.resize()

    def write(self, data):
        try:
            os.write(self.pty, data)
        except OSError:
            pass

    # -- desenho
    def render(self):
        img = Image.new('RGB', (W, H), BG)
        d = ImageDraw.Draw(img)
        # barra de status
        d.rectangle([0, 0, W, STATUS_H], fill=(20, 22, 38))
        d.text((10, 3), 'TarsOS Terminal', font=self.ui_font, fill=ACCENT)
        right = f'{socket.gethostname()}  {ip_addr()}  {time.strftime("%H:%M")}'
        d.text((W - 10 - d.textlength(right, font=self.ui_font), 3), right, font=self.ui_font, fill=(170, 180, 200))
        # texto do terminal
        top, _ = self.term_area()
        y0 = top + 2
        buf = self.screen.buffer
        for row in range(self.screen.lines):
            line = buf[row]
            y = y0 + row * self.ch
            col = 0
            while col < self.screen.columns:
                ch = line[col]
                attrs = (ch.fg, ch.bg, ch.bold, ch.reverse)
                start = col
                text = []
                while col < self.screen.columns:
                    c = line[col]
                    if (c.fg, c.bg, c.bold, c.reverse) != attrs:
                        break
                    text.append(c.data or ' ')
                    col += 1
                fg = color(attrs[0], FG)
                bg = color(attrs[1], BG)
                if attrs[3]:
                    fg, bg = bg, fg
                x = start * self.cw
                if bg != BG:
                    d.rectangle([x, y, x + len(text) * self.cw, y + self.ch], fill=bg)
                s = ''.join(text)
                if s.strip():
                    d.text((x, y), s, font=self.font_bold if attrs[2] else self.font, fill=fg)
        # cursor
        cur = self.screen.cursor
        if not cur.hidden:
            cx, cy = cur.x * self.cw, y0 + cur.y * self.ch
            d.rectangle([cx, cy, cx + self.cw - 1, cy + self.ch - 1], outline=ACCENT, width=2)
        if self.kb_visible:
            self.kb.draw(d)
        self.blit(img)
        self.dirty = False
        self.last_render = time.monotonic()

    def blit(self, img):
        data = img.transpose(Image.Transpose.ROTATE_270).convert('RGBA').tobytes()
        os.pwrite(self.fb, data, 0)

    def show_splash(self):
        try:
            with open(SPLASH, 'rb') as f:
                os.pwrite(self.fb, f.read(), 0)
            time.sleep(1.5)
        except OSError:
            pass

    # -- laço principal
    def run(self):
        self.show_splash()
        fds = {self.pty: 'pty'}
        if self.touch_dev:
            fds[self.touch_dev.fd] = 'touch'
        for k in self.key_devs:
            fds[k.fd] = k
        while True:
            timeout = 0.03 if self.dirty else 1.0
            r, _, _ = select.select(list(fds), [], [], timeout)
            for fd in r:
                kind = fds[fd]
                if kind == 'pty':
                    try:
                        data = os.read(self.pty, 65536)
                    except OSError as e:
                        if e.errno != errno.EIO:
                            raise
                        data = b''
                    if not data:        # shell terminou: abre outro
                        os.waitpid(self.pid, 0)
                        del fds[self.pty]
                        self.spawn_shell()
                        fds[self.pty] = 'pty'
                        continue
                    self.stream.feed(data)
                    self.dirty = True
                elif kind == 'touch':
                    for ev in self.touch_dev.read():
                        self.on_touch(ev)
                else:
                    for ev in kind.read():
                        self.on_button(ev)
            clock = time.strftime('%H:%M')
            if clock != self.last_clock:
                self.last_clock = clock
                self.dirty = True
            if self.dirty and time.monotonic() - self.last_render >= 0.04:
                t = time.monotonic()
                self.render()
                if os.environ.get('LST_DEBUG'):
                    log(f'render {1000 * (time.monotonic() - t):.0f} ms')


def ip_addr():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('192.0.2.1', 9))    # não envia nada; só escolhe a interface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return 'sem rede'


if __name__ == '__main__':
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    log('TarsOS Terminal iniciando')
    Terminal().run()
