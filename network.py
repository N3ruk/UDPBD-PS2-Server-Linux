"""IPv4 activas de Linux sin depender de iproute2 ni ejecutar programas."""
import ctypes as C
import socket

class Sockaddr(C.Structure):
    _fields_ = [('family', C.c_ushort), ('data', C.c_ubyte * 14)]

class Ifaddrs(C.Structure):
    pass

Ifaddrs._fields_ = [('next', C.POINTER(Ifaddrs)), ('name', C.c_char_p),
                   ('flags', C.c_uint), ('addr', C.POINTER(Sockaddr)),
                   ('netmask', C.POINTER(Sockaddr)), ('union', C.c_void_p),
                   ('data', C.c_void_p)]

def local_ips():
    libc = C.CDLL(None, use_errno=True)
    libc.getifaddrs.argtypes = [C.POINTER(C.POINTER(Ifaddrs))]
    libc.getifaddrs.restype = C.c_int
    libc.freeifaddrs.argtypes = [C.POINTER(Ifaddrs)]
    head = C.POINTER(Ifaddrs)()
    if libc.getifaddrs(C.byref(head)) != 0:
        return []
    result = []
    try:
        current = head
        while current:
            item = current.contents
            if item.addr and item.addr.contents.family == socket.AF_INET and item.flags & 1 and not item.flags & 8:
                address = socket.inet_ntoa(bytes(item.addr.contents.data)[2:6])
                result.append((f'{address} — {item.name.decode()}', address))
            current = item.next
    finally:
        libc.freeifaddrs(head)
    return result
