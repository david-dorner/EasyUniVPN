# Third-Party Notices

EasyUniVPN is licensed under the GNU General Public License v3.0 or later
(see [LICENSE](LICENSE)). It bundles, redistributes, or downloads at install
time the third-party components listed below. Each component remains under
its own license; the license texts are available at the linked upstream
sources and, where required, are included with the installed product.

## Modified third-party source in this repository

| Component | License | Notes |
|---|---|---|
| [openconnect-saml](https://github.com/mschabhuettl/openconnect-saml) (Matthias Schabhüttl) | GPL-3.0-or-later | `installer/assets/headless.py` is a modified copy of `openconnect_saml/headless.py`; the modifications are documented in the file header. The unmodified package is also installed from PyPI at install time and bundled inside `EasyUniVPNCli.exe`. |

## Binaries redistributed in the installer (`runtime/openconnect/`)

These are unmodified binaries taken from the Windows distribution of
[OpenConnect-GUI](https://gitlab.com/openconnect/openconnect-gui), which
packages the OpenConnect VPN client (v9.12) and its dependency libraries.
Corresponding source code is available from the upstream projects linked
below. The `LICENSE.txt` shipped alongside the binaries is retained verbatim.

| Component | License | Source |
|---|---|---|
| OpenConnect (`openconnect.exe`, `libopenconnect-5.dll`, `vpnc-script*.js`) | LGPL-2.1 | <https://gitlab.com/openconnect/openconnect> |
| GnuTLS (`libgnutls-30.dll`) | LGPL-2.1-or-later | <https://gnutls.org> |
| Nettle / Hogweed (`libnettle-8.dll`, `libhogweed-6.dll`) | LGPL-3.0 / GPL-2.0 dual | <https://www.lysator.liu.se/~nisse/nettle/> |
| GMP (`libgmp-10.dll`) | LGPL-3.0 / GPL-2.0 dual | <https://gmplib.org> |
| libxml2 (`libxml2-2.dll`) | MIT | <https://gitlab.gnome.org/GNOME/libxml2> |
| p11-kit (`libp11-kit-0.dll`) | BSD-3-Clause | <https://p11-glue.github.io/p11-glue/p11-kit.html> |
| libtasn1 (`libtasn1-6.dll`) | LGPL-2.1-or-later | <https://www.gnu.org/software/libtasn1/> |
| libidn2 (`libidn2-0.dll`) | LGPL-3.0-or-later / GPL-2.0-or-later dual | <https://www.gnu.org/software/libidn/> |
| libunistring (`libunistring-5.dll`) | LGPL-3.0-or-later / GPL-2.0-or-later dual | <https://www.gnu.org/software/libunistring/> |
| libiconv / libintl (`libiconv-2.dll`, `libintl-8.dll`) | LGPL-2.1-or-later | <https://www.gnu.org/software/libiconv/> |
| stoken (`libstoken-1.dll`) | LGPL-2.1-or-later | <https://github.com/stoken-dev/stoken> |
| liblz4 (`liblz4.dll`) | BSD-2-Clause | <https://github.com/lz4/lz4> |
| liblzma (`liblzma-5.dll`) | 0BSD | <https://tukaani.org/xz/> |
| zlib (`zlib1.dll`) | zlib | <https://zlib.net> |
| libffi (`libffi-8.dll`) | MIT | <https://github.com/libffi/libffi> |
| MinGW-w64 runtime (`libgcc_s_seh-1.dll`, `libstdc++-6.dll`, `libwinpthread-1.dll`) | GPL-3.0 with GCC Runtime Library Exception / MIT (winpthread) | <https://www.mingw-w64.org> |
| Wintun (`wintun.dll`) | Prebuilt Binaries License (permits redistribution as part of a larger application) | <https://www.wintun.net> |

## Downloaded at install time (not redistributed)

The installer's bootstrap step downloads these from their official
distribution points onto the user's machine; they are not contained in the
installer itself.

| Component | License | Source |
|---|---|---|
| Python 3.12 (Windows embeddable package) | PSF-2.0 | <https://www.python.org> |
| pip (via get-pip.py) | MIT | <https://pip.pypa.io> |
| All packages pinned in `installer/requirements.lock.txt` | See below | PyPI |

## Python packages bundled inside `EasyUniVPNCli.exe`

`EasyUniVPNCli.exe` is a PyInstaller bundle containing the packages pinned in
`cli/requirements.lock.txt`:

| Package | License |
|---|---|
| openconnect-saml | GPL-3.0-or-later |
| attrs, keyring, jaraco.classes, jaraco.context, jaraco.functools, more-itertools, toml, urllib3, charset-normalizer, wcwidth, PyOTP | MIT |
| requests, structlog | Apache-2.0 |
| certifi | MPL-2.0 |
| colorama, idna, lxml, PySocks, prompt_toolkit, pywin32-ctypes | BSD |
| Pillow | MIT-CMU |
| pystray | LGPL-3.0 |
| pyxdg | LGPL-2.1 |
| typing_extensions | PSF-2.0 |
| PyInstaller bootloader | GPL-2.0-or-later with a special exception permitting bundling |

## Icons

The tray icons are the "shield-check" and "shield-off" glyphs from
[Lucide](https://lucide.dev) (lucide-static v1.25.0), embedded as vector path
data in `tray/LucideIcons.cs`; the SVG sources and full upstream license text
are kept in `assets/lucide/`. Lucide is licensed under the ISC License:

> ISC License
>
> Copyright (c) 2026 Lucide Icons and Contributors
>
> Permission to use, copy, modify, and/or distribute this software for any
> purpose with or without fee is hereby granted, provided that the above
> copyright notice and this permission notice appear in all copies.
>
> THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
> WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
> MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
> ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
> WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
> ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
> OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

## Trademarks

"University of Graz", "TU Graz", "uniLOGIN", "privacyIDEA", "Cisco",
"AnyConnect", and "Studo" are trademarks of their respective owners.
EasyUniVPN is an independent project and is not affiliated with, endorsed
by, or supported by any of them.
