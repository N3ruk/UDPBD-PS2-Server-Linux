#!/usr/bin/env python3
"""Ciclo real de la GUI, oculta; loopback, carpeta temporal, sin discos ni PS2."""
from pathlib import Path
import json
import socket
import struct
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import lanzador as module
real_popen = subprocess.Popen


def spin(root, condition, timeout=6):
    limit = time.monotonic() + timeout
    while not condition() and time.monotonic() < limit:
        root.update()
        time.sleep(.015)
    assert condition(), 'Tiempo agotado esperando el estado de la app'


with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp)
    source = folder / 'juegos'
    (source / 'DVD').mkdir(parents=True)
    game = source / 'DVD' / 'Prueba.iso'
    original = bytes(range(256)) * 800
    game.write_bytes(original)
    module.SETTINGS = folder / 'prefs.json'
    module.SETTINGS.write_text(json.dumps({'persistent': True}))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(('127.0.0.1', 0))
        address = probe.getsockname()
    root = tk.Tk()
    root.withdraw()
    app = module.Launcher(root)
    app.ips = [('127.0.0.1 — lo', '127.0.0.1')]
    app.bind_ip.set(app.ips[0][0])
    app.source.set(str(source))
    module.STATE = folder / 'logs'

    def local_server(command, **kwargs):
        command = list(command)
        command[command.index('--bind') + 1] = '127.0.0.1'
        return real_popen([*command[:1], '--port', str(address[1]), *command[1:]], **kwargs)

    try:
        with patch.object(module.subprocess, 'Popen', side_effect=local_server):
            app.start_button.invoke()
            spin(root, lambda: app.state == 'running')
        assert app.start_button['text'] == 'Parar servidor'
        assert str(app.source_entry['state']) == 'disabled'
        assert '1 archivos' in app.summary.get(), app.summary.get()
        session_path = Path(app.session.name)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(2)
            client.sendto(b'\x60\x00', address)
            response = client.recv(2048)
            assert response[:2] == b'\x61\x01'
            assert struct.unpack('<II', response[2:])[0] == 512
            info_sectors = struct.unpack('<II', response[2:])[1]
            client.sendto(b'\x42\x00' + struct.pack('<IH', 2048, 1), address)
            response = client.recv(2048)
            assert response[0] & 31 == 3
            assert response[6:17] == b'\xeb\x76\x90EXFAT   '
        spin(root, lambda: len(app.traffic.get_children()) >= 4)
        assert '2 paquetes' in app.rx.get(), app.rx.get()
        assert '2 paquetes' in app.tx.get(), app.tx.get()
        assert '--read-write' in app.proc.args
        assert '--virtual' in app.proc.args, 'Las preferencias antiguas no deben activar persistencia'
        assert '--packet-delay-us' in app.proc.args
        # GUI-launched server accepts real writes; originals remain unchanged.
        from test_udpbd import hdr, rdma
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(2)
            last = info_sectors - 1
            client.sendto(hdr(4, 2) + struct.pack('<IH', last, 1), address)
            client.sendto(rdma(b'W' * 512, 2), address)
            assert client.recv(2048) == hdr(6, 2, 3) + struct.pack('<i', 0)
            client.sendto(hdr(2, 2) + struct.pack('<IH', last, 1), address)
            assert client.recv(2048)[6:] == b'W' * 512
        assert game.read_bytes() == original
        app.start_button.invoke()
        spin(root, lambda: app.state == 'stopped')
        assert app.proc is None
        assert not session_path.exists(), 'No se limpiaron los temporales'
        assert app.start_button['text'] == 'Iniciar servidor'
        assert app.status.get() == 'Detenido'
        assert game.read_bytes() == original
        # Restart same button, then change source: error is reflected and cleaned up.
        with patch.object(module.subprocess, 'Popen', side_effect=local_server):
            app.start_button.invoke()
            spin(root, lambda: app.state == 'running')
        game.write_bytes(b'changed')
        spin(root, lambda: app.state == 'stopped')
        assert app.status.get().startswith('Error')
        assert 'cambiado' in app.log.get('1.0', 'end')
        # Closing an active window stops the server and releases its resources.
        with patch.object(module.subprocess, 'Popen', side_effect=local_server):
            app.start_button.invoke()
            spin(root, lambda: app.state == 'running')
        proc = app.proc
        app.close()
        spin(root, lambda: app.state == 'stopped')
        assert proc.poll() is not None
        print('GUI OK: IP, carpeta, botón único, INFO/READ/WRITE UDP, COW sin tocar ISO, RX/TX visibles, parada, limpieza, reinicio, modificación y cierre activo.')
    finally:
        if app.proc and app.proc.poll() is None:
            app.proc.terminate()
            app.proc.wait(timeout=3)
        try:
            root.destroy()
        except tk.TclError:
            pass

