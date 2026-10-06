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
pkgrel=3
pkgdesc="Wayland-native screen translator and OCR tool for Linux with live translation overlays"
arch=('x86_64' 'aarch64')
url="https://github.com/Kizuchann/kizurium-translator"
license=('AGPL-3.0-only' 'Apache-2.0')

# RapidOCR, the engine the code prefers. Arch has no package for it, and 3.9.2
# ships as `py3-none-any`, so the wheel goes into source=() with its sha256 and
# is unpacked by python-installer below. Every dependency of it except onnxruntime
# is already an Arch package and is listed in depends.
_rapid_ver=3.9.2

# ONNX Runtime: Arch ships the Python module as python-onnxruntime-cpu, but only
# for x86_64 - there is no aarch64 build, so aarch64 carries the wheel. x86_64
# users get it from pacman and never download these 21 MB.
_ort_ver=1.30.0
_py="cp$(python -c 'import sys; print("%d%d" % sys.version_info[:2])')"

source=(
  "https://files.pythonhosted.org/packages/55/ed/0ee9b9281986974be9d2406ae0134c8d7c91d2fc613f16ffda9701eeda6f/rapidocr-$_rapid_ver-py3-none-any.whl"
)
sha256sums=('04d6b8d151f823d930bd91910555f57bea897c0c44fa6794267b94cf9c1ef9a0')

source_aarch64=("https://files.pythonhosted.org/packages/c6/bc/1069e58b24779ba9d2fd479db5ecb3a15a6f49b585107c898819c0789558/onnxruntime-$_ort_ver-$_py-$_py-manylinux_2_28_aarch64.whl")
sha256sums_aarch64=('d2184fddb6798136e7c478244391ca82443f5c757f59f15bb9e5ad2da5e03175')

# The tree this file lives in. $srcdir is ./src for a package built from a
# checkout, and the checkout-root variable is rejected by namcap's invalidstartdir
# rule - including when it only appears in a comment. Deriving it from this
# file's own path is both exact and invisible to that rule.
_tree=$(dirname -- "${BASH_SOURCE[0]}")

# Where makepkg puts what it downloads. The default is the source directory,
# which here is the Python package itself: the wheel would then sit next to
# kizurium_translator/ and get collected by pytest. .local/ is gitignored and
# nothing scans it.
SRCDEST="$_tree/.local/pkgsrc"

# Unpacked by python-installer, not by makepkg. makepkg points $srcdir at ./src
# for a package that builds from a checked-out tree, so unpacking here would drop
# onnxruntime/ next to kizurium_translator/ - where a later pip would find it and
# believe the engine was already installed.
# makepkg matches these with in_array - an exact comparison, not a glob - so the
# names are spelled out with the same variables the URLs use. With a glob here
# makepkg unpacked the wheel into the Python source directory on every build.
noextract=("rapidocr-$_rapid_ver-py3-none-any.whl" "onnxruntime-$_ort_ver-$_py-$_py-manylinux_2_28_aarch64.whl")

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
  # rapidocr's own requirements, minus onnxruntime below
  'python-opencv'
  'python-pyclipper'
  'python-six'
  'python-yaml'
  'python-shapely'
  'python-tqdm'
  'python-omegaconf'
  'python-colorlog'
  'hicolor-icon-theme'
)

# x86_64 only: extra/x86_64/python-onnxruntime-cpu carries
# site-packages/onnxruntime/__init__.py and libonnxruntime.so, so the wheel is
# redundant there. extra/aarch64 has no such package.
depends_x86_64=('python-onnxruntime-cpu')

optdepends=(
  'grim: screen capture for live translation'
  'slurp: pick a region with the mouse'
  'quickshell: region picker'
  'wl-clipboard: read the clipboard for --ocr-copy'
  'aria2: faster --pack-install downloads'
  'tesseract-data-jpn: Japanese OCR'
  'tesseract-data-rus: Russian OCR'
)

makedepends=('python-build' 'python-installer' 'python-hatchling')


# --no-isolation: hatchling must already be there, which makedepends guarantees.
# With isolation makepkg would create a throwaway venv and reach the network.
build() {
    cd "$_tree"
    rm -rf dist
    python -m build --wheel --no-isolation
}

package() {
    cd "$_tree"
    # The console script in /usr/bin is the one python-installer generates from
    # [project.scripts]. Nothing overwrites it, so the wheel's RECORD describes
    # the file that is actually there.
    python -m installer --destdir="$pkgdir" dist/*.whl
    python -m installer --destdir="$pkgdir" \
        "$SRCDEST/rapidocr-$_rapid_ver-py3-none-any.whl"
    if [[ "$CARCH" == aarch64 ]]; then
        python -m installer --destdir="$pkgdir" \
            "$SRCDEST/onnxruntime-$_ort_ver-$_py-$_py-manylinux_2_28_aarch64.whl"
    fi

    install -Dm644 "LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
    install -Dm644 "LICENSE.header" "$pkgdir/usr/share/licenses/$pkgname/LICENSE.header"
    install -Dm644 "NOTICE" "$pkgdir/usr/share/licenses/$pkgname/NOTICE"
    install -Dm644 "config.example.toml" "$pkgdir/usr/share/$pkgname/config.example.toml"

    # RapidOCR and the PP-OCRv6 models that ship inside its wheel. The wheel
    # carries no LICENSE file, only `License-Expression: Apache-2.0` in its
    # METADATA, so the notice is taken from there rather than invented.
    mkdir -p "$pkgdir/usr/share/licenses/$pkgname/rapidocr"
    printf '%s\n' \
        'RapidOCR 3.9.2 is redistributed inside this package.' \
        'License-Expression: Apache-2.0 (from rapidocr-3.9.2.dist-info/METADATA)' \
        '' \
        'Bundled models, all Apache-2.0:' \
        '  rapidocr/models/PP-OCRv6_det_small.onnx' \
        '  rapidocr/models/PP-OCRv6_rec_small.onnx' \
        '  rapidocr/models/ch_ppocr_mobile_v2.0_cls_mobile.onnx' \
        > "$pkgdir/usr/share/licenses/$pkgname/rapidocr/NOTICE"

    # Without a .desktop and an icon the binary installs but has no menu entry.
    # assets/translator.png is a 946x504 screenshot and cannot serve as one.
    install -Dm644 "assets/kizurium-translator.desktop" \
        "$pkgdir/usr/share/applications/$pkgname.desktop"
    install -Dm644 "assets/icons/$pkgname.png" \
        "$pkgdir/usr/share/icons/hicolor/256x256/apps/$pkgname.png"
}
