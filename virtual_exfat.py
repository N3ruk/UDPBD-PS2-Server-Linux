"""Disco exFAT con origen de solo lectura: metadatos pequeños + extensiones de archivos reales.
No monta nada en el kernel, no escribe en el origen ni copia los juegos.
"""
from dataclasses import dataclass, field
from pathlib import Path
import math
import os
import stat
import struct
import threading
import time

SECTOR = 512
CLUSTER = 128 * 1024
PARTITION = 2048
MAGIC = b'UDPVF001'
MAX_NODES = 20000


def checksum(data, bits=32, skip=()):
    value, mask = 0, (1 << bits) - 1
    for i, byte in enumerate(data):
        if i not in skip:
            value = (((value >> 1) | (value << (bits - 1))) + byte) & mask
    return value


def upper_unit(n):
    s = chr(n).upper()
    return ord(s) if len(s) == 1 and ord(s) <= 0xffff else n


def upcase_table():
    # Identity runs are encoded as FFFF,count (exFAT compressed upcase table).
    result, i = bytearray(), 0
    while i < 65536:
        if upper_unit(i) == i:
            end = i + 1
            while end < 65536 and upper_unit(end) == end and end - i < 65535:
                end += 1
            if end - i > 1 or i == 65535:
                result += struct.pack('<HH', 65535, end - i)
            else:
                result += struct.pack('<H', i)
            i = end
        else:
            result += struct.pack('<H', upper_unit(i))
            i += 1
    return bytes(result)


