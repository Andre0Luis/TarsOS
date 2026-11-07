#!/usr/bin/env python3
"""Roda comandos no shell root do EchoLinux pela serial USB (CDC-ACM).
Uso: echosh.py 'comando'   (ou sem args: lê comandos do stdin)"""
import glob, sys, time, serial, uuid

def port():
    ps = sorted(glob.glob('/dev/cu.usbmodem*'))
    if not ps: sys.exit('nenhuma porta /dev/cu.usbmodem* encontrada')
    return ps[-1]

def run(s, cmd, timeout=60):
    tag = uuid.uuid4().hex[:8]
    s.reset_input_buffer()
    s.write(f"{cmd}; echo __END_{tag}_$?\n".encode())
    buf = b''; t0 = time.time()
    while time.time() - t0 < timeout:
        buf += s.read(4096)
        marker = f"__END_{tag}_".encode()
        i = buf.rfind(marker)
        if i != -1 and b'\n' in buf[i:]:
            out = buf[:i].decode(errors='replace')
            rc = buf[i+len(marker):].split(b'\n')[0].strip().decode()
            lines = out.splitlines()
            # remove o eco do comando
            lines = [l for l in lines if f"__END_{tag}_" not in l]
            return '\n'.join(lines).strip('\r\n'), rc
    return buf.decode(errors='replace'), 'TIMEOUT'

def push(s, local, remote):
    import base64, gzip
    data = base64.b64encode(gzip.compress(open(local, 'rb').read(), 9)).decode()
    run(s, f"rm -f {remote}.b64", 10)
    for i in range(0, len(data), 3000):
        out, rc = run(s, f"echo '{data[i:i+3000]}' >> {remote}.b64", 20)
        if rc != '0': sys.exit(f'falha no bloco {i}: {out}')
    out, rc = run(s, f"base64 -d {remote}.b64 | gunzip > {remote} && rm {remote}.b64 && wc -c < {remote}", 60)
    print(f'enviado {local} -> {remote} ({out.strip()} bytes)')

if __name__ == '__main__':
    s = serial.Serial(port(), 115200, timeout=0.3)
    s.write(b'\n'); time.sleep(0.5); s.reset_input_buffer()
    run(s, 'stty -echo 2>/dev/null; export PS1=""', 5)
    if len(sys.argv) == 4 and sys.argv[1] == '--push':
        push(s, sys.argv[2], sys.argv[3]); sys.exit(0)
    cmds = [' '.join(sys.argv[1:])] if len(sys.argv) > 1 else [l for l in sys.stdin.read().split('\n') if l.strip()]
    for c in cmds:
        out, rc = run(s, c)
        print(out)
        if rc not in ('0',): print(f'[rc={rc}]', file=sys.stderr)
