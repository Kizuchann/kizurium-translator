// Kizurium Translator — selector shell root.
//
// quickshell -p <this directory> loads this file. It pulls in ScreenshotOverlay,
// which holds the actual selector: frozen frame, dimmed output, draggable and
// resizable region, toolbar and the central round Live button.
//
// Nothing here touches the user's ~/.config/quickshell, their Hyprland config or
// their existing shell. -p starts this directory as its own shell root.

import Quickshell

ShellRoot {
    ScreenshotOverlay {}
}
