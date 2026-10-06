# Maintainer: Kizuchann
#
#   git clone https://github.com/Kizuchann/kizurium-translator.git
#   cd kizurium-translator
#   makepkg -si
#
# Update:
#   git pull && makepkg -su

pkgname=kizurium-translator
pkgver=1.1.0
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
  'wl-clipboard: read the clipboard for --ocr-copy'
  'aria2: faster --pack-install downloads'
  'tesseract-data-jpn: Japanese OCR'
  'tesseract-data-rus: Russian OCR'
)
# Deliberately absent: rapidocr + onnxruntime, the pair the code prefers for OCR.
# There is no Arch package for either (rapidocr is PyPI-only: 27 MB plus 21 MB of
# onnxruntime), pacman cannot install from pip, and vendoring the wheels into
# this build would cost it its reproducibility. tesseract is in depends, so the
# package reads text; RapidOCR is a quality upgrade available through
# ./install.sh, and README says so where the makepkg path is described.
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
  # Меню приложений: без .desktop и иконки команда есть, а запустить её из
  # списка нельзя. Иконка обязана быть квадратной - assets/translator.png это
  # скриншот 946x504, и в таком виде панель растягивает её в прямоугольник.
  install -Dm644 "$startdir/assets/kizurium-translator.desktop" \
    "$pkgdir/usr/share/applications/$pkgname.desktop"
  install -Dm644 "$startdir/assets/icons/$pkgname.png" \
    "$pkgdir/usr/share/icons/hicolor/256x256/apps/$pkgname.png"
  # No wrapper is installed over pip's console script: scripts/arch-wrapper was
  # an 88-byte copy of exactly what pip writes, and overwriting the file left
  # the wheel's RECORD describing something else. `pacman -Qkk` compares against
  # .MTREE and never noticed; `pip uninstall` would.
}