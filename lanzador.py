#!/usr/bin/env python3
"""UDPBD con carpeta virtual, escritura temporal y monitor de red."""
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
# API del lanzador anterior, conservado en lanzador_unidades.py.
from lanzador_unidades import devices
from ps2_theme import install, Scene, card, wrapped_label, CARD, TEXT, BLUE

BASE = Path(__file__).resolve().parent
SETTINGS = Path.home() / '.config' / 'udpbd-ubuntu' / 'carpetas.json'
STATE = Path.home() / '.local/state/udpbd-ubuntu'
COMMANDS = {0: 'INFO', 1: 'INFO_REPLY', 2: 'READ', 3: 'READ_RDMA',
            4: 'WRITE', 5: 'WRITE_RDMA', 6: 'WRITE_DONE'}


def size_text(n):
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if n < 1024 or unit == 'TiB':
            return f'{n:.1f} {unit}'
        n /= 1024


from network import local_ips


class Launcher:
    def __init__(self, root):
        self.root, self.proc = root, None
        self.events = queue.Queue()
        self.state, self.closing = 'stopped', False
        self.cancelled = threading.Event()
        self.session = None
        self.worker = None
        self.last_stats = (time.monotonic(), 0, 0)
        self.rows = []
        root.title('UDPBD PS2 Server')
        root.iconphoto(True, tk.PhotoImage(master=root, file=str(BASE / 'assets/udpbd-ps2-server.png')))
        root.geometry('1120x820')
        root.minsize(940, 740)
        install(root)
        self.scene = Scene(root)
        self.scene.pack(fill='both', expand=True)
        frame = self.scene.content
        top = ttk.Frame(frame)
        top.pack(fill='x', pady=(0, 10))
        top.columnconfigure(0, weight=3, uniform='panels')
        top.columnconfigure(1, weight=2, uniform='panels')
        source_frame = card(top)
        source_frame.grid(row=0, column=0, sticky='nsew', padx=(0, 6))
        network = card(top)
        network.grid(row=0, column=1, sticky='nsew', padx=(6, 0))
        ttk.Label(source_frame, text='01  /  CARPETA COMPARTIDA', style='Section.TLabel').pack(anchor='w', pady=(0, 10))
        source_row = ttk.Frame(source_frame, style='Inner.TFrame')
        source_row.pack(fill='x')
        self.source = tk.StringVar()
        self.source_entry = ttk.Entry(source_row, textvariable=self.source)
        self.source_entry.pack(side='left', fill='x', expand=True)
        self.choose_button = ttk.Button(source_row, text='Seleccionar carpeta…', command=self.choose_folder)
        self.choose_button.pack(side='left', padx=(10, 0))
        wrapped_label(source_frame, text='Raíz OPL, DVD/CD o ISO sueltas. Sin copiar juegos; escritura temporal y originales intactos.',
                      style='CardHint.TLabel').pack(anchor='w', fill='x', pady=(12, 0))
        ttk.Label(network, text='02  /  RED  ·  UDP 48573', style='Section.TLabel').pack(anchor='w', pady=(0, 10))
        network_row = ttk.Frame(network, style='Inner.TFrame')
        network_row.pack(fill='x')
        self.bind_ip = tk.StringVar()
        self.ip_box = ttk.Combobox(network_row, textvariable=self.bind_ip, state='readonly', width=22)
        self.ip_box.pack(side='left', fill='x', expand=True)
        self.refresh_button = ttk.Button(network_row, text='Actualizar', command=self.refresh_ips)
        self.refresh_button.pack(side='left', padx=(8, 0))
        self.ip_hint = tk.StringVar(value='IP del equipo · selecciona la interfaz conectada a tu PS2.')
        wrapped_label(network, textvariable=self.ip_hint, style='CardHint.TLabel').pack(anchor='w', fill='x', pady=(12, 0))
        controls = card(frame)
        controls.pack(fill='x', pady=(0, 10))
        control_row = ttk.Frame(controls, style='Inner.TFrame')
        control_row.pack(fill='x')
        self.start_button = ttk.Button(control_row, text='Iniciar servidor', style='Action.TButton', command=self.toggle)
        self.start_button.pack(side='left')
        self.status = tk.StringVar(value='Detenido')
        wrapped_label(control_row, textvariable=self.status, style='Card.TLabel',
                      font=('DejaVu Sans', 10, 'bold')).pack(side='left', padx=20, fill='x', expand=True)
        self.summary = tk.StringVar(value='La carpeta no se moverá ni se modificará.')
        wrapped_label(controls, textvariable=self.summary, style='CardHint.TLabel').pack(anchor='w', fill='x', pady=(10, 0))
        counters = ttk.Frame(frame)
        counters.pack(fill='x', pady=(0, 10))
        counters.columnconfigure(0, weight=1, uniform='metrics')
        counters.columnconfigure(1, weight=1, uniform='metrics')
        rx_card = card(counters)
        rx_card.grid(row=0, column=0, sticky='nsew', padx=(0, 6))
        tx_card = card(counters)
        tx_card.grid(row=0, column=1, sticky='nsew', padx=(6, 0))
        self.rx = tk.StringVar(value='↓ RECIBIDOS   0 paquetes · 0 B')
        self.tx = tk.StringVar(value='↑ ENVIADOS   0 paquetes · 0 B')
        wrapped_label(rx_card, textvariable=self.rx, style='Metric.TLabel', foreground='#87c6ff').pack(anchor='w', fill='x')
        wrapped_label(tx_card, textvariable=self.tx, style='Metric.TLabel', foreground='#83dacb').pack(anchor='w', fill='x')
        self.monitor_note = tk.StringVar(value='Contadores completos · últimos 600 paquetes · sin capturar el contenido de tus juegos')
        wrapped_label(frame, textvariable=self.monitor_note, style='Subtitle.TLabel').pack(side='bottom', fill='x', pady=(10, 0))
        monitor = card(frame)
        monitor.pack(fill='both', expand=True)
        notebook = ttk.Notebook(monitor)
        notebook.pack(fill='both', expand=True)
        traffic_frame = ttk.Frame(notebook, style='Inner.TFrame')
        log_frame = ttk.Frame(notebook, style='Inner.TFrame')
        notebook.add(traffic_frame, text='Tráfico en directo')
        notebook.add(log_frame, text='Registro del servidor')
        columns = ('time', 'direction', 'peer', 'command', 'id', 'seq', 'bytes')
        self.traffic = ttk.Treeview(traffic_frame, columns=columns, show='headings', height=6)
        for key, label, width in zip(columns, ('Hora', 'Sentido', 'Cliente', 'Paquete UDPBD', 'ID', 'Sec.', 'Bytes'),
                                     (110, 95, 210, 150, 45, 50, 70)):
            self.traffic.heading(key, text=label)
            self.traffic.column(key, width=width, minwidth=40, anchor='w')
        scroll = ttk.Scrollbar(traffic_frame, orient='vertical', command=self.traffic.yview)
        self.traffic.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y', padx=(6, 0))
        self.traffic.pack(fill='both', expand=True)
        self.traffic.tag_configure('RX', foreground='#87c6ff')
        self.traffic.tag_configure('TX', foreground='#83dacb')
        self.log = tk.Text(log_frame, height=6, state='disabled', wrap='word',
                           background='#09142a', foreground=TEXT, insertbackground=BLUE,
                           selectbackground='#214d87', selectforeground='#ffffff',
                           relief='flat', borderwidth=0, highlightthickness=0,
                           font=('DejaVu Sans Mono', 10), padx=14, pady=12)
        self.log.pack(fill='both', expand=True)
        self.refresh_ips()
        self.load_settings()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(75, self.poll)

    def append(self, line):
        self.log.configure(state='normal')
        self.log.insert('end', line)
        if int(self.log.index('end-1c').split('.')[0]) > 1000:
            self.log.delete('1.0', '200.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def refresh_ips(self):
        self.ips = local_ips()
        previous = self.bind_ip.get()
        values = [label for label, _ip in self.ips] or ['Sin IPv4 activa en la red']
        self.ip_box.configure(values=values)
        self.bind_ip.set(previous if previous in values else values[0])

    def selected_ip(self):
        return dict(self.ips).get(self.bind_ip.get())

    def choose_folder(self):
        folder = filedialog.askdirectory(title='Selecciona la carpeta con tus juegos de PS2', mustexist=True)
        if folder:
            self.source.set(folder)

    def load_settings(self):
        try:
            data = json.loads(SETTINGS.read_text())
            self.source.set(data.get('folder', ''))
            if data.get('interface') in [label for label, _ in self.ips]:
                self.bind_ip.set(data['interface'])
        except (OSError, ValueError):
            pass

    def save_settings(self):
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps({'folder': self.source.get(),
                                            'interface': self.bind_ip.get(), 'persistent': False}, ensure_ascii=False, indent=2) + '\n')
        except OSError as exc:
            self.append(f'No se guardaron las preferencias: {exc}\n')

    def set_state(self, state):
        self.state = state
        idle = state == 'stopped'
        for widget in (self.source_entry, self.choose_button, self.refresh_button):
            widget.configure(state='normal' if idle else 'disabled')
        self.ip_box.configure(state='readonly' if idle else 'disabled')
        self.start_button.configure(text='Iniciar servidor' if idle else 'Parar servidor',
                                    state='disabled' if state == 'stopping' else 'normal')

    def toggle(self):
        if self.state == 'stopped':
            self.start()
        else:
            self.stop()

    def start(self):
        if self.state != 'stopped':
            return
        folder, bind = self.source.get().strip(), self.selected_ip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror('Carpeta', 'Selecciona una carpeta existente con los juegos.')
            return
        if not bind:
            messagebox.showerror('Red', 'No hay IPv4 activa. Conecta la red y pulsa Actualizar.')
            return
        self.cancelled = threading.Event()
        self.set_state('preparing')
        self.status.set('Preparando unidad virtual…')
        self.traffic.delete(*self.traffic.get_children())
        self.rows.clear()
        self.last_stats = (time.monotonic(), 0, 0)
        self.rx.set('↓ RECIBIDOS   0 paquetes · 0 B')
        self.tx.set('↑ ENVIADOS   0 paquetes · 0 B')
        self.monitor_note.set('Contadores completos · historial de los últimos 600 paquetes mostrados')
        self.save_settings()

        def prepare():
            session = None
            try:
                session = tempfile.TemporaryDirectory(prefix='udpbd-session-')
                resolved = Path(folder).resolve()
                if Path(session.name).is_relative_to(resolved):
                    raise ValueError('Elige una carpeta de juegos más concreta, no la carpeta que contiene los temporales de la app.')
                descriptor = Path(session.name) / 'unidad.udpv'
                # If DVD/CD itself is selected, synthesize the parent directory in exFAT.
                progress = lambda text: self.events.put(('progress', text))
                from virtual_exfat import build_opl
                info = build_opl(folder, descriptor, self.cancelled, progress, writable=True)
                self.events.put(('prepared', (session, descriptor, info, bind)))
            except Exception as exc:
                if session:
                    session.cleanup()
                self.events.put(('failed', str(exc)))
        self.worker = threading.Thread(target=prepare, daemon=True)
        self.worker.start()

    def launch(self, session, descriptor, info, bind):
        self.session = session
        if self.cancelled.is_set():
            self.finish(0)
            return
        command = [str(BASE / 'udpbd-server'), '--read-write', '--telemetry', '--bind', '0.0.0.0']
        command.append('--virtual')
        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.trace_path = STATE / f'solicitudes-{time.time_ns()}.jsonl'
        command += ['--trace', str(self.trace_path), '--packet-delay-us', '500']
        interface = self.bind_ip.get().rsplit(' — ', 1)[-1]
        try:
            socket.if_nametoindex(interface)
        except OSError as exc:
            self.append(f'Interfaz no disponible: {interface}: {exc}\n')
            self.finish(1)
            return
        command += ['--interface', interface]
        command += ['--', str(descriptor)]
        try:
            self.proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding='utf-8', errors='replace', bufsize=1)
        except OSError as exc:
            self.append(f'Error al iniciar: {exc}\n')
            self.finish(1)
            return
        self.set_state('starting')
        self.status.set('Iniciando servidor…')
        self.ip_hint.set(f'IP para la PS2: {bind} · UDP 48573 · solo interfaz {interface}')
        self.summary.set(f"{info['files']} archivos · {size_text(info['bytes'])} · escritura temporal; se pierde al parar · sin copiar juegos")
        self.append(f"\nCarpeta: {info['folder']}\nUnidad virtual exFAT · lectura/escritura temporal · {info['files']} archivos\n")
        self.append(f'Registro completo de solicitudes: {self.trace_path}\n')
        if not info['files']:
            self.append('La carpeta está vacía; no aparecerán juegos en OPL.\n')
        proc = self.proc
        def reader():
            try:
                for line in proc.stdout:
                    if line.startswith('@traffic '):
                        try:
                            self.events.put(('traffic', json.loads(line[9:])))
                        except ValueError:
                            self.events.put(('log', 'Telemetría inválida recibida.\n'))
                    else:
                        self.events.put(('log', line))
            finally:
                self.events.put(('exit', proc.wait()))
                proc.stdout.close()
        threading.Thread(target=reader, daemon=True).start()

    def stop(self):
        if self.state == 'stopped':
            return
        self.cancelled.set()
        self.set_state('stopping')
        self.status.set('Deteniendo…')
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            proc = self.proc
            self.root.after(2500, lambda: proc.kill() if proc.poll() is None else None)

    def update_traffic(self, data):
        now = time.monotonic()
        previous_time, previous_rx, previous_tx = self.last_stats
        delta = max(.01, now - previous_time)
        self.rx.set(f"↓ RECIBIDOS   {data['rx']:,} paquetes · {size_text(data['rx_bytes'])} · {size_text(max(0, data['rx_bytes'] - previous_rx) / delta)}/s")
        self.tx.set(f"↑ ENVIADOS   {data['tx']:,} paquetes · {size_text(data['tx_bytes'])} · {size_text(max(0, data['tx_bytes'] - previous_tx) / delta)}/s")
        self.last_stats = (now, data['rx_bytes'], data['tx_bytes'])
        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        for direction, peer, length, command, ident, sequence in data['samples']:
            self.rows.append(self.traffic.insert('', 'end', values=(timestamp, direction, peer,
                COMMANDS.get(command, f'CMD {command}'), ident, sequence, length), tags=(direction,)))
        if len(self.rows) > 600:
            self.traffic.delete(*self.rows[:-600])
            self.rows = self.rows[-600:]
        if self.rows:
            self.traffic.see(self.rows[-1])
        self.monitor_note.set(f"Contadores completos · lista muestreada con tráfico intenso · {data['omitted']:,} paquetes no listados · últimos 600 mostrados")

    def finish(self, code):
        self.proc = None
        if self.session:
            self.session.cleanup()
            self.session = None
        self.set_state('stopped')
        self.status.set('Detenido' if code == 0 else 'Error · consulta el registro')
        self.append(f'Proceso finalizado (código {code}). Servidor detenido; escrituras temporales descartadas.\n')
        if self.closing:
            self.root.destroy()

    def poll(self):
        for _ in range(100):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'progress':
                if self.state != 'stopping':
                    self.status.set(value)
            elif kind == 'prepared':
                self.launch(*value)
            elif kind == 'failed':
                self.append(value + '\n')
                self.finish(0 if self.cancelled.is_set() else 1)
            elif kind == 'log':
                self.append(value)
                if 'Servidor listo' in value and self.state != 'stopping':
                    self.set_state('running')
                    self.status.set('Activo · esperando/conectado a PS2')
            elif kind == 'traffic':
                self.update_traffic(value)
            elif kind == 'exit':
                self.finish(value)
            if self.closing and self.state == 'stopped':
                return
        self.root.after(75, self.poll)

    def close(self):
        if self.state != 'stopped':
            self.closing = True
            self.stop()
        else:
            self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    Launcher(root)
    root.mainloop()
