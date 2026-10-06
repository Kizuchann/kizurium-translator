# Nix flake for Kizurium Translator (NixOS / nix profile / nix run).
#
# Runtime needs Wayland + grim + gtk4-layer-shell (+ quickshell for region
# select); no particular compositor is required.
#
# Quick start:
#   nix run .#
#   nix run .# -- --doctor
#   nix profile install .#kizurium-translator
{
  description = "Kizurium Translator — Wayland OCR overlay translator";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = import nixpkgs {
            inherit system;
            config.allowUnfree = false;
          };
          python = pkgs.python3;
          kizurium-translator = python.pkgs.buildPythonApplication {
            pname = "kizurium-translator";
            version = "0.1.0";
            pyproject = true;
            src = pkgs.lib.cleanSourceWith {
              src = self;
              filter =
                path: type:
                let
                  base = baseNameOf path;
                in
                !(builtins.elem base [
                  ".git"
                  ".venv"
                  ".local"
                  "result"
                  "__pycache__"
                  ".pytest_cache"
                  ".ruff_cache"
                  ".mypy_cache"
                ]);
            };

            build-system = with python.pkgs; [ hatchling ];

            dependencies = with python.pkgs; [
              pillow
              numpy
              pytesseract
              requests
              pygobject3
              pycairo
              # Same quality path as Arch ./install.sh defaults (RapidOCR).
              rapidocr
              onnxruntime
            ];

            nativeBuildInputs = with pkgs; [
              wrapGAppsHook4
              gobject-introspection
              pkg-config
              makeWrapper
            ];

            buildInputs = with pkgs; [
              gtk4
              gtk4-layer-shell
              libnotify
            ];

            # Do not run pytest in the nix build (needs a Wayland session /
            # fixtures).
            doCheck = false;

            # Make grim/quickshell/tesseract visible even when the user only
            # installed this package into a profile without systemPackages.
            # GI_TYPELIB_PATH for Gtk4LayerShell is handled by wrapGAppsHook4.
            postFixup = ''
              wrapProgram $out/bin/kizurium-translator \
                --prefix PATH : ${
                  pkgs.lib.makeBinPath [
                    pkgs.grim
                    pkgs.slurp
                    pkgs.wl-clipboard
                    pkgs.tesseract
                    pkgs.quickshell
                    pkgs.libnotify
                    pkgs.aria2
                  ]
                } \
                --prefix LD_LIBRARY_PATH : ${pkgs.lib.makeLibraryPath [
                  pkgs.gtk4-layer-shell
                  pkgs.gtk4
                ]}
            '';

            meta = with pkgs.lib; {
              description = "Translate text on screen via OCR and a Wayland layer-shell overlay";
              homepage = "https://github.com/Kizuchann/kizurium-translator";
              license = licenses.agpl3Plus;
              platforms = platforms.linux;
              mainProgram = "kizurium-translator";
            };
          };
        in
        {
          default = kizurium-translator;
          kizurium-translator = kizurium-translator;
        }
      );

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/kizurium-translator";
        };
      });

      # Suggested system packages for NixOS (import in configuration).
      # Not a full NixOS module — keeps the flake simple and reviewable.
      nixosModules.default =
        {
          pkgs,
          lib,
          config,
          ...
        }:
        {
          options.programs.kizurium-translator = {
            enable = lib.mkEnableOption "Kizurium Translator (Wayland OCR overlay)";
            package = lib.mkOption {
              type = lib.types.package;
              default = self.packages.${pkgs.system}.default;
              description = "Package to install";
            };
          };
          config = lib.mkIf config.programs.kizurium-translator.enable {
            environment.systemPackages = [
              config.programs.kizurium-translator.package
              pkgs.grim
              pkgs.slurp
              pkgs.wl-clipboard
              pkgs.tesseract
              pkgs.quickshell
              pkgs.gtk4
              pkgs.gtk4-layer-shell
              pkgs.aria2
            ];
            # eng/rus/jpn tessdata — package set varies by nixpkgs; users may
            # also set TESSDATA_PREFIX. Doctor will report missing langs.
          };
        };

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt-rfc-style);
    };
}
