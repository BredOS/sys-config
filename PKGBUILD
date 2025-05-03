# Maintainer: Bill Sideris <bill88t@bredos.org>

pkgname=bredos-sysconfig
pkgver=1.0.0
pkgrel=1
pkgdesc='BredOS System Configurator and Management utility'
arch=(any)
url=https://github.com/BredOS/sys-config
license=('GPL3')

depends=(python)

source=('sys-config.py')
sha256sums=('SKIP')

package() {
    mkdir -p "${pkgdir}/usr/bin"
    install -Dm755 "${srcdir}/sys-config.py" "${pkgdir}/usr/bin/bredos-config"
}
