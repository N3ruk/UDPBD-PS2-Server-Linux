#!/usr/bin/env python3
"""Construye DEB y AppImage con herramientas verificadas, sin sudo."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'VERSION').read_text().strip()
CACHE = ROOT / '.cache'
BUILD = ROOT / 'build'
DIST = ROOT / 'dist'
APP = 'udpbd-ps2-server'

def run(argv, **kwargs):
    subprocess.run([str(a) for a in argv], check=True, **kwargs)

def fetch(spec):
    path = CACHE / spec['file']
    if not path.exists():
        request = urllib.request.Request(spec['url'], headers={'User-Agent': 'UDPBD-PS2-Server-builder'})
        with urllib.request.urlopen(request) as response, path.open('wb') as target:
            shutil.copyfileobj(response, target)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != spec['sha256']:
        raise SystemExit(f'SHA256 incorrecto: {path}. No se ejecutará el archivo.')
    return path

def extract(archive, target):
    with tarfile.open(archive) as source:
        # Los archivos están fijados por hash. Validar rutas también en Python 3.10.
        for member in source.getmembers():
            resolved = (target / member.name).resolve()
            if not resolved.is_relative_to(target.resolve()):
                raise ValueError('Ruta fuera del destino en el archivo descargado')
        source.extractall(target)

def payload(target):
    target.mkdir(parents=True)
    for name in ('main.py', 'lanzador.py', 'lanzador_unidades.py', 'network.py',
                 'virtual_exfat.py', 'ps2_theme.py', 'self_test.py', 'udpbd-server'):
        shutil.copy2(ROOT / name, target / name)
    shutil.copytree(ROOT / 'assets', target / 'assets')

def common(stage):
    payload(stage / 'usr/share' / APP)
    desktop = stage / 'usr/share/applications'
    desktop.mkdir(parents=True)
    shutil.copy2(ROOT / 'packaging' / f'{APP}.desktop', desktop)
    icon_dir = stage / 'usr/share/icons/hicolor/512x512/apps'
    icon_dir.mkdir(parents=True)
    # El PNG original se conserva sin recomprimir ni sustituir su diseño.
    shutil.copy2(ROOT / 'assets' / f'{APP}.png', icon_dir / f'{APP}.png')
    doc = stage / 'usr/share/doc' / APP
    doc.mkdir(parents=True)
    for name in ('README.md', 'NOTICE.md', 'LICENSE'):
        shutil.copy2(ROOT / name, doc)
    shutil.copytree(ROOT / 'licenses', doc / 'licenses')

if __name__ == '__main__':
    if platform.machine() not in ('x86_64', 'AMD64'):
        raise SystemExit('Esta construcción genera paquetes x86_64/amd64.')
    for folder in (CACHE, BUILD, DIST):
        folder.mkdir(exist_ok=True)
    specs = json.loads((ROOT / 'packaging/toolchain-lock.json').read_text())
    tools = {name: fetch(spec) for name, spec in specs.items()}
    zig_dir = CACHE / 'zig-linux-x86_64-0.13.0'
    if not zig_dir.exists():
        extract(tools['zig'], CACHE)
    if not (CACHE / 'python').exists():
        extract(tools['python'], CACHE)
    zig = zig_dir / 'zig'
    run([zig, 'c++', '-target', 'x86_64-linux-musl', '-O2', '-std=c++17',
         '-Wall', '-Wextra', '-Wpedantic', '-Wno-sign-compare',
         '-D_FILE_OFFSET_BITS=64', '-fstack-protector-strong', '-static',
         ROOT / 'src/udpbd-server.cpp', '-o', ROOT / 'udpbd-server'])
    run(['strip', ROOT / 'udpbd-server'])
    deb = BUILD / 'deb'
    appdir = BUILD / 'UDPBD-PS2-Server.AppDir'
    for stage in (deb, appdir):
        if stage.exists():
            shutil.rmtree(stage)
        common(stage)
    bin_dir = deb / 'usr/bin'
    bin_dir.mkdir(parents=True)
    shutil.copy2(ROOT / 'packaging/launcher', bin_dir / APP)
    (bin_dir / APP).chmod(0o755)
    control = deb / 'DEBIAN'
    control.mkdir()
    installed_size = sum(p.stat().st_size for p in deb.rglob('*') if p.is_file()) // 1024
    (control / 'control').write_text(
        f'Package: {APP}\nVersion: {VERSION}\nSection: games\nPriority: optional\n'
        'Architecture: amd64\nMaintainer: N3ruk <noreply@github.com>\n'
        'Depends: python3 (>= 3.10), python3-tk, fonts-dejavu-core\n'
        f'Installed-Size: {installed_size}\n'
        'Homepage: https://github.com/N3ruk/UDPBD-PS2-Server-Linux\n'
        'Description: Servidor UDPBD v2 para PS2 con interfaz grafica Linux\n'
        ' Comparte carpetas de juegos sin copiar ni modificar las ISO originales.\n'
        ' Incluye unidad exFAT virtual, escritura temporal y monitor de trafico.\n')
    deb_file = DIST / f'{APP}_{VERSION}_amd64.deb'
    run(['dpkg-deb', '--root-owner-group', '--build', deb, deb_file])
    shutil.copytree(CACHE / 'python', appdir / 'usr/python',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    # No pip ni SDK: la aplicación no necesita instalar paquetes de Python.
    shutil.rmtree(appdir / 'usr/python/include')
    shutil.rmtree(appdir / 'usr/python/lib/python3.12/site-packages')
    shutil.rmtree(appdir / 'usr/python/share/man')
    for path in (appdir / 'usr/python/bin').iterdir():
        if path.name.startswith(('pip', 'idle', 'pydoc', '2to3')):
            path.unlink()
    shutil.copy2(ROOT / 'packaging/AppRun', appdir / 'AppRun')
    (appdir / 'AppRun').chmod(0o755)
    shutil.copy2(ROOT / 'packaging' / f'{APP}.desktop', appdir)
    shutil.copy2(ROOT / 'assets' / f'{APP}.png', appdir / f'{APP}.png')
    (appdir / '.DirIcon').symlink_to(f'{APP}.png')
    # En la AppImage el comando del desktop resuelve a AppRun.
    bindir = appdir / 'usr/bin'
    bindir.mkdir(parents=True)
    (bindir / APP).symlink_to('../../AppRun')
    for spec in ('appimagetool', 'runtime'):
        tools[spec].chmod(0o755)
    image_file = DIST / f'UDPBD-PS2-Server-{VERSION}-x86_64.AppImage'
    env = dict(os.environ, ARCH='x86_64', SOURCE_DATE_EPOCH='1793577600')
    run([tools['appimagetool'], '--appimage-extract-and-run', '--no-appstream',
         '--runtime-file', tools['runtime'], '--comp', 'zstd',
         '--mksquashfs-opt', '-processors', '--mksquashfs-opt', '2', appdir, image_file], env=env)
    image_file.chmod(0o755)
    checksums = ''.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n'
                        for path in (image_file, deb_file))
    (DIST / 'SHA256SUMS').write_text(checksums)
    print('Paquetes listos:', image_file.name, deb_file.name, flush=True)
