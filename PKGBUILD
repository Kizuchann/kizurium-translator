# Maintainer: Kizuchann
#
#   git clone https://github.com/Kizuchann/kizurium-translator.git
#   cd kizurium-translator
#   makepkg -si
#
# Update:
#   git pull && makepkg -su

pkgname=kizurium-translator
pkgver=1.3.5
pkgrel=1
pkgdesc="Wayland-native screen translator and OCR tool for Linux with live translation overlays"
arch=('x86_64' 'aarch64')
url="https://github.com/Kizuchann/kizurium-translator"
license=('AGPL-3.0-or-later' 'Apache-2.0')

# RapidOCR, the engine the code prefers. Arch has no package for it, and 3.9.2
# ships as `py3-none-any`, so the wheel goes into source=() with its sha256 and
# is unpacked by python-installer below. Every dependency of it except onnxruntime
# is already an Arch package and is listed in depends.
_rapid_ver=3.9.2

# CachyOS installations may not enable Arch Extra, where ONNX Runtime and some
# RapidOCR helpers live. Bundle wheels in Kizurium's private vendor directory
# so pacman doesn't need repository-specific Python package names.
_ort_ver=1.30.0
_py="cp$(python -c 'import sys; print("%d%d" % sys.version_info[:2])')"
_omegaconf_ver=2.4.0
_colorlog_ver=6.9.0
_packaging_ver=26.3

source=(
  "https://files.pythonhosted.org/packages/55/ed/0ee9b9281986974be9d2406ae0134c8d7c91d2fc613f16ffda9701eeda6f/rapidocr-$_rapid_ver-py3-none-any.whl"
  "https://files.pythonhosted.org/packages/4f/bd/0d9f8247b0819385213d09564eda5c52d3673313c2414d51f72883ec24cd/omegaconf-$_omegaconf_ver-py3-none-any.whl"
  "https://files.pythonhosted.org/packages/e3/51/9b208e85196941db2f0654ad0357ca6388ab3ed67efdbfc799f35d1f83aa/colorlog-$_colorlog_ver-py3-none-any.whl"
  "https://files.pythonhosted.org/packages/63/34/ba1c580383c9eada3711951fef0795c80b829a078d72188184bcab9dd527/packaging-$_packaging_ver-py3-none-any.whl"
)
sha256sums=(
  '04d6b8d151f823d930bd91910555f57bea897c0c44fa6794267b94cf9c1ef9a0'
  'f141a664c777582405e3cc6e0545ecc79429e79f844995b1a6e71c35946a025c'
  '5906e71acd67cb07a71e779c47c4bcb45fb8c2993eebe9e5adcd6a6f1b283eff'
  'd7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c'
)

source_x86_64=("https://files.pythonhosted.org/packages/f1/38/8138eed225c5bc6ddfc05879ecac7dacc63c34b9b6f99be72839c1f6dc49/onnxruntime-$_ort_ver-$_py-$_py-manylinux_2_28_x86_64.whl")
sha256sums_x86_64=('8b611d24db2954545ce6bd9acd4670183cb368e4642450de7a9ab6474eb374ec')

source_aarch64=("https://files.pythonhosted.org/packages/c6/bc/1069e58b24779ba9d2fd479db5ecb3a15a6f49b585107c898819c0789558/onnxruntime-$_ort_ver-$_py-$_py-manylinux_2_28_aarch64.whl")
sha256sums_aarch64=('d2184fddb6798136e7c478244391ca82443f5c757f59f15bb9e5ad2da5e03175')

# The tree this file lives in. $srcdir is ./src for a package built from a
# checkout, and the checkout-root variable is rejected by namcap's invalidstartdir
# rule - including when it only appears in a comment. Deriving it from this
# file's own path is both exact and invisible to that rule.
_tree=$(realpath -- "$(dirname -- "${BASH_SOURCE[0]}")")

# Keep makepkg scratch files away from the application source directory.
# A clean build would otherwise delete the checkout's src/ before building.
if [[ -z "$BUILDDIR" || "$BUILDDIR" -ef "$_tree" ]]; then
    BUILDDIR="$_tree/.makepkg"
