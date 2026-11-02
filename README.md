<p align="center"><img src="assets/udpbd-ps2-server.png" width="220" alt="UDPBD PS2 Server"></p>

# UDPBD PS2 Server · Linux

**English** | [Español](README_ES.md)

Share a PlayStation 2 game directory over **UDPBD v2** using a straightforward native Linux GUI. Works with UDPBD-capable clients such as compatible OPL and OSD-XMB/Neutrino builds.

## Why it exists

This project provides an easy-to-use Linux UDPBD server without a Windows executable or a manually prepared disk. Select a directory, choose your network interface and press **Start server** (`Iniciar servidor`).

- Start/stop controls, live status and traffic monitoring.
- Shares standalone ISOs, DVD/CD folders or an OPL library root.
- Does not copy games or modify original files.
- Presents a virtual exFAT drive with temporary client writes.
- Shows the server IP to use on your PS2.
- No administrator privileges required to run.

## Download

Get packages from the [latest release](https://github.com/N3ruk/UDPBD-PS2-Server-Linux/releases/latest).

| Package | Purpose |
| --- | --- |
| `UDPBD-PS2-Server-1.0.0-x86_64.AppImage` | Portable; includes Python, Tk and the server |
| `udpbd-ps2-server_1.0.0_amd64.deb` | Ubuntu installation with an application-menu entry |

Both packages target **x86_64/amd64**, not ARM or 32-bit Linux. No games are included.

### AppImage

Allow execution in the file's Properties or run:

```sh
chmod +x UDPBD-PS2-Server-1.0.0-x86_64.AppImage
./UDPBD-PS2-Server-1.0.0-x86_64.AppImage
```

Includes Python, Tcl/Tk, fonts, GUI and backend. Requires **glibc 2.31+** and X11, or Wayland with XWayland. If FUSE is unavailable, use:

```sh
./UDPBD-PS2-Server-1.0.0-x86_64.AppImage --appimage-extract-and-run
```

No services or system files are installed.

### Ubuntu DEB

```sh
sudo apt install ./udpbd-ps2-server_1.0.0_amd64.deb
```

APT resolves Python 3, Tk and font dependencies. Open **UDPBD PS2 Server** from the menu. Targets Ubuntu 22.04+ amd64. GitHub CI verifies DEB installation, testing and removal on Ubuntu 22.04; the GUI/AppImage were also checked on Ubuntu 26.04.1. Do not substitute `dpkg -i` for APT when dependencies are missing.

## Use with your PS2

1. Connect the computer and PS2 to the same network, preferably Ethernet.
2. Open the application and select your games directory.
3. Select the IP/interface connected to the PS2, particularly if Wi-Fi is also active.
4. Press **Iniciar servidor** and wait for the active status.
5. Select UDPBD mode in your PS2 loader and enter the displayed server IP if requested. The service uses **UDP port 48573**.
6. Monitor traffic and press **Parar servidor** when finished.

You may select DVD, CD, a directory of ISOs or the OPL root. Keep shared files unchanged while running. No dedicated partition, formatting or manual mounting is required.

### Temporary writes

The client can write to the virtual drive while the server is active, but changes are **discarded when it stops or closes**. Original ISOs remain unchanged. **Do not use this mode for VMC saves that must survive between sessions**: use a physical memory card or other persistent storage. There is no permanent library import/copy mode.

## Troubleshooting

- Confirm the PS2 loader supports UDPBD v2; this is not SMB.
- Check the interface and ensure both devices are in the same subnet.
- If a firewall is enabled, allow UDP 48573 from your local network.
- Close another server instance that might occupy the port.
- Inspect the application's log for network/file errors.

The application does not automatically change the firewall, download games or require Internet access to share files.

## Check and uninstall

The optional GUI/UDP self-test uses synthetic files and loopback, not your PS2. It requires a graphical session:

```sh
udpbd-ps2-server --self-test
# Or:
./UDPBD-PS2-Server-1.0.0-x86_64.AppImage --self-test
```

Remove the Ubuntu package with:

```sh
sudo apt purge udpbd-ps2-server
```

For AppImage, delete its file. Games are never removed. Preferences are in `~/.config/udpbd-ubuntu/` and local logs in `~/.local/state/udpbd-ubuntu/`; remove those separately if desired.

## Source and build

Python 3.10+, Tk and a C++17 compiler are needed to run from source:

```sh
make
python3 main.py
```

Build both packages with `python3 packaging/build.py`. Requires Python 3, `make`, `dpkg-deb`, `tar`, `xz`, `strip` and Internet access for the first download of hash-pinned tools from `packaging/toolchain-lock.json`. No global tool installation is performed.

[Credits and licenses](NOTICE.md) · [Créditos en español](NOTICE_ES.md).
