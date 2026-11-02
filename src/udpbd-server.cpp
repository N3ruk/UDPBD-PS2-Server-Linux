// UDPBD v2 wire-compatible implementation for Linux (C++17).
// Protocol reference: Rick Gaiser / israpps/udpbd-server, udpbd.h.
#include <arpa/inet.h>
#include <sys/socket.h>
#include <net/if.h>
#include <sys/stat.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/sysmacros.h>
#include <linux/fs.h>
#include <unistd.h>
#include <fcntl.h>
#include <grp.h>
#include <pwd.h>
#include <signal.h>
#include <cerrno>
#include <cstring>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <fstream>
#include <sstream>
#include <filesystem>
#include <vector>
#include <array>
#include <chrono>
#include <stdexcept>
#include <limits>

namespace fs = std::filesystem;
static volatile sig_atomic_t running = 1;
static void stop(int) { running = 0; }
static void fail(const std::string& msg) { throw std::runtime_error(msg); }
static void syserr(const std::string& msg) { fail(msg + ": " + std::strerror(errno)); }
static uint16_t u16(const uint8_t* p) { return uint16_t(p[0]) | uint16_t(p[1]) << 8; }
static uint32_t u32(const uint8_t* p) { return uint32_t(u16(p)) | uint32_t(u16(p+2)) << 16; }
static void put16(uint8_t* p, uint16_t v) { p[0]=v; p[1]=v>>8; }
static void put32(uint8_t* p, uint32_t v) { put16(p,v); put16(p+2,v>>16); }
static uint16_t header(unsigned cmd, unsigned id, unsigned pkt) { return cmd | (id<<5) | ((pkt&255)<<8); }
static unsigned number(const std::string& s, unsigned max) {
    if (s.empty() || s.find_first_not_of("0123456789") != std::string::npos) fail("Número inválido: " + s);
    auto n=std::stoull(s); if (n>max) fail("Número fuera de rango: " + s); return n;
}
static bool below(const fs::path& a, const fs::path& b) {
    auto as=a.string(), bs=b.string(); return as==bs || as.rfind(bs+"/",0)==0;
}
static void check_unmounted(dev_t device) {
    std::error_code ec;
    auto target=fs::canonical("/sys/dev/block/"+std::to_string(major(device))+":"+std::to_string(minor(device)),ec);
    if (ec) fail("No se puede verificar el estado de montaje");
    std::ifstream mounts("/proc/self/mountinfo");
    if (!mounts) fail("No se puede leer mountinfo");
    std::string line;
    while (std::getline(mounts,line)) {
        std::istringstream row(line); std::string id,parent,dev; row>>id>>parent>>dev;
        auto mounted=fs::canonical("/sys/dev/block/"+dev,ec);
        if (!ec && (below(mounted,target)||below(target,mounted)))
            fail("La unidad o una de sus particiones está montada. Desmóntala antes de activar escritura.");
    }
}
static void check_image_unmounted(const std::string& path) {
    std::error_code ec;
    for (const auto& entry: fs::directory_iterator("/sys/block")) {
        std::ifstream in(entry.path()/"loop/backing_file"); std::string backing;
        if (std::getline(in,backing)) {
            auto real=fs::canonical(backing,ec);
            if (!ec && real==path) fail("La imagen está asociada a un dispositivo loop. Desvincúlala antes de escribir.");
        }
    }
}
static void drop_privileges() {
    if (geteuid()!=0) return;
    const char* value=std::getenv("PKEXEC_UID");
    if (!value) value=std::getenv("SUDO_UID");
    if (!value) fail("No ejecutar como root directamente; usa el lanzador o pkexec desde tu usuario.");
    uid_t uid=number(value,std::numeric_limits<uid_t>::max());
    passwd* pw=getpwuid(uid);
    if (!uid || !pw) fail("Usuario de origen inválido");
    if (setgroups(0,nullptr)<0 || setgid(pw->pw_gid)<0 || setuid(uid)<0) syserr("Reducir privilegios");
    if (geteuid()==0) fail("No se han reducido los privilegios");
}
static bool exact_io(int fd, uint8_t* data, size_t size, uint64_t offset, bool write) {
    size_t done=0;
    while (done<size) {
        ssize_t n=write ? pwrite(fd,data+done,size-done,offset+done) : pread(fd,data+done,size-done,offset+done);
        if (n<0 && errno==EINTR) continue;
        if (n<=0) return false;
        done+=size_t(n);
    }
    return true;
}
struct Handle { int fd=-1; ~Handle(){ if(fd>=0) close(fd); } };
struct Pending {
    bool active=false; unsigned id=0,next=0; bool first=true;
    sockaddr_in peer{}; uint64_t offset=0; size_t size=0;
    std::vector<uint8_t> data;
    std::chrono::steady_clock::time_point started;
};
static bool same_peer(const sockaddr_in& a,const sockaddr_in& b) {
    return a.sin_addr.s_addr==b.sin_addr.s_addr && a.sin_port==b.sin_port;
}
#include "virtual-disk.h"

