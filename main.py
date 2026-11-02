#!/usr/bin/env python3
"""Entrada común de AppImage, paquete DEB y ejecución desde fuentes."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
VERSION = '1.0.0'
if __name__ == '__main__':
    if sys.argv[1:] == ['--version']:
        print(f'UDPBD PS2 Server {VERSION}')
    elif sys.argv[1:] == ['--self-test']:
        from self_test import run
        run()
    elif sys.argv[1:]:
        raise SystemExit('Uso: udpbd-ps2-server [--version | --self-test]')
    else:
        import tkinter as tk
        from lanzador import Launcher
        root = tk.Tk(className='Udpbd-ps2-server')
        Launcher(root)
        root.mainloop()
