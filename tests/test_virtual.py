"""Pruebas de carpeta -> exFAT -> UDP; solo datos temporales, sin root."""
import errno
import json
from pathlib import Path
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from virtual_exfat import build, build_opl, checksum, PARTITION, SECTOR, CLUSTER
from test_udpbd import Server, hdr


class VirtualTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.folder = self.path / 'juegos'
        (self.folder / 'DVD').mkdir(parents=True)
        self.game = self.folder / 'DVD' / 'SLUS_123.45.Juego á日本.iso'
        self.content = bytes(range(256)) * 1200 + b'FINAL'
        self.game.write_bytes(self.content)
        (self.folder / 'vacío.txt').touch()
        self.descriptor = self.path / 'disco.udpv'
        self.info = build(self.folder, self.descriptor)
        self.server = Server(self.descriptor, '--virtual')
        with self.descriptor.open('rb') as f:
            _magic, self.length, n, _count = struct.unpack('<8sQQI', f.read(28))
            self.meta = f.read(n)

    def tearDown(self):
        self.server.close()
        self.temp.cleanup()

    def test_files_and_cluster_padding_over_udp(self):
        for _name, offset, size in self.info['extents']:
            result = bytearray()
            for pos in range(0, (size + 511) // 512, 128):
                sectors = min(128, (size + 511) // 512 - pos)
                result += self.server.read(offset // 512 + pos, sectors)
            self.assertEqual(result[:size], self.content)
            self.assertEqual(result[size:], bytes(len(result) - size))
            self.assertEqual(self.server.read((offset + ((size + CLUSTER - 1) // CLUSTER) * CLUSTER) // 512 - 1, 1), bytes(512))

    def test_exfat_navigation_and_boot_checksums_over_udp(self):
        mbr = self.server.read(0, 1)
        self.assertEqual(mbr[510:], b'\x55\xaa')
        self.assertEqual(struct.unpack_from('<I', mbr, 454)[0], PARTITION)
        boot = self.server.read(PARTITION, 24)
        self.assertEqual(boot[:11], b'\xeb\x76\x90EXFAT   ')
        self.assertEqual(boot[:12 * 512], boot[12 * 512:])
        self.assertEqual(struct.unpack_from('<I', boot, 11 * 512)[0], checksum(boot[:11 * 512], skip=(106, 107, 112)))
        # Read actual directory entries using only on-wire sectors, not descriptor extents.
        heap = self.info['heap_sector']
        def entries(cluster):
            raw = self.server.read(heap + (cluster - 2) * 256, 256)
            result, pos = {}, 0
            while raw[pos]:
                if raw[pos] == 0x85:
                    n = raw[pos + 1]
                    data = raw[pos:pos + (n + 1) * 32]
                    self.assertEqual(struct.unpack_from('<H', data, 2)[0], checksum(data, 16, (2, 3)))
                    units = data[35]
                    name_bytes = b''.join(data[j + 2:j + 32] for j in range(64, len(data), 32))
                    name = name_bytes[:units * 2].decode('utf-16le')
                    result[name] = (struct.unpack_from('<I', data, 52)[0], struct.unpack_from('<Q', data, 56)[0])
                    pos += len(data)
                else:
                    pos += 32
            return result
        root = entries(self.info['root_cluster'])
        self.assertIn('DVD', root)
        dvd = entries(root['DVD'][0])
        cluster, size = dvd[self.game.name]
        self.assertEqual(size, len(self.content))
        self.assertEqual(self.server.read(heap + (cluster - 2) * 256, 1), self.content[:512])
        self.assertEqual(root['vacío.txt'], (0, 0))

    def test_reads_cross_metadata_file_and_padding_boundaries(self):
        rng = random.Random(42)
        offset = self.info['extents'][0][1]
        for sector in [0, PARTITION - 1, PARTITION, len(self.meta) // 512 - 2,
                       offset // 512 - 1, (offset + len(self.content)) // 512 - 1,
                       self.info['sectors'] - 2] + [rng.randrange(self.info['sectors'] - 10) for _ in range(12)]:
            count = min(8, self.info['sectors'] - sector)
            start, end = sector * 512, (sector + count) * 512
            expected = bytearray(count * 512)
            a, b = start, min(end, len(self.meta))
            if b > a:
                expected[:b-a] = self.meta[a:b]
            a, b = max(start, offset), min(end, offset + len(self.content))
            if b > a:
                expected[a-start:b-start] = self.content[a-offset:b-offset]
            self.assertEqual(self.server.read(sector, count), expected)

    def test_virtual_writes_rejected(self):
        self.server.send(hdr(4, 2) + struct.pack('<IH', self.info['extents'][0][1] // 512, 1))
        self.assertEqual(struct.unpack('<i', self.server.recv()[2:])[0], -errno.EROFS)
        self.assertEqual(self.game.read_bytes(), self.content)

    def writable(self):
        self.server.close()
        self.server = Server(self.descriptor, '--virtual', '--read-write')

    def test_cow_metadata_and_file_roundtrip_without_changing_originals(self):
        self.writable()
        descriptor = self.descriptor.read_bytes()
        sector = self.info['extents'][0][1] // 512
        before = self.server.read(sector - 1, 5)
        self.assertEqual(self.server.write(sector, b'X' * 1024), 0)
        self.assertEqual(self.server.write(sector + 1, b'Y' * 512), 0)
        self.assertEqual(self.server.read(sector - 1, 5), before[:512] + b'X' * 512 + b'Y' * 512 + before[1536:])
        self.assertEqual(self.server.write(PARTITION, b'Z' * 512), 0)
        self.assertEqual(self.server.read(PARTITION, 1), b'Z' * 512)
        self.assertEqual(self.game.read_bytes(), self.content)
        self.assertEqual(self.descriptor.read_bytes(), descriptor)

    def test_cow_large_write_and_unallocated_last_sector(self):
        self.writable()
        data = bytes(range(256)) * 1024
        self.assertEqual(self.server.write(0, data), 0)
        self.assertEqual(self.server.read(0, 512), data)
        last = self.info['sectors'] - 1
        self.assertEqual(self.server.write(last, b'L' * 512), 0)
        self.assertEqual(self.server.read(last - 1, 2), bytes(512) + b'L' * 512)

    def test_cow_incomplete_write_not_committed_and_bounds_rejected(self):
        from test_udpbd import rdma
        self.writable()
        before = self.server.read(0, 2)
        self.server.send(hdr(4, 2) + struct.pack('<IH', 0, 2))
        self.server.send(rdma(b'X' * 512, 2))
        self.assertEqual(self.server.read(0, 2), before)
        self.server.send(hdr(4, 2) + struct.pack('<IH', self.info['sectors'], 1))
        self.assertEqual(struct.unpack('<i', self.server.recv()[2:])[0], -errno.EINVAL)

    def test_cow_reset_after_stop(self):
        self.writable()
        before = self.server.read(0, 1)
        self.assertEqual(self.server.write(0, b'X' * 512), 0)
        self.server.close()
        self.server = Server(self.descriptor, '--virtual', '--read-write')
        self.assertEqual(self.server.read(0, 1), before)

    def test_modification_stops_even_while_idle(self):
        self.game.write_bytes(b'changed')
        self.assertEqual(self.server.process.wait(timeout=3), 1)
        self.server.log.seek(0)
        self.assertIn(b'cambiado', self.server.log.read())

    def test_added_file_stops_session(self):
        (self.folder / 'DVD' / 'nuevo.iso').touch()
        self.assertEqual(self.server.process.wait(timeout=3), 1)

    def test_replaced_file_is_not_served(self):
        replacement = self.path / 'replacement'
        replacement.write_bytes(self.content)
        replacement.replace(self.game)
        self.assertEqual(self.server.process.wait(timeout=3), 1)

    def test_fsck_exfat_independent_validator(self):
        validator = shutil.which('fsck.exfat') or '/usr/sbin/fsck.exfat'
        if not Path(validator).is_file():
            self.skipTest('exfatprogs no disponible')
        # Only the small test fixture is materialized for the independent fsck.
        image = self.path / 'fixture.img'
        with image.open('wb') as f:
            f.truncate(self.length - PARTITION * SECTOR)
            f.write(self.meta[PARTITION * SECTOR:])
            for path, offset, _size in self.info['extents']:
                f.seek(offset - PARTITION * SECTOR)
                f.write(Path(path).read_bytes())
        result = subprocess.run([validator, '-n', str(image)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('clean', result.stdout)

    def test_telemetry_counters_equal_datagrams(self):
        self.server.close()
        self.server = Server(self.descriptor, '--virtual', '--telemetry')
        self.server.read(self.info['extents'][0][1] // 512, 4)
        self.server.process.terminate()
        self.server.process.wait(timeout=3)
        self.server.log.seek(0)
        records = [json.loads(line[9:]) for line in self.server.log.read().decode().splitlines() if line.startswith('@traffic ')]
        last = records[-1]
        self.assertGreaterEqual(last['rx'], 2)
        self.assertEqual(last['tx'], 3)  # INFO + 2 x 1024-byte RDMA
        self.assertEqual(last['tx_bytes'], 10 + 2 * 1030)
        self.assertEqual(last['omitted'], 0)
        self.assertEqual(len(last['samples']), last['rx'] + last['tx'])


class BuilderTests(unittest.TestCase):
    def test_large_file_over_4gib_sparse_without_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); folder = p / 'DVD'; folder.mkdir(); game = folder / 'DVD9.iso'
            size = 5 * 1024**3 + 7
            with game.open('wb') as f:
                f.truncate(size); f.seek(5 * 1024**3); f.write(b'ENDING!')
            descriptor = p / 'disc.udpv'
            info = build_opl(folder, descriptor)
            self.assertLess(descriptor.stat().st_size, 4 * 1024**2)
            self.assertEqual(info['bytes'], size)
            server = Server(descriptor, '--virtual')
            try:
                offset = info['extents'][0][1]
                data = server.read((offset + 5 * 1024**3) // 512, 1)
                self.assertEqual(data, b'ENDING!' + bytes(505))
                # Root has exactly DVD and not the ISO, even when selecting DVD directly.
                root = server.read(info['heap_sector'] + (info['root_cluster'] - 2) * 256, 1)
                self.assertIn('DVD'.encode('utf-16le'), root)
            finally:
                server.close()

    def test_refuse_symlinks_and_case_collisions(self):
        for kind in ('symlink', 'case'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp); folder = p / 'games'; folder.mkdir()
                (folder / 'game.iso').touch()
                if kind == 'symlink':
                    (folder / 'link.iso').symlink_to(folder / 'game.iso')
                else:
                    (folder / 'GAME.iso').touch()
                with self.assertRaises(ValueError):
                    build(folder, p / 'disc.udpv')

    def test_loose_iso_mapped_to_virtual_dvd_without_moving(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); folder = p / 'games'; folder.mkdir()
            (folder / 'Juego.iso').write_bytes(b'ISO')
            info = build_opl(folder, p / 'disc.udpv')
            self.assertTrue((folder / 'Juego.iso').exists())
            self.assertFalse((folder / 'DVD').exists())
            server = Server(p / 'disc.udpv', '--virtual')
            try:
                root = server.read(info['heap_sector'] + (info['root_cluster'] - 2) * 256, 1)
                self.assertIn('DVD'.encode('utf-16le'), root)
                self.assertNotIn('Juego.iso'.encode('utf-16le'), root)
            finally:
                server.close()

    def test_cancel_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop = threading.Event(); stop.set()
            with self.assertRaises(InterruptedError):
                build(tmp, Path(tmp) / 'never.udpv', stop)

    def test_many_files_lru_and_multicluster_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); folder = p / 'games'; folder.mkdir()
            for i in range(75):
                (folder / f'{i:03}.iso').write_bytes(bytes([i]) * 512)
            # >128 KiB directory; long filenames use 19 directory entries each.
            for i in range(230):
                (folder / (f'{i:03}' + 'l' * 235)).touch()
            info = build(folder, p / 'disc.udpv')
            server = Server(p / 'disc.udpv', '--virtual')
            try:
                for path, offset, _size in info['extents']:
                    self.assertEqual(server.read(offset // 512, 1), Path(path).read_bytes())
                self.assertEqual(server.read(info['extents'][0][1] // 512, 1), bytes(512))
            finally:
                server.close()

    def test_manifest_truncation_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); folder = p / 'games'; folder.mkdir()
            descriptor = p / 'disc.udpv'; build(folder, descriptor)
            descriptor.write_bytes(descriptor.read_bytes()[:64])
            result = subprocess.run([str(BASE / 'udpbd-server'), '--virtual', str(descriptor)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
