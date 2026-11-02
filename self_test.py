"""Prueba instalada: GUI + UDP real, exclusivamente loopback y archivos ficticios."""
from pathlib import Path
import socket
import struct
import tempfile
import time
import tkinter as tk
from unittest.mock import patch
import lanzador as module

def run():
    def spin(root, condition):
        deadline = time.monotonic() + 10
        while not condition() and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        assert condition(), 'Timeout esperando al servidor'
    popen = module.subprocess.Popen
    with tempfile.TemporaryDirectory(prefix='udpbd-package-test-') as tmp:
        base = Path(tmp)
        games = base / 'DVD'
        games.mkdir()
        original = bytes(range(256)) * 800
        iso = games / 'Prueba.iso'
        iso.write_bytes(original)
        module.SETTINGS = base / 'settings.json'
        module.STATE = base / 'state'
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(('127.0.0.1', 0))
            address = probe.getsockname()
        root = tk.Tk()
        root.withdraw()
        app = module.Launcher(root)
        app.ips = [('127.0.0.1 — lo', '127.0.0.1')]
        app.bind_ip.set(app.ips[0][0])
        app.source.set(str(games))
        def spawn(command, **kwargs):
            command = list(command)
            command[command.index('--bind') + 1] = '127.0.0.1'
            return popen([command[0], '--port', str(address[1]), *command[1:]], **kwargs)
        def hdr(cmd, ident=0, packet=0):
            return struct.pack('<H', cmd | ident << 5 | packet << 8)
        try:
            with patch.object(module.subprocess, 'Popen', side_effect=spawn):
                app.start_button.invoke()
                spin(root, lambda: app.state == 'running')
            assert '--virtual' in app.proc.args and '--read-write' in app.proc.args
            assert app.proc.args[app.proc.args.index('--packet-delay-us') + 1] == '500'
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.settimeout(3)
                client.sendto(hdr(0), address)
                info = client.recv(2048)
                assert info[0] & 31 == 1 and len(info) == 10
                sector_size, sectors = struct.unpack('<II', info[2:])
                assert sector_size == 512
                client.sendto(hdr(2) + struct.pack('<IH', 2048, 1), address)
                assert client.recv(2048)[9:17] == b'EXFAT   '
                last = sectors - 1
                client.sendto(hdr(4, 2) + struct.pack('<IH', last, 1), address)
                bt = 5 | (4 << 4) | (0x7ffff << 13)
                client.sendto(hdr(5, 2, 1) + struct.pack('<I', bt) + b'W' * 512, address)
                assert client.recv(2048) == hdr(6, 2, 3) + struct.pack('<i', 0)
                client.sendto(hdr(2, 2) + struct.pack('<IH', last, 1), address)
                assert client.recv(2048)[6:] == b'W' * 512
            spin(root, lambda: len(app.traffic.get_children()) >= 8)
            session = Path(app.session.name)
            app.start_button.invoke()
            spin(root, lambda: app.state == 'stopped')
            assert not session.exists() and iso.read_bytes() == original
            # Restart proves that a temporary write has not survived.
            with patch.object(module.subprocess, 'Popen', side_effect=spawn):
                app.start_button.invoke()
                spin(root, lambda: app.state == 'running')
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.settimeout(3)
                client.sendto(hdr(2, 2) + struct.pack('<IH', last, 1), address)
                assert client.recv(2048)[6:] == bytes(512)
            app.close()
            spin(root, lambda: app.state == 'stopped')
            print('OK: GUI, icono, INFO/READ/WRITE UDP, bits reservados OPL, pacing, reinicio temporal y originales intactos.')
        finally:
            if app.proc and app.proc.poll() is None:
                app.proc.terminate()
                app.proc.wait(timeout=5)
            try:
                root.destroy()
            except tk.TclError:
                pass
