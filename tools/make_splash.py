#!/usr/bin/env python3
"""Gera a tela de boas-vindas do TarsOS como framebuffer cru RGBA 480x960.
A tela física é paisagem 960x480 e o painel é retrato: rotação 270 deixa na orientação certa."""
import sys
from PIL import Image, ImageDraw, ImageFont
NAME, VERSION, CODENAME, DATE = 'TarsOS', '0.2.0', 'Companheiro', '06/11/2026'
W, H = 960, 480
out = sys.argv[1]; rot = int(sys.argv[2]) if len(sys.argv) > 2 else 270
img = Image.new('RGB', (W, H)); d = ImageDraw.Draw(img)
for y in range(H):
    c = int(14 + 28 * y / H); d.line([(0, y), (W, y)], fill=(c // 2, c // 3 + 4, c + 18))
def font(sz, bold=False):
    path = '/System/Library/Fonts/Supplemental/Arial Bold.ttf' if bold else '/System/Library/Fonts/Supplemental/Arial.ttf'
    try: return ImageFont.truetype(path, sz)
    except OSError: return ImageFont.load_default()
# faixa de destaque com as três cores primárias (também serve de teste de cor)
for i, col in enumerate(((225, 45, 45), (45, 195, 70), (55, 95, 235))):
    d.rectangle([0, 0 + i * 6, W, 5 + i * 6], fill=col)
d.text((48, 70), NAME, font=font(80, True), fill=(255, 255, 255))
d.text((52, 165), f'versão {VERSION} · "{CODENAME}" · {DATE}', font=font(28), fill=(255, 205, 80))
d.text((52, 215), 'Linux nativo no Echo Show 5 (1ª geração)', font=font(26), fill=(180, 205, 255))
d.text((52, 255), 'sem Android · sem Alexa · com o TARS', font=font(24), fill=(200, 200, 200))
fine = 'baseado em postmarketOS (Nura edge) e Alpine Linux · kernel Linux 4.9.77 · bootloader amonet'
d.text((52, H - 46), fine, font=font(16), fill=(140, 150, 170))
img = img.rotate(rot, expand=True)
assert img.size == (480, 960)
open(out, 'wb').write(img.convert('RGBA').tobytes()); img.rotate(-rot, expand=True).save(out + '.png')
print('ok', out)