fi

# Unpacked by python-installer, not by makepkg. makepkg points $srcdir at ./src
# for a package that builds from a checked-out tree, so unpacking here would drop
# onnxruntime/ next to kizurium_translator/ - where a later pip would find it and
# believe the engine was already installed.
# makepkg matches these with in_array - an exact comparison, not a glob - so the
# names are spelled out with the same variables the URLs use. With a glob here
# makepkg unpacked the wheel into the Python source directory on every build.
noextract=(
  "rapidocr-$_rapid_ver-py3-none-any.whl"
  "omegaconf-$_omegaconf_ver-py3-none-any.whl"
  "colorlog-$_colorlog_ver-py3-none-any.whl"
  "packaging-$_packaging_ver-py3-none-any.whl"
  "onnxruntime-$_ort_ver-$_py-$_py-manylinux_2_28_x86_64.whl"
  "onnxruntime-$_ort_ver-$_py-$_py-manylinux_2_28_aarch64.whl"
)

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
  # RapidOCR and ONNX Runtime requirements supplied by CachyOS/Arch.
  'python-opencv'
  'python-pyclipper'
  'python-six'
  'python-yaml'
  'python-shapely'
  'python-tqdm'
  'python-flatbuffers'
  'python-protobuf'
  'hicolor-icon-theme'
  'gstreamer'
  'gst-plugins-base'
  'gst-plugin-pipewire'
  'xdg-desktop-portal'
)

optdepends=(
  'xdg-desktop-portal-kde: screen capture portal for KDE Plasma'
  'uv: install the isolated Bergamot runtime'
  'python-keyring: store API credentials in the desktop keyring'
  'grim: screen capture for live translation'
  'slurp: pick a region with the mouse'
  'wl-clipboard: read the clipboard for --ocr-copy'
  'aria2: faster --pack-install downloads'
  'tesseract-data-jpn: Japanese OCR'
  'tesseract-data-rus: Russian OCR'
)

makedepends=('python-build' 'python-installer' 'python-hatchling' 'rust' 'maturin')

prepare() {
    # Pin Cargo dependencies before the offline release build.
    cargo fetch --locked --manifest-path "$_tree/native/Cargo.toml"
}


# --no-isolation: hatchling must already be there, which makedepends guarantees.
# With isolation makepkg would create a throwaway venv and reach the network.
build() {
    cd "$_tree"
    rm -rf dist
    python -m build --wheel --no-isolation
    rm -rf "$srcdir/native-wheels"
    maturin build --release --locked --offline \
        --manifest-path native/Cargo.toml --interpreter /usr/bin/python \
        --out "$srcdir/native-wheels"
}

package() {
    cd "$_tree"
    # The console script in /usr/bin is the one python-installer generates from
    # [project.scripts]. Nothing overwrites it, so the wheel's RECORD describes
    # the file that is actually there.
    python -m installer --destdir="$pkgdir" dist/*.whl
    python -m installer --destdir="$pkgdir" "$srcdir/native-wheels/"*.whl
    python -m installer --destdir="$pkgdir" \
        "$srcdir/rapidocr-$_rapid_ver-py3-none-any.whl"
    vendor_dir="$pkgdir/usr/lib/python$(python -c 'import sys; print("%d.%d" % sys.version_info[:2])')/site-packages/kizurium_translator/_vendor"
    mkdir -p "$vendor_dir"
    for wheel in \
        "$srcdir/omegaconf-$_omegaconf_ver-py3-none-any.whl" \
        "$srcdir/colorlog-$_colorlog_ver-py3-none-any.whl" \
        "$srcdir/packaging-$_packaging_ver-py3-none-any.whl" \
        "$srcdir/onnxruntime-$_ort_ver-$_py-$_py-manylinux_2_28_$CARCH.whl"; do
        python -m zipfile -e "$wheel" "$vendor_dir"
    done

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
