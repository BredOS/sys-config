# Maintainer: Bill Sideris <bill88t@bredos.org>

pkgname=bredos-sysconfig
pkgver=1.5.1
pkgrel=1
pkgdesc='BredOS System Configurator and Management utility'
arch=(any)
url=https://github.com/BredOS/sys-config
license=('GPL3')
provides=("bredos-config")

depends=('python' 'dtc' 'python-bredos-common>=1.4.1')
optdepends=('u-boot-update: Automatic U-Boot Updates')

source=('sys-config.py' 'bredos-sysconfig.desktop')
sha256sums=('fb2c0c33d7513eb258b59fe92c50bb4e0a78f11227dd8af5fd27fbfe13679585'
            '3f43196e365720274e2a7f3273a921cc1bd669f4c122e8d6eb63c2dfc98dabe9')

package() {
    mkdir -p "${pkgdir}/usr/bin"
    install -Dm755 "${srcdir}/sys-config.py" "${pkgdir}/usr/bin/bredos-config"
    install -Dm644 "${srcdir}/bredos-sysconfig.desktop" "${pkgdir}/usr/share/applications/bredos-sysconfig.desktop"
}
