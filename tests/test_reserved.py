import unittest, tempfile, struct
from pathlib import Path
import test_udpbd as protocol
class ReservedBitsCompatibility(unittest.TestCase):
    def test_reserved_bits_single_and_multisector(self):
        with tempfile.TemporaryDirectory(prefix='udpbd-reserved-test-') as folder:
            image=Path(folder)/'fixture.img'; image.write_bytes(bytes(32*512))
            server=protocol.Server(image,'--read-write','--interface','lo')
            try:
                for index, spare in enumerate((0,1,0x15555,0x7ffff)):
                    with self.subTest(spare=spare):
                        ident=index; sector=index*4
                        payload=bytes([index+65])*1536
                        server.send(protocol.hdr(4,ident)+struct.pack('<IH',sector,3))
                        for packet in range(1,4):
                            server.send(protocol.hdr(5,ident,packet)+struct.pack('<I',5|(4<<4)|(spare<<13))+payload[(packet-1)*512:packet*512])
                        self.assertEqual(server.recv(),protocol.hdr(6,ident,ident+1)+struct.pack('<i',0))
                        self.assertEqual(server.read(sector,3,ident=7),payload)
            finally: server.close()
