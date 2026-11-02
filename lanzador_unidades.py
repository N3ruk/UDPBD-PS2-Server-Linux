#!/usr/bin/env python3
"""Lanzador local UDPBD; selección explícita, consola y parada del servidor."""
import ipaddress
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

BASE = Path(__file__).resolve().parent


def devices():
    result = subprocess.run(
        ['lsblk', '--json', '--tree', '--paths', '--bytes', '--output',
         'PATH,TYPE,SIZE,FSTYPE,LABEL,MOUNTPOINTS,MODEL,UUID'],
        check=True, capture_output=True, text=True)
    roots = json.loads(result.stdout)['blockdevices']
    rows = []

    def system_mount(item):
        return any(m in ('/', '/boot', '/boot/efi', '/home', '/usr', '/var', '[SWAP]')
                   for m in item.get('mountpoints') or [])

    def contains_system(item):
        return system_mount(item) or any(contains_system(c) for c in item.get('children', []))

    def mounts_under(item):
        result = [m for m in item.get('mountpoints') or [] if m]
        for child in item.get('children', []):
            result.extend(mounts_under(child))
        return result

    def walk(item):
        if item['type'] == 'loop':
            return
        children = item.get('children', [])
        if item['type'] in ('disk', 'part', 'lvm', 'crypt', 'raid0', 'raid1') and not contains_system(item):
            label = item.get('label') or item.get('model') or ''
            rows.append((item['path'], item['type'], f"{int(item['size']) / 2**30:.1f} GiB",
                         item.get('fstype') or '—', label.strip(),
                         ', '.join(mounts_under(item)) or 'Sin montar'))
        for child in children:
            walk(child)

    for root in roots:
        walk(root)
    return rows