@dataclass
class Node:
    path: Path
    name: str
    directory: bool
    info: os.stat_result
    children: list = field(default_factory=list)
    cluster: int = 0
    clusters: int = 0

    @property
    def units(self):
        raw = self.name.encode('utf-16le')
        return struct.unpack('<' + 'H' * (len(raw) // 2), raw)


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def scan(folder, cancelled):
    root = Path(folder).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Selecciona una carpeta existente.')
    nodes = []

    def visit(path, depth=0):
        if cancelled.is_set():
            raise InterruptedError('Preparación cancelada')
        if depth > 64 or len(nodes) >= MAX_NODES:
            raise ValueError('Carpeta demasiado grande: máximo 20 000 entradas y 64 niveles.')
        info = path.lstat()
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError(f'No se admiten enlaces simbólicos ni archivos especiales: {path}')
        node = Node(path, path.name, stat.S_ISDIR(info.st_mode), info)
        nodes.append(node)
        if depth:
            units = node.units
            if not units or len(units) > 255 or any(u < 32 or chr(u) in '"*/:<>?\\|' for u in units) or node.name.endswith((' ', '.')):
                raise ValueError(f'Nombre incompatible con exFAT: {path}')
        if node.directory:
            with os.scandir(path) as it:
                entries = sorted(it, key=lambda e: e.name)
            names = set()
            for entry in entries:
                child = visit(Path(entry.path), depth + 1)
                key = tuple(upper_unit(u) for u in child.units)
                if key in names:
                    raise ValueError(f'Nombres duplicados sin distinguir mayúsculas en {path}')
                names.add(key)
                node.children.append(child)
        return node

    return visit(root), nodes


def timestamp(info):
    t = time.localtime(max(315532800, min(info.st_mtime, 4354819198)))
    return ((t.tm_year - 1980) << 25 | t.tm_mon << 21 | t.tm_mday << 16 |
            t.tm_hour << 11 | t.tm_min << 5 | t.tm_sec // 2)


def entry(node, writable=False):
    units = node.units
    count = math.ceil(len(units) / 15)
    data = bytearray(32 * (2 + count))
    data[0], data[1] = 0x85, 1 + count
    struct.pack_into('<H', data, 4, 0x10 if node.directory else
                     (0x20 if writable and not node.name.lower().endswith('.iso') else 0x21))
    for offset in (8, 12, 16):
        struct.pack_into('<I', data, offset, timestamp(node.info))
    # Timestamps are local; timezone validity flag is intentionally unset.
    data[32], data[33], data[35] = 0xc0, (3 if node.clusters else 1), len(units)
    encoded_upper = struct.pack('<' + 'H' * len(units), *(upper_unit(u) for u in units))
    struct.pack_into('<H', data, 36, checksum(encoded_upper, 16))
    size = node.clusters * CLUSTER if node.directory else node.info.st_size
    struct.pack_into('<Q', data, 40, size)
    struct.pack_into('<I', data, 52, node.cluster)
    struct.pack_into('<Q', data, 56, size)
    for i in range(count):
        start = 64 + 32 * i
        data[start] = 0xc1
        part = units[i * 15:(i + 1) * 15]
        struct.pack_into('<' + 'H' * len(part), data, start + 2, *part)
    struct.pack_into('<H', data, 2, checksum(data, 16, (2, 3)))
    return data


def build(folder, destination, cancelled=None, progress=None, *, wrap=False, opl=False, writable=False, partition=PARTITION, free_bytes=None):
    cancelled = cancelled or threading.Event()
    progress = progress or (lambda _: None)
    progress('Leyendo la estructura de la carpeta…')
    root, nodes = scan(folder, cancelled)
    if wrap:
        root.name = root.name.upper()
        root = Node(root.path, '', True, root.info, [root])
        nodes.insert(0, root)
    elif opl:
        loose = [n for n in root.children if not n.directory and n.name.lower().endswith('.iso')]
        if loose:
            dvd = next((n for n in root.children if n.name.upper() == 'DVD'), None)
            if dvd is not None and not dvd.directory:
                raise ValueError('Existe un archivo llamado DVD; no se puede crear el directorio virtual de juegos.')
            if dvd is None:
                dvd = Node(root.path, 'DVD', True, root.info)
                nodes.append(dvd)
                root.children.append(dvd)
            names = {tuple(upper_unit(u) for u in n.units) for n in dvd.children}
            for node in loose:
                key = tuple(upper_unit(u) for u in node.units)
                if key in names:
                    raise ValueError(f'ISO duplicada entre la raíz y DVD: {node.name}')
                names.add(key)
                root.children.remove(node)
                dvd.children.append(node)
    if len(nodes) > MAX_NODES:
        raise ValueError('Demasiadas entradas para la unidad virtual.')
    files = [n for n in nodes if not n.directory]
    dirs = [n for n in nodes if n.directory]
    upcase = upcase_table()
    for node in dirs:
        entries = sum(2 + math.ceil(len(c.units) / 15) for c in node.children)
        node.clusters = max(1, math.ceil((entries * 32 + 32 + (96 if node is root else 0)) / CLUSTER))
    for node in files:
        node.clusters = math.ceil(node.info.st_size / CLUSTER)
    used_base = sum(n.clusters for n in nodes) + math.ceil(len(upcase) / CLUSTER)
    if partition not in (0, PARTITION):
        raise ValueError('Inicio de volumen incompatible.')
    reserve = math.ceil(free_bytes / CLUSTER) if free_bytes is not None else (2048 if writable else 8)
    cluster_count = max(256, used_base + reserve)
    while True:
        bitmap_size = math.ceil(cluster_count / 8)
        bitmap_clusters = math.ceil(bitmap_size / CLUSTER)
        used = used_base + bitmap_clusters
        if used + reserve <= cluster_count:
            break
        cluster_count = used + reserve
    fat_sector = 24
    fat_sectors = math.ceil((cluster_count + 2) * 4 / SECTOR)
    heap_sector = math.ceil((fat_sector + fat_sectors) / 256) * 256
    volume_sectors = heap_sector + cluster_count * (CLUSTER // SECTOR)
    total_sectors = partition + volume_sectors
    if total_sectors > 0xffffffff:
        raise ValueError('La carpeta excede el máximo UDPBD de 2 TiB.')
    next_cluster = 2
    bitmap_cluster = next_cluster
    next_cluster += bitmap_clusters
    upcase_cluster = next_cluster
    upcase_clusters = math.ceil(len(upcase) / CLUSTER)
    next_cluster += upcase_clusters
    for node in dirs:
        node.cluster = next_cluster
        next_cluster += node.clusters
    first_file_cluster = next_cluster
    for node in files:
        if node.clusters:
            node.cluster = next_cluster
            next_cluster += node.clusters
    prefix_size = (partition + heap_sector) * SECTOR + (first_file_cluster - 2) * CLUSTER
    if prefix_size > 128 * 1024 * 1024:
        raise ValueError('Los metadatos superan el límite de 128 MiB.')
    meta = bytearray(prefix_size)
    def cluster_offset(cluster):
        return (partition + heap_sector) * SECTOR + (cluster - 2) * CLUSTER
    # MBR with one exFAT partition (type 07), no physical device involved.
    if partition:
        struct.pack_into('<B3sB3sII', meta, 446, 0, b'\xfe\xff\xff', 7, b'\xfe\xff\xff', partition, volume_sectors)
        meta[510:512] = b'\x55\xaa'
    boot = bytearray(12 * SECTOR)
    boot[:11] = b'\xeb\x76\x90EXFAT   '
    struct.pack_into('<QQIIIIIIHHBBBBB', boot, 64, partition, volume_sectors,
                     fat_sector, fat_sectors, heap_sector, cluster_count, root.cluster,
                     0x55445042, 0x100, 0, 9, 8, 1, 0x80, min(100, used * 100 // cluster_count))
    boot[510:512] = b'\x55\xaa'
    for sector in range(1, 9):
        boot[(sector + 1) * SECTOR - 4:(sector + 1) * SECTOR] = b'\0\0\x55\xaa'
    check = checksum(boot[:11 * SECTOR], skip=(106, 107, 112))
    boot[11 * SECTOR:] = struct.pack('<I', check) * 128
    start = partition * SECTOR
    meta[start:start + len(boot)] = boot
    meta[start + len(boot):start + len(boot) * 2] = boot
    fat = (partition + fat_sector) * SECTOR
    struct.pack_into('<II', meta, fat, 0xfffffff8, 0xffffffff)
    # Populate FAT even for contiguous streams: useful for fragment queries in OPL.
    def chain(first, count):
        for i in range(count):
            struct.pack_into('<I', meta, fat + (first + i) * 4,
                             first + i + 1 if i + 1 < count else 0xffffffff)
    chain(bitmap_cluster, bitmap_clusters)
    chain(upcase_cluster, upcase_clusters)
    for node in nodes:
        if node.clusters:
            chain(node.cluster, node.clusters)
    bitmap = bytearray(bitmap_size)
    for i in range(next_cluster - 2):
        bitmap[i // 8] |= 1 << (i % 8)
    pos = cluster_offset(bitmap_cluster)
    meta[pos:pos + len(bitmap)] = bitmap
    pos = cluster_offset(upcase_cluster)
    meta[pos:pos + len(upcase)] = upcase
    for node in dirs:
        data = bytearray()
        if node is root:
            label = bytearray(32)
            label[0], label[1] = 0x83, 9
            label[2:20] = 'UDPBD PS2'.encode('utf-16le')
            data += label
            alloc = bytearray(32)
            alloc[0] = 0x81
            struct.pack_into('<IQ', alloc, 20, bitmap_cluster, bitmap_size)
            data += alloc
            upper = bytearray(32)
            upper[0] = 0x82
            struct.pack_into('<I', upper, 4, checksum(upcase))
            struct.pack_into('<IQ', upper, 20, upcase_cluster, len(upcase))
            data += upper
        for child in node.children:
            data += entry(child, writable)
        pos = cluster_offset(node.cluster)
        meta[pos:pos + len(data)] = data
    progress(f'Preparando {len(files)} archivos · sin copiar juegos…')
    if cancelled.is_set():
        raise InterruptedError('Preparación cancelada')
    # Detect changes while walking/building, including directory additions/removals.
    for node in nodes:
        if signature(node.path.lstat()) != signature(node.info):
            raise ValueError(f'El origen ha cambiado durante la preparación: {node.path}')
    destination = Path(destination)
    with destination.open('xb') as out:
        out.write(struct.pack('<8sQQI', MAGIC, total_sectors * SECTOR, len(meta), len(nodes)))
        out.write(meta)
        for node in nodes:
            path = os.fsencode(node.path)
            out.write(struct.pack('<BQQQQQQQI', node.directory,
                0 if node.directory or not node.clusters else cluster_offset(node.cluster),
                node.info.st_size, node.info.st_dev, node.info.st_ino,
                node.info.st_mtime_ns, node.info.st_ctime_ns, node.clusters * CLUSTER, len(path)))
            out.write(path)
    return {'files': len(files), 'bytes': sum(n.info.st_size for n in files),
            'sectors': total_sectors, 'metadata_bytes': len(meta),
            'extents': [(str(n.path), cluster_offset(n.cluster), n.info.st_size) for n in files if n.clusters],
            'folder': str(root.path), 'root_cluster': root.cluster, 'heap_sector': partition + heap_sector}


def build_opl(folder, destination, cancelled=None, progress=None, *, writable=False, partition=PARTITION, free_bytes=None):
    """OPL expects ISO images under DVD/ or CD/, not directly at volume root."""
    return build(folder, destination, cancelled, progress,
                 wrap=Path(folder).name.upper() in ('DVD', 'CD'), opl=True, writable=writable,
                 partition=partition, free_bytes=free_bytes)
