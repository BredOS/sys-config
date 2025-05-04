# Maintainer: Bill Sideris <bill88t@bredos.org>

pkgname=bredos-sysconfig
pkgver=1.2.0
pkgrel=1
pkgdesc='BredOS System Configurator and Management utility'
arch=(any)
url=https://github.com/BredOS/sys-config
license=('GPL3')
provides=("bredos-config")

depends=(python)

source=('sys-config.py' 'bredos-sysconfig.desktop')
sha256sums=('SKIP' 'SKIP')

package() {
    mkdir -p "${pkgdir}/usr/bin"
    install -Dm755 "${srcdir}/sys-config.py" "${pkgdir}/usr/bin/bredos-config"
    install -Dm644 "${srcdir}/bredos-sysconfig.desktop" "${pkgdir}/usr/share/applications/bredos-sysconfig.desktop"
}