class Launcher:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.events = queue.Queue()
        self.closing = False
        root.title('UDPBD — Servidor para PlayStation 2')
        root.geometry('1000x630')
        root.minsize(780, 500)
        frame = ttk.Frame(root, padding=15)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='UDPBD para Ubuntu', font=('', 18, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='Comparte una partición o imagen por UDPBD v2 · UDP 48573').pack(anchor='w', pady=(3, 12))
        ttk.Label(frame, text='En Windows usabas E:; aquí selecciona la partición equivalente, no la carpeta DVD.').pack(anchor='w')
        columns = ('path', 'kind', 'size', 'fs', 'label', 'mount')
        self.tree = ttk.Treeview(frame, columns=columns, show='headings', height=8, selectmode='browse')
        for key, title, width in zip(columns, ('Dispositivo', 'Tipo', 'Tamaño', 'Formato', 'Etiqueta / modelo', 'Montaje'), (140, 55, 80, 65, 170, 320)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=45)
        self.tree.pack(fill='both', expand=True, pady=8)
        self.tree.bind('<<TreeviewSelect>>', self.select)
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill='x')
        ttk.Button(toolbar, text='Actualizar unidades', command=self.refresh).pack(side='left')
        ttk.Button(toolbar, text='Elegir imagen de disco…', command=self.choose_image).pack(side='left', padx=8)
        self.source = tk.StringVar()
        ttk.Label(frame, text='Origen (partición, enlace /dev/disk/by-uuid/… o imagen):').pack(anchor='w', pady=(12, 2))
        ttk.Entry(frame, textvariable=self.source).pack(fill='x')
        settings = ttk.Frame(frame)
        settings.pack(fill='x', pady=10)
        self.writable = tk.BooleanVar(value=False)
        ttk.Checkbutton(settings, text='Permitir escritura de la PS2', variable=self.writable).pack(side='left')
        ttk.Label(settings, text='IP de PS2 (opcional):').pack(side='left', padx=(30, 5))
        self.client = tk.StringVar()
        ttk.Entry(settings, textvariable=self.client, width=16).pack(side='left')
        controls = ttk.Frame(frame)
        controls.pack(fill='x')
        self.start_button = ttk.Button(controls, text='Iniciar servidor', command=self.start)
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(controls, text='Detener', command=self.stop, state='disabled')
        self.stop_button.pack(side='left', padx=8)
        self.status = tk.StringVar(value='Detenido · solo lectura por defecto')
        ttk.Label(controls, textvariable=self.status).pack(side='left', padx=8)
        self.log = tk.Text(frame, height=11, state='disabled', wrap='word', background='#171b22', foreground='#d5e3f3')
        self.log.pack(fill='both', expand=True, pady=(12, 0))
        self.refresh()
        self.load_settings()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def append(self, line):
        self.log.configure(state='normal')
        self.log.insert('end', line)
        if int(self.log.index('end-1c').split('.')[0]) > 1500:
            self.log.delete('1.0', '300.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def refresh(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        try:
            for row in devices():
                self.tree.insert('', 'end', values=row)
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            messagebox.showerror('No se pudieron listar las unidades', str(exc))

    def select(self, _event=None):
        selected = self.tree.selection()
        if selected:
            self.source.set(self.tree.item(selected[0], 'values')[0])

    def choose_image(self):
        path = filedialog.askopenfilename(title='Elegir imagen completa de partición o disco (no una ISO de juego)',
                                         filetypes=[('Imágenes de disco', '*.img *.bin *.raw'), ('Todos', '*')])
        if path:
            self.source.set(path)

    def load_settings(self):
        try:
            data = json.loads((BASE / 'preferencias.json').read_text())
            self.source.set(data.get('source', ''))
            self.client.set(data.get('client', ''))
        except (OSError, ValueError):
            pass

    def save_settings(self, path, client):
        # Prefer the filesystem UUID to avoid /dev/sdX names changing after reconnect.
        stable = path
        for link in Path('/dev/disk/by-uuid').glob('*'):
            if os.path.realpath(link) == os.path.realpath(path):
                stable = str(link)
                break
        try:
            (BASE / 'preferencias.json').write_text(json.dumps({'source': stable, 'client': client}, ensure_ascii=False, indent=2) + '\n')
        except OSError:
            pass

    def start(self):
        if self.proc is not None:
            return
        path, client = self.source.get().strip(), self.client.get().strip()
        if not path or not Path(path).exists():
            messagebox.showerror('Origen', 'Selecciona una partición o una imagen existente.')
            return
        if client:
            try:
                ipaddress.IPv4Address(client)
            except ipaddress.AddressValueError:
                messagebox.showerror('IP de PS2', 'Introduce una dirección IPv4 válida o deja el campo vacío.')
                return
        if self.writable.get() and not messagebox.askyesno('Modo lectura/escritura',
                f'La PS2 podrá modificar directamente {path}.\nLa unidad debe estar desmontada.\n\n¿Activar escritura?'):
            return
        command = [str(BASE / 'udpbd-server')]
        if self.writable.get():
            command.append('--read-write')
        if client:
            command += ['--client', client]
        command += ['--', os.path.abspath(path)]
        access = os.R_OK | (os.W_OK if self.writable.get() else 0)
        if not os.access(path, access):
            pkexec = shutil.which('pkexec')
            if not pkexec:
                messagebox.showerror('Permisos', 'No tienes acceso a esta unidad y no está disponible pkexec (paquete pkexec).')
                return
            command.insert(0, pkexec)
            self.append('Se solicitará autorización del sistema para abrir la unidad.\n')
        try:
            self.proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         encoding='utf-8', errors='replace', bufsize=1)
        except OSError as exc:
            messagebox.showerror('Error al iniciar', str(exc))
            self.proc = None
            return
        self.save_settings(path, client)
        self.start_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.status.set('Iniciando…')
        proc = self.proc

        def reader():
            for line in proc.stdout:
                self.events.put(('log', line))
            self.events.put(('exit', proc.wait()))
            proc.stdout.close()
        threading.Thread(target=reader, daemon=True).start()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.status.set('Deteniendo…')
            except PermissionError:
                self.append('Finaliza o cancela primero la autorización del sistema.\n')
            except ProcessLookupError:
                pass

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'log':
                    self.append(value)
                    if 'Servidor listo' in value:
                        self.status.set('Activo · esperando/conectado a PS2')
                else:
                    self.append(f'Proceso finalizado (código {value}).\n')
                    self.proc = None
                    self.start_button.configure(state='normal')
                    self.stop_button.configure(state='disabled')
                    self.status.set('Detenido' if value == 0 else 'No se pudo iniciar / error: consulta el registro')
                    if self.closing:
                        self.root.destroy()
                        return
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def close(self):
        if self.proc is not None:
            self.closing = True
            self.stop()
        else:
            self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    Launcher(root)
    root.mainloop()