int main(int argc,char** argv) {
    try {
        bool writable=false,virtual_mode=false,telemetry=false; std::string path,bind_ip="0.0.0.0",client,interface,trace_path; unsigned port=48573,packet_delay=0;
        for(int i=1;i<argc;i++) {
            std::string a=argv[i];
            if(a=="--help" || a=="-h") {
                std::cout<<"UDPBD Ubuntu 1.0 — protocolo UDPBD v2\nUso: udpbd-server [--read-write] [--bind IPv4] [--interface INTERFAZ] [--port 48573] [--client IPv4] /dev/partición_o_imagen\nSolo lectura por defecto. --virtual: descriptor de carpeta exFAT. Con --read-write usa escritura temporal (se pierde al parar), sin tocar los originales. --telemetry: monitor JSON. Ctrl+C para detener.\n"; return 0;
            } else if(a=="--version") { std::cout<<"udpbd-server Ubuntu 1.0 (UDPBD v2)\n"; return 0;
            } else if(a=="--virtual") virtual_mode=true;
            else if(a=="--telemetry") telemetry=true;
            else if(a=="--read-write") writable=true;
            else if(a=="--read-only") writable=false;
            else if(a=="--bind" || a=="--client" || a=="--port" || a=="--interface" || a=="--trace" || a=="--packet-delay-us") {
                if(++i>=argc) fail("Falta valor para " + a);
                if(a=="--bind") bind_ip=argv[i]; else if(a=="--client") client=argv[i];
                else if(a=="--interface") interface=argv[i];
                else if(a=="--trace") trace_path=argv[i];
                else if(a=="--packet-delay-us") packet_delay=number(argv[i],5000);
                else { port=number(argv[i],65535); if(!port) fail("Puerto inválido"); }
            } else if(a=="--") { if(++i!=argc-1 || !path.empty()) fail("Falta una única ruta"); path=argv[i]; }
            else if(a.rfind("-",0)==0 || !path.empty()) fail("Argumento inválido: "+a);
            else path=a;
        }
        if(path.empty()) fail("Selecciona una partición o imagen. Consulta --help.");
        sockaddr_in local{},allowed{}; local.sin_family=AF_INET; local.sin_port=htons(port);
        if(inet_pton(AF_INET,bind_ip.c_str(),&local.sin_addr)!=1) fail("IPv4 local inválida");
        if(!client.empty() && inet_pton(AF_INET,client.c_str(),&allowed.sin_addr)!=1) fail("IPv4 de PS2 inválida");
        unsigned interface_index=interface.empty()?0:if_nametoindex(interface.c_str());
        if(!interface.empty() && !interface_index) fail("Interfaz de red no válida: "+interface);
        Handle trace_file;
        if(!trace_path.empty()) {
            trace_file.fd=open(trace_path.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
            if(trace_file.fd<0) syserr("Crear registro de solicitudes");
        }
        auto trace=[&](const char* event,const sockaddr_in& peer,unsigned cmd,unsigned id,unsigned pkt,uint64_t sector,unsigned count,int result) {
            if(trace_file.fd<0) return;
            char ip[INET_ADDRSTRLEN]; inet_ntop(AF_INET,&peer.sin_addr,ip,sizeof(ip));
            auto us=std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
            if(dprintf(trace_file.fd,"{\"us\":%lld,\"event\":\"%s\",\"peer\":\"%s:%u\",\"cmd\":%u,\"id\":%u,\"pkt\":%u,\"sector\":%llu,\"count\":%u,\"result\":%d}\n",
                (long long)us,event,ip,ntohs(peer.sin_port),cmd,id,pkt,(unsigned long long)sector,count,result)<0)
                syserr("Guardar registro de solicitudes");
        };
        Handle disk;
        std::unique_ptr<VirtualDisk> virtual_disk;
        uint64_t bytes=0;
        path=fs::canonical(path).string();
        if(virtual_mode) {
            virtual_disk=std::make_unique<VirtualDisk>(path,writable);
            bytes=virtual_disk->bytes();
        } else {
        struct stat st{}; if(stat(path.c_str(),&st)<0) syserr("Examinar origen");
        if(!S_ISREG(st.st_mode) && !S_ISBLK(st.st_mode)) fail("El origen debe ser una imagen o un dispositivo de bloques");
        // Exclusive block-device open prevents writes to a currently mounted disk/partition.
        int flags=(writable?O_RDWR:O_RDONLY)|O_CLOEXEC;
        if(writable && S_ISBLK(st.st_mode)) { check_unmounted(st.st_rdev); flags|=O_EXCL; }
        if(writable && S_ISREG(st.st_mode)) check_image_unmounted(path);
        disk.fd=open(path.c_str(),flags); if(disk.fd<0) syserr("Abrir "+path);
        if(flock(disk.fd,(writable?LOCK_EX:LOCK_SH)|LOCK_NB)<0) syserr("Bloquear origen");
        bytes=st.st_size;
        if(S_ISBLK(st.st_mode)) {
            int sector=0;
            if(ioctl(disk.fd,BLKGETSIZE64,&bytes)<0 || ioctl(disk.fd,BLKSSZGET,&sector)<0) syserr("Consultar unidad");
            if(sector!=512) fail("Esta versión requiere sectores lógicos de 512 bytes, como UDPBD original para PS2.");
        }
        if(bytes<512 || bytes%512 || bytes/512>UINT32_MAX) fail("Tamaño incompatible: múltiplo de 512 bytes, hasta 2 TiB menos un sector.");
        }
        drop_privileges(); // Only opening the disk may require elevation, never the UDP loop.
        std::cout.setf(std::ios::unitbuf); std::cerr.setf(std::ios::unitbuf);
        std::cout<<"Origen: "<<path<<"\nModo: "<<(writable?"lectura/escritura":"solo lectura")
                 <<"\nTamaño: "<<bytes/(1024*1024)<<" MiB; sectores: "<<bytes/512<<" x 512\n";
        if(virtual_disk && writable) std::cout<<"Escritura temporal de sesión (máximo 512 MiB); originales intactos. Se pierde al parar.\n";
        if(packet_delay) std::cout<<"Intervalo RDMA: "<<packet_delay<<" us entre paquetes de lectura\n";
        Handle sock; sock.fd=socket(AF_INET,SOCK_DGRAM|SOCK_CLOEXEC,0); if(sock.fd<0) syserr("socket");
        int yes=1,bufsize=1024*1024;
        setsockopt(sock.fd,SOL_SOCKET,SO_BROADCAST,&yes,sizeof(yes));
        setsockopt(sock.fd,SOL_SOCKET,SO_SNDBUF,&bufsize,sizeof(bufsize));
        if(setsockopt(sock.fd,IPPROTO_IP,IP_PKTINFO,&yes,sizeof(yes))<0) syserr("Activar información de interfaz");
        if(bind(sock.fd,reinterpret_cast<sockaddr*>(&local),sizeof(local))<0) syserr("Escuchar puerto");
        struct sigaction action{}; action.sa_handler=stop; sigemptyset(&action.sa_mask);
        sigaction(SIGINT,&action,nullptr); sigaction(SIGTERM,&action,nullptr);
        std::cout<<"Servidor listo en "<<bind_ip<<":"<<port<<" UDP. Esperando PS2...\n";
        if(interface_index) std::cout<<"Interfaz exclusiva: "<<interface<<" (broadcast conservado)\n";
        timeval timeout{0,250000};
        setsockopt(sock.fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
        Traffic traffic(telemetry);
        in_pktinfo route{};
        auto send=[&](const uint8_t* p,size_t n,const sockaddr_in& peer) {
            iovec payload{const_cast<uint8_t*>(p),n};
            alignas(cmsghdr) std::array<char,CMSG_SPACE(sizeof(in_pktinfo))> control{};
            msghdr message{}; message.msg_name=const_cast<sockaddr_in*>(&peer); message.msg_namelen=sizeof(peer);
            message.msg_iov=&payload; message.msg_iovlen=1;
            message.msg_control=control.data(); message.msg_controllen=control.size();
            auto cmsg=CMSG_FIRSTHDR(&message); cmsg->cmsg_level=IPPROTO_IP; cmsg->cmsg_type=IP_PKTINFO;
            cmsg->cmsg_len=CMSG_LEN(sizeof(in_pktinfo));
            std::memcpy(CMSG_DATA(cmsg),&route,sizeof(route));
            ssize_t r;
            do { r=sendmsg(sock.fd,&message,0); } while(r<0 && errno==EINTR && running);
            if(r!=ssize_t(n) && running) syserr("Enviar paquete");
            if(r==ssize_t(n)) traffic.packet(true,p,n,peer);
        };
        auto done=[&](unsigned id,int32_t result,const sockaddr_in& peer) {
            uint8_t p[6]{}; put16(p,header(6,id,id+1)); put32(p+2,uint32_t(result)); send(p,sizeof(p),peer);
        };
        uint64_t total_read=0,total_write=0; Pending pending;
        std::array<uint8_t,65536> incoming{};
        while(running) {
            traffic.flush();
            if(virtual_disk) virtual_disk->validate();
            sockaddr_in peer{};
            iovec payload{incoming.data(),incoming.size()};
            alignas(cmsghdr) std::array<char,CMSG_SPACE(sizeof(in_pktinfo))> control{};
            msghdr message{}; message.msg_name=&peer; message.msg_namelen=sizeof(peer);
            message.msg_iov=&payload; message.msg_iovlen=1;
            message.msg_control=control.data(); message.msg_controllen=control.size();
            ssize_t n=recvmsg(sock.fd,&message,0);
            if(n<0) { if(errno==EINTR || errno==EAGAIN || errno==EWOULDBLOCK) continue; syserr("Recibir paquete"); }
            route={};
            for(auto cmsg=CMSG_FIRSTHDR(&message);cmsg;cmsg=CMSG_NXTHDR(&message,cmsg))
                if(cmsg->cmsg_level==IPPROTO_IP && cmsg->cmsg_type==IP_PKTINFO && cmsg->cmsg_len>=CMSG_LEN(sizeof(route)))
                    std::memcpy(&route,CMSG_DATA(cmsg),sizeof(route));
            if(message.msg_flags&(MSG_TRUNC|MSG_CTRUNC)) continue;
            if(interface_index && unsigned(route.ipi_ifindex)!=interface_index) continue;
            // Use the ingress interface's unicast address, never its broadcast address.
            route.ipi_addr={};
            traffic.packet(false,incoming.data(),n,peer);
            if(n<2 || (!client.empty() && peer.sin_addr.s_addr!=allowed.sin_addr.s_addr)) continue;
            auto p=incoming.data(); unsigned h=u16(p),cmd=h&31,id=(h>>5)&7,pkt=h>>8;
            trace("rx",peer,cmd,id,pkt,((cmd==2 || cmd==4) && n==8)?u32(p+2):0,
                  ((cmd==2 || cmd==4) && n==8)?u16(p+6):unsigned(n),0);
            auto now=std::chrono::steady_clock::now();
            if(pending.active && now-pending.started>std::chrono::seconds(5)) pending.active=false;
            if(cmd==0 && n==2) {
                uint8_t reply[10]{}; put16(reply,header(1,id,1)); put32(reply+2,512); put32(reply+6,bytes/512);
                send(reply,sizeof(reply),peer);
                char ip[INET_ADDRSTRLEN]; inet_ntop(AF_INET,&peer.sin_addr,ip,sizeof(ip));
                std::cout<<"PS2/cliente: "<<ip<<"; leídos "<<total_read/1024<<" KiB, escritos "<<total_write/1024<<" KiB\n";
            } else if((cmd==2 || cmd==4) && n==8 && pkt==0) {
                uint32_t sector=u32(p+2); unsigned count=u16(p+6);
                if(!count || count>512 || uint64_t(sector)+count>bytes/512) { trace("reject",peer,cmd,id,pkt,sector,count,-EINVAL); if(cmd==4) done(id,-EINVAL,peer); continue; }
                size_t size=size_t(count)*512; uint64_t offset=uint64_t(sector)*512;
                if(cmd==4) {
                    if(!writable) { done(id,-EROFS,peer); continue; }
                    if(pending.active && !same_peer(peer,pending.peer)) { done(id,-EBUSY,peer); continue; }
                    pending=Pending{}; pending.active=true; pending.id=id; pending.peer=peer;
                    pending.size=size; pending.offset=offset; pending.data.reserve(size); pending.started=now;
                    continue;
                }
                std::vector<uint8_t> data(size);
                if(!(virtual_disk ? virtual_disk->read(data.data(),size,offset) : exact_io(disk.fd,data.data(),size,offset,false))) { std::cerr<<"Error leyendo sector "<<sector<<"\n"; continue; }
                unsigned minimum=(size+1439)/1440,shift=3;
                if((size+1023)/1024==minimum) shift=7;
                else if((size+1279)/1280==minimum) shift=6;
                else if((size+1407)/1408==minimum) shift=5;
                unsigned block=1u<<(shift+2),per_packet=1466/block,seq=1;
                size_t sent=0; std::array<uint8_t,1472> reply{};
                while(sent<size && running) {
                    // OPL's in-game SMAP ISR can overrun on an unpaced 64-KiB burst.
                    // Keep packet format unchanged; allow the receiver time to drain its FIFO.
                    if(sent && packet_delay) {
                        timespec pause{0,long(packet_delay)*1000};
                        while(nanosleep(&pause,&pause)<0 && errno==EINTR && running) {}
                        if(!running) break;
                    }
                    unsigned blocks=std::min(size_t(per_packet),(size-sent)/block),chunk=blocks*block;
                    put16(reply.data(),header(3,id,seq++)); put32(reply.data()+2,shift|(blocks<<4));
                    std::memcpy(reply.data()+6,data.data()+sent,chunk); send(reply.data(),chunk+6,peer); sent+=chunk;
                }
                trace("read_done",peer,cmd,id,seq-1,sector,count,0);
                total_read+=sent;
            } else if(cmd==5 && n>=6 && pending.active && pending.id==id && same_peer(peer,pending.peer)) {
                uint32_t bt=u32(p+2); unsigned shift=bt&15,blocks=(bt>>4)&511;
                // OPL does not initialize the 19 reserved bits; they are not part of the block length.
                if(shift>7 || !blocks) continue;
                size_t chunk=size_t(blocks)<<(shift+2);
                if(chunk>1466 || chunk!=size_t(n-6) || chunk>pending.size-pending.data.size()) continue;
                if(!pending.first && pkt!=pending.next) continue;
                pending.first=false; pending.next=(pkt+1)&255;
                pending.data.insert(pending.data.end(),p+6,p+n);
                if(pending.data.size()==pending.size) {
                    int result=0;
                    if(virtual_disk) result=virtual_disk->write(pending.data.data(),pending.size,pending.offset);
                    else {
                        bool ok=exact_io(disk.fd,pending.data.data(),pending.size,pending.offset,true);
                        if(ok && fdatasync(disk.fd)<0) ok=false;
                        if(!ok) result=-EIO;
                    }
                    if(result==0) total_write+=pending.size;
                    else std::cerr<<"Error escribiendo sector "<<pending.offset/512<<": "<<std::strerror(-result)<<"\n";
                    trace("write_done",peer,cmd,id,pkt,pending.offset/512,pending.size/512,result);
                    done(id,result,peer); pending.active=false;
                }
            }
        }
        std::cout<<"Servidor detenido. Leídos "<<total_read<<" bytes; escritos "<<total_write<<" bytes.\n";
        return 0;
    } catch(const std::exception& e) { std::cerr<<"Error: "<<e.what()<<"\n"; return 1; }
}
