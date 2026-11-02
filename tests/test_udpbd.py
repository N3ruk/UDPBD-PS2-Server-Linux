"""Pruebas por UDP real, sin root, discos reales ni conexión a la PS2."""
import errno
import importlib.util
import json
from pathlib import Path
import select
import socket
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
BINARY = BASE / 'udpbd-server'

def hdr(cmd, ident=0, packet=0):
    return struct.pack('<H', cmd | ident << 5 | (packet & 255) << 8)

def rdma(data, ident=0, packet=1, shift=5):
    block = 1 << (shift + 2)
    assert len(data) % block == 0
    return hdr(5, ident, packet) + struct.pack('<I', shift | ((len(data) // block) << 4)) + data

class Server:
    def __init__(self, image, *options):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(('127.0.0.1', 0))
            self.address = probe.getsockname()
        self.log = tempfile.TemporaryFile(mode='w+b')
        self.process = subprocess.Popen([str(BINARY), '--bind', '127.0.0.1', '--port', str(self.address[1]),
                                         *options, str(image)], stdout=self.log, stderr=subprocess.STDOUT)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        self.sock.settimeout(.15)
        self.ready = False
        for _ in range(30):
            if self.process.poll() is not None:
                self.log.seek(0)
                raise AssertionError(self.log.read().decode())
            self.sock.sendto(hdr(0), self.address)
            try:
                self.sock.recv(2048)
                self.ready = True
                break
            except socket.timeout:
                pass
        if not self.ready:
            self.close()
            raise AssertionError('Servidor no responde')

    def send(self, data):
        self.sock.sendto(data, self.address)

    def recv(self):
        return self.sock.recv(65536)

    def read(self, sector, count, ident=0):
        self.send(hdr(2, ident) + struct.pack('<IH', sector, count))
        result = b''
        packet = 1
        while len(result) < count * 512:
            data = self.recv()
            self_header, bt = struct.unpack('<HI', data[:6])
            if self_header != struct.unpack('<H', hdr(3, ident, packet))[0]:
                raise AssertionError('Cabecera RDMA incorrecta')
            shift, blocks = bt & 15, bt >> 4 & 511
            if bt >> 13 or len(data) != 6 + blocks * (1 << (shift + 2)) or len(data) > 1472:
                raise AssertionError('Bloque RDMA incorrecto')
            result += data[6:]
            packet += 1
        return result

    def write(self, sector, data, ident=2):
        assert data and len(data) % 512 == 0
        assert len(data) <= 512 * 512
        self.send(hdr(4, ident) + struct.pack('<IH', sector, len(data) // 512))
        for packet, pos in enumerate(range(0, len(data), 1024), 1):
            self.send(rdma(data[pos:pos + 1024], ident, packet))
        response = self.recv()
        if response[:2] != hdr(6, ident, ident + 1):
            raise AssertionError('Cabecera WRITE_DONE incorrecta')
        return struct.unpack('<i', response[2:])[0]

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=3)
        self.sock.close()
        self.log.close()

class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = b''.join(struct.pack('<I', sector) * 128 for sector in range(2048))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.image = Path(self.temp.name) / 'unidad de prueba.img'
        self.image.write_bytes(self.content)
        self.server = Server(self.image)

    def tearDown(self):
        self.server.close()
        self.temp.cleanup()

    def silence(self):
        with self.assertRaises(socket.timeout):
            self.server.recv()
        self.assertIsNone(self.server.process.poll())

    def writable(self):
        self.server.close()
        self.server = Server(self.image, '--read-write')

    def test_selected_loopback_interface_preserves_protocol(self):
        self.server.close()
        self.server = Server(self.image, '--interface', 'lo')
        self.assertEqual(self.server.read(13, 512), self.content[13*512:525*512])

    def test_other_ingress_interface_is_ignored(self):
        interfaces = [name for _index, name in socket.if_nameindex() if name != 'lo']
        if not interfaces:
            self.skipTest('Se necesita una interfaz adicional a loopback')
        self.server.close()
        self.server = Server(self.image)
        self.server.process.terminate()
        self.server.process.wait(timeout=3)
        self.server.process = subprocess.Popen(
            [str(BINARY), '--bind', '127.0.0.1', '--port', str(self.server.address[1]),
             '--interface', interfaces[0], str(self.image)],
            stdout=self.server.log, stderr=subprocess.STDOUT)
        time.sleep(.1)
        self.server.send(hdr(0))
        self.silence()

    def test_unknown_interface_fails_clearly(self):
        result = subprocess.run([str(BINARY), '--interface', 'udpbd-no-such', str(self.image)],
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Interfaz de red no válida', result.stderr)

    def test_info_all_command_ids(self):
        for ident in range(8):
            self.server.send(hdr(0, ident))
            self.assertEqual(self.server.recv(), hdr(1, ident, 1) + struct.pack('<II', 512, 2048))

    def test_read_fragmentation_and_payload(self):
        for count in [1, 2, 3, 7, 31, 64, 127, 512]:
            with self.subTest(count=count):
                start = 13
                self.assertEqual(self.server.read(start, count, count % 8), self.content[start * 512:(start + count) * 512])

    def test_read_final_sector(self):
        self.assertEqual(self.server.read(2047, 1), self.content[-512:])

    def test_read_bounds(self):
        for start, count in [(2048, 1), (2047, 2), (0, 0), (0, 513), (0xffffffff, 2)]:
            self.server.send(hdr(2) + struct.pack('<IH', start, count))
            self.silence()

    def test_malformed_packets(self):
        for data in [b'', b'\x00', b'\x02', hdr(0) + b'junk', hdr(2) + b'\0' * 5,
                     hdr(2) + b'\0' * 7, hdr(31), hdr(5) + b'\xff' * 10, b'x' * 60000]:
            self.server.send(data)
            self.silence()
        self.assertEqual(self.server.read(0, 1), self.content[:512])

    def test_read_only_rejects_writes(self):
        self.server.send(hdr(4, 3) + struct.pack('<IH', 1, 1))
        self.assertEqual(self.server.recv(), hdr(6, 3, 4) + struct.pack('<i', -errno.EROFS))
        self.server.send(rdma(b'X' * 512, 3))
        self.silence()
        self.assertEqual(self.image.read_bytes(), self.content)

    def test_write_roundtrip(self):
        self.writable()
        data = b'Linux-PS2-' * 102 + b'abcd'
        self.assertEqual(len(data), 1024)
        self.server.send(hdr(4, 7) + struct.pack('<IH', 9, 2))
        self.server.send(rdma(data, 7))
        self.assertEqual(self.server.recv(), hdr(6, 7, 8) + struct.pack('<i', 0))
        self.assertEqual(self.server.read(9, 2), data)
        contents = self.image.read_bytes()
        self.assertEqual(contents[:9 * 512], self.content[:9 * 512])
        self.assertEqual(contents[11 * 512:], self.content[11 * 512:])

    def test_incomplete_write_is_not_committed(self):
        self.writable()
        self.server.send(hdr(4, 2) + struct.pack('<IH', 5, 2))
        self.server.send(rdma(b'X' * 512, 2))
        self.silence()
        self.assertEqual(self.image.read_bytes(), self.content)

    def test_write_wrong_peer_and_id(self):
        self.writable()
        self.server.send(hdr(4, 2) + struct.pack('<IH', 5, 1))
        self.server.send(rdma(b'X' * 512, 3))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stranger:
            stranger.sendto(rdma(b'Y' * 512, 2), self.server.address)
        self.silence()
        self.assertEqual(self.image.read_bytes(), self.content)
        self.server.send(rdma(b'Z' * 512, 2))
        self.assertEqual(struct.unpack('<i', self.server.recv()[2:])[0], 0)
        self.assertEqual(self.server.read(5, 1), b'Z' * 512)

    def test_write_bounds(self):
        self.writable()
        self.server.send(hdr(4, 1) + struct.pack('<IH', 2047, 2))
        self.assertEqual(self.server.recv(), hdr(6, 1, 2) + struct.pack('<i', -errno.EINVAL))
        self.assertEqual(self.image.read_bytes(), self.content)

    def test_write_invalid_block_and_duplicate_packet(self):
        self.writable()
        self.server.send(hdr(4, 2) + struct.pack('<IH', 6, 2))
        self.server.send(hdr(5, 2, 1) + struct.pack('<I', 15 | (1 << 4)) + b'X' * 512)
        self.server.send(rdma(b'A' * 512, 2, 1))
        self.server.send(rdma(b'Z' * 512, 2, 1))  # duplicate must not advance
        self.server.send(rdma(b'Z' * 512, 2, 8))  # out of order must not advance
        self.silence()
        self.assertEqual(self.image.read_bytes(), self.content)
        self.server.send(rdma(b'B' * 512, 2, 2))
        self.assertEqual(struct.unpack('<i', self.server.recv()[2:])[0], 0)
        self.assertEqual(self.server.read(6, 2), b'A' * 512 + b'B' * 512)

    def test_client_filter(self):
        self.server.close()
        # Allowed address differs from the source used by this test.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(('127.0.0.1', 0))
            addr = probe.getsockname()
        result = subprocess.Popen([str(BINARY), '--bind', addr[0], '--port', str(addr[1]),
                                   '--client', '127.0.0.2', str(self.image)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            ready, _, _ = select.select([result.stdout], [], [], 2)
            self.assertTrue(ready)
            time.sleep(.05)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.settimeout(.15)
                client.sendto(hdr(0), addr)
                with self.assertRaises(socket.timeout):
                    client.recv(2048)
        finally:
            result.terminate()
            result.communicate(timeout=3)

    def test_busy_port_error(self):
        result = subprocess.run([str(BINARY), '--bind', self.server.address[0], '--port',
                                 str(self.server.address[1]), str(self.image)], capture_output=True, timeout=3)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Escuchar puerto', result.stderr)

    def test_sigterm_clean_exit(self):
        self.server.process.terminate()
        self.assertEqual(self.server.process.wait(timeout=3), 0)

class CommandTests(unittest.TestCase):
    def test_help_and_version(self):
        for arg in ['--help', '--version']:
            result = subprocess.run([str(BINARY), arg], capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertIn(b'UDPBD v2', result.stdout)

    def test_invalid_arguments(self):
        for args in [[], ['--port', '0'], ['--port', '65536'], ['--port', '-1'],
                     ['--bind', 'bad', '/dev/null'], ['--client', 'bad', '/dev/null'], ['/dev/null']]:
            result = subprocess.run([str(BINARY), *args], capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_invalid_image_size(self):
        with tempfile.NamedTemporaryFile() as image:
            image.write(b'X' * 513)
            image.flush()
            result = subprocess.run([str(BINARY), image.name], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'Tama', result.stderr)

    def test_launcher_hides_system_disk_and_partition(self):
        spec = importlib.util.spec_from_file_location('lanzador', BASE / 'lanzador.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        data = {'blockdevices': [
            {'path': '/dev/system', 'type': 'disk', 'size': 1000000, 'children': [
                {'path': '/dev/system1', 'type': 'part', 'size': 500000, 'mountpoints': ['/']} ]},
            {'path': '/dev/games', 'type': 'disk', 'size': 1000000, 'children': [
                {'path': '/dev/games1', 'type': 'part', 'size': 500000, 'mountpoints': ['/media/PS2']} ]}]}
        with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(data))):
            rows = module.devices()
        self.assertEqual([row[0] for row in rows], ['/dev/games', '/dev/games1'])
        self.assertIn('/media/PS2', rows[0][-1])

if __name__ == '__main__':
    unittest.main()
