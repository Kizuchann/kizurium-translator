# Maintainer: Kizuchann
#
#   git clone https://github.com/Kizuchann/kizurium-translator.git
#   cd kizurium-translator
#   makepkg -si
#
# Update:
#   git pull && makepkg -su

pkgname=kizurium-translator
pkgver=1.0.0
pkgrel=1
pkgdesc="Wayland-native screen translator and OCR tool for Linux with live translation overlays"
arch=('x86_64' 'aarch64')
url="https://github.com/Kizuchann/kizurium-translator"
license=('AGPL3-only')
depends=(
  'gtk4'
  'gtk4-layer-shell'
  'gobject-introspection'
  'python-gobject'
  'python-cairo'
  'python-pillow'
  'python-numpy'
  'python-pytesseract'
  'python-requests'
  'tesseract'
  'tesseract-data-eng'
)
optdepends=(
  'grim: screen capture for live translation'
  'slurp: pick a region with the mouse'
  'quickshell: region picker'
  'tesseract-data-jpn: Japanese OCR'
  'tesseract-data-rus: Russian OCR'
)
makedepends=('python-pip')
source=()
sha256sums=()

build() {
  cd "$startdir"
  chmod u+rwx "$startdir/pkg" 2>/dev/null || true
  # --ignore-installed: without it pip uninstalls the copy already in
  # /usr/lib/python3.x/site-packages first, and build() runs unprivileged, so it
  # dies on the root-owned console script with EACCES as soon as the package is
  # installed twice.
  python -m pip install --root="$pkgdir" --no-deps --ignore-installed .
}

package() {
  chmod u+rwx "$startdir/pkg" 2>/dev/null || true
  install -Dm644 "$startdir/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
  install -Dm644 "$startdir/NOTICE" "$pkgdir/usr/share/licenses/$pkgname/NOTICE"
  install -Dm644 "$startdir/config.example.toml" "$pkgdir/usr/share/$pkgname/config.example.toml"
  install -Dm755 "$startdir/scripts/arch-wrapper" "$pkgdir/usr/bin/$pkgname"
}