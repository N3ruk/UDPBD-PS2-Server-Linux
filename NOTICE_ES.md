# Créditos y componentes

[English](NOTICE.md)

Aplicación Linux y empaquetado: N3ruk. Implementación C++17 compatible con UDPBD v2.
Referencia del protocolo: Rick Gaiser; servidor de referencia publicado por
[israpps/udpbd-server](https://github.com/israpps/udpbd-server), versión Windows de Alex Parrado.
Este repositorio no incluye juegos, firmware ni el ejecutable Windows.

El código propio se distribuye bajo MIT. El icono fue proporcionado por N3ruk y
se incluye para identificar esta aplicación, separado de la licencia del código.
PlayStation es una marca de su propietario; este es un proyecto independiente.

La AppImage incluye CPython 3.12.16 y Tcl/Tk 9.0 de
[python-build-standalone](https://github.com/astral-sh/python-build-standalone),
sus componentes asociados, fuentes DejaVu y el runtime AppImage.
El backend estático incluye musl y componentes de LLVM/libc++.
Los textos de licencia correspondientes están en `licenses/` y se instalan
junto a la aplicación. El DEB utiliza Python/Tk mantenidos por Ubuntu.
