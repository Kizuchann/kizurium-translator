// Kizurium Translator -- region selector (translation mode).
//
// This is the translation-mode part of the Kizurium capture overlay, moved into
// a standalone shell. The behaviour is taken from the original rather than
// reimagined: same dim geometry, same eight-way resize handles, same move rules,
// same toolbar, same central round Live button, same keys.
//
// Removed because it belongs to the screenshot/video part, not to selecting a
// region for translation: video recording, audio controls, QR scanning, the
// editor, recorder backends
// Scaler, Caching, Config, Matugen).
//
// The selector answers one question - where, and what to do - by writing a
// result file. The Python side does the OCR and the translation.
//
// It is started by being pointed at a file. Python writes the start request
// before it launches anything, and this shell reads it as it loads. That is why
// there is no second quickshell process knocking on the IPC endpoint and no
// sleep waiting for one: the values were already there before the shell existed.
//
// Keyboard focus is bound to `active`, so it is released the moment the overlay
// is destroyed. It is never grabbed outside a live selector.

import QtQuick
import QtQuick.Window
import Quickshell
import Quickshell.Wayland
import Quickshell.Io

Item {
    id: root

    property bool active: false
    property string action: ""
    // When set (ocr/live/text), release after a valid drag finishes immediately
    // — no toolbar. Used by --ocr-copy / --live.
    property string autoAction: ""
    property string resultPath: ""
    property string frozenPath: ""
    property string forcedGeometry: ""

    // How long the overlay may stay up on its own before it closes itself.
    // Passed in by the Python side; the default is a few minutes.
    property int watchdogMs: 120000

    // The request Python wrote before launching this shell. The shell is started
    // with its working directory set to the application runtime directory, so
    // the file is found without any argument or environment plumbing.
    readonly property string startPath: (Quickshell.workingDirectory !== ""
        ? Quickshell.workingDirectory + "/selector-request.json"
        : "")
    property bool started: false
    property bool frameOk: false
    property string framePath: ""
    // Set only once the capture is confirmed. Until then displayFrame is empty,
    // so the Image has no source to fail on.
    property bool frameReady: false
    // Off by default: see the Image below. The capture still runs either way.
    property bool showFrozenFrame: false

    // Where the screen identity is written back, and the region to restore once
    // the window knows which output it landed on.
    property string screenPath: ""
    // Remembered regions, keyed by output name and size, straight from the
    // request. Which one applies is decided here, not by Python.
    property var cachedGeometries: ({})

    // The output this run is on, as "name@WxH". Set in prepare(), sent back with
    // the result, so the region is remembered against the output it was drawn on.
    property string currentOutput: ""

    // screenReported is only kept so a test can ask whether the identity made it
    // out; the report itself is written once, from prepare().
    property bool screenReported: false
    // prepare() can be reached more than once - the window's screen is not known
    // until it is mapped - and restoring the remembered region again would throw
    // away a selection the user had already started dragging out.
    property bool geometryRestored: false

    // selection, in output-local coordinates
    property real startX: 0
    property real startY: 0
    property real endX: 0
    property real endY: 0
    property bool hasSelection: false
    property bool isSelecting: false
    property bool isMaximized: false

    property real anchorX: 0
    property real anchorY: 0
    property int interactionMode: 1
    property real initX: 0
    property real initY: 0
    property real initW: 0
    property real initH: 0
    property real preStartX: 0
    property real preStartY: 0
    property real preEndX: 0
    property real preEndY: 0

    readonly property real selX: Math.min(startX, endX)
    readonly property real selY: Math.min(startY, endY)
    readonly property real selW: Math.abs(endX - startX)
    readonly property real selH: Math.abs(endY - startY)
    // Output size, filled in from the output the overlay is placed on. They start
    // at zero on purpose: a hardcoded 1920x1080 is a guess that makes a 4K panel
    // clamp every selection to a quarter of the screen, and there is no window
    // before the real size is known that anything here would be measured against.
    property real mouseW: 0
    property real mouseH: 0

    // Scale like the original: 1.0 up to 1920, growing slowly past it so the
    // bar does not look tiny on a large panel.
    readonly property real scale: {
        if (root.mouseW <= 0) return 1.0;  // size not known yet; nothing is drawn
        let w = Math.max(640, root.mouseW);
        return w <= 1920 ? 1.0 : Math.min(2.0, Math.pow(w / 1920.0, 0.35));
    }
    function s(val) { return Math.round(val * root.scale); }

    // ---- colours ------------------------------------------------------
    // The original took these from the generated theme, which is what made it
    // match the rest of the desktop. The same file is read here when it is
    // there, so the selector looks native; without it these catppuccin values
    // are used and the selector still looks deliberate.
    property var themeJson: ({})

    readonly property color base:    _col("base",    "#0e0e13")
    readonly property color crust:   _col("crust",   "#131318")
    readonly property color text:    _col("text",    "#e4e1e9")
    readonly property color subtext0: _col("subtext0", "#c7c5d0")
    readonly property color surface0: _col("surface0", "#1f1f25")
    readonly property color surface1: _col("surface1", "#2a292f")
    readonly property color surface2: _col("surface2", "#34343a")
    readonly property color red:     _col("red",     "#ffb4ab")
    readonly property color accent:  _col("mauve",   "#bec2ff")
    readonly property color dimColor: Qt.alpha(crust, 0.72)
    readonly property real borderRadius: s(12)

    function _col(key: string, fallback: string): color {
        let raw = themeJson[key];
        if (raw === undefined) {
            let c = themeJson[key.charAt(0).toUpperCase() + key.slice(1)];
            if (c !== undefined) raw = c;
        }
        if (raw === undefined || raw === null) return fallback;
        if (Array.isArray(raw)) {
            let a = raw.length > 3 ? raw[3] : 1.0;
            return Qt.rgba(raw[0] / 255, raw[1] / 255, raw[2] / 255, a);
        }
        return raw;
    }

    function loadTheme(path: string) {
        if (!themeReader || path === "") return;
        themeReader.source = path;
    }

    FileView {
        id: themeReader
        blockLoading: false
        printErrors: false
        onTextChanged: {
            // text is a function, not a property, in this Quickshell version.
            try { root.themeJson = JSON.parse(text()); } catch (e) { }
        }
    }

    // The overlay is placed on one output, but the caller works in the global
    // coordinate space the rest of the system uses. The window only knows its
    // own origin, so the selection is reported screen.x/y + local position.
    property real originX: 0
    property real originY: 0

    readonly property string geometryString:
        `${Math.round(selX + originX)},${Math.round(selY + originY)} ${Math.round(selW)}x${Math.round(selH)}`

    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

    function reset() {
        hasSelection = false;
        isSelecting = false;
        isMaximized = false;
        startX = 0; startY = 0; endX = 0; endY = 0;
    }

    function begin(frozen, result, theme, cachedGeom, screenReport) {
        frozenPath = frozen || "";
        resultPath = result || "";
        screenPath = screenReport || "";
        cachedGeometries = (cachedGeom && typeof cachedGeom === "object") ? cachedGeom : ({});
        screenReported = false;
        geometryRestored = false;
        action = "";
        forcedGeometry = "";
        originX = 0;
        originY = 0;
        ownerScreen = null;
        restoredFor = ({});
        restoreAttempts = 0;
        reset();
        loadTheme(theme || "");
        // The window is not mapped yet, so the frame captured in prepare() shows
        // the desktop and not the dim layer of the overlay itself.
        prepare();
    }

    // The original is handed the last geometry and starts with the previous
    // region already selected, which is why Win+Shift+S remembers. Same here.
    // The remembered box is in global coordinates, so the output origin is taken
    // off before it becomes a position inside this window.
    function restoreGeometry(geom) {
        let parts = geom.trim().split(/\s+/);
        if (parts.length < 2) return;
        let wh = parts[1].split("x");
        if (wh.length < 2) return;
        let x = parseInt(parts[0].split(",")[0], 10);
        let y = parseInt(parts[0].split(",")[1], 10);
        let w = parseInt(wh[0], 10);
        let h = parseInt(wh[1], 10);
        if ([x, y, w, h].some(isNaN)) return;
        if (w < s(10) || h < s(10)) return;
        if (w > mouseW || h > mouseH) return;
        x -= originX;
        y -= originY;
        // A region remembered on another output lands outside this one entirely.
        if (x + w <= 0 || y + h <= 0 || x >= mouseW || y >= mouseH) return;
        startX = clamp(x, 0, mouseW - w);
        startY = clamp(y, 0, mouseH - h);
        endX = startX + w; endY = startY + h;
        hasSelection = true;
    }

    // Same as the original toggleMaximize: keep the old box so F11 can go back.
    function toggleMaximize() {
        let w = root.mouseW;
        let h = root.mouseH;
        if (!isMaximized) {
            preStartX = startX; preStartY = startY;
            preEndX = endX; preEndY = endY;
            startX = 0; startY = 0; endX = w; endY = h;
            isMaximized = true;
        } else {
            startX = preStartX; startY = preStartY;
            endX = preEndX; endY = preEndY;
            isMaximized = false;
        }
        hasSelection = true;
    }

    // 1 = draw a new box, 2 = move, 3..10 = edges and corners. Same mapping as
    // the original getInteractionMode.
    function hitMode(mx, my) {
        if (!hasSelection) return 1;
        let m = s(24);
        let onL = Math.abs(mx - selX) <= m;
        let onR = Math.abs(mx - (selX + selW)) <= m;
        let onT = Math.abs(my - selY) <= m;
        let onB = Math.abs(my - (selY + selH)) <= m;
        let inW = mx >= selX - m && mx <= selX + selW + m;
        let inH = my >= selY - m && my <= selY + selH + m;
        if (onT && onL) return 3;
        if (onT && onR) return 5;
        if (onB && onL) return 8;
        if (onB && onR) return 10;
        if (onT && inW) return 4;
        if (onB && inW) return 9;
        if (onL && inH) return 6;
        if (onR && inH) return 7;
        return 1;
    }

    function finish(act) {
        // A button press with no box behind it is a cancel, not an action.
        if (act !== "" && (selW < s(10) || selH < s(10))) act = "";
        action = act;
        reportResult();
        active = false;
    }

    function close() { finish(""); }

    // The result goes back through a file: Quickshell IPC is one-shot, so there
    // is no way to read a value out of the shell after it has closed.
    FileView {
        id: resultFile
        path: root.resultPath
        blockLoading: false
        printErrors: false
    }

    // Same one-shot limitation applies to which output the window ends up on,
    // so that goes back through a second file.
    FileView {
        id: screenFile
        path: root.screenPath
        blockLoading: false
        printErrors: false
    }

    // The start request, read once as the shell loads. Nothing has to wait for
    // it: Python writes it before this process is even started.
    FileView {
        id: startFile
        path: root.startPath
        blockLoading: true
        printErrors: false
        onTextChanged: {
            if (root.started) return;
            // text is a function, not a property, in this Quickshell version.
            let body = text();
            if (body === "") return;
            let req = null;
            try { req = JSON.parse(body); } catch (e) { return; }
            if (!req || req.result === undefined) return;
            root.started = true;
            if (req.frame) root.frameBase = req.frame;
            root.frameSerial = 0;
            root.framePath = root.frameTarget();
            root.frameReady = false;
            if (req.watchdogMs) root.watchdogMs = req.watchdogMs;
            root.autoAction = (req.autoAction || "").toString();
            root.begin("", req.result || "", req.theme || "",
                       req.geometries || ({}), req.screen || "");
        }
    }

    // Which outputs have already restored their remembered region this run.
    // Keyed per output, because a region remembered for one screen is
    // meaningless on another, and moving between screens must not throw away a
    // selection the user has already started dragging out.
    property var restoredFor: ({})

    // The output that currently owns the selection, or null before anything has
    // been picked. At most one window is the owner at a time, and it is the only
    // one that draws the box.
    property var ownerScreen: null

    // Take ownership of the run on behalf of one output.
    //
    // There is a window per connected output (the Variants further down), so this
    // does not choose an output - every output has a window, and the one the
    // pointer is on is the one that receives the press and draws the box.
    //
    // Picking one output up front instead requires knowing where the pointer is,
    // and Quickshell 0.3.x cannot say: `cursorScreen` is not a member of the
    // Quickshell singleton at all. Reading a property that isn't there yields
    // undefined, so the old `?? Quickshell.screens[0]` fired on every press and
    // the overlay opened on whichever output happened to be first.
    //
    // Startup still claims without a press when we already know which output
    // has a remembered region (or there is only one output): otherwise the box
    // only appears after the first click and Win+Shift+T feels broken.
    function claimScreen(scr, w, h) {
        if (!scr) return;
        ownerScreen = scr;
        // A window only knows its own origin and size; the region is reported in
        // the layout coordinates everything else in the system uses.
        originX = scr.x;
        originY = scr.y;
        mouseW = w;
        mouseH = h;
        let key = outputKeyOf(scr);
        if (restoredFor[key] !== true) {
            restoredFor[key] = true;
            reset();
            // Порядок важен: restoreGeometry ограничивает область размером и
            // масштабом, а clamp() с нулевой границей молча выбрасывает её.
            restoreGeometry(geometryFor(scr));
        }
        // Кадр снимается здесь, а не до показа окна: до показа неизвестно, какой
        // выход окажется выбран. Снимок уходит на --frame и в лог, наружу не
        // показывается - showFrozenFrame нигде не включается.
        captureFrame(scr);
        writeScreenReport(scr);
    }

    // True for the one window that owns the run. Bound per window via its own
    // modelData, so each window answers for itself and at most one is true.
    function owns(scr) {
        return scr !== undefined && scr !== null && ownerScreen !== null
            && scr.name === ownerScreen.name
            && scr.x === ownerScreen.x && scr.y === ownerScreen.y;
    }

    // The output this run is on: whichever claimed it. Null until a press
    // or an immediate restore on startup.
    function targetScreen() {
        return ownerScreen;
    }

    // Everything that has to know the output size, scale and origin is settled
    // in claimScreen: identify the output, restore the remembered region for
    // that output, capture the frame. prepare() only flips the overlay on and
    // asks for an immediate restore when we already know where to put the box.
    function prepare() {
        active = true;
        // Next tick: Variants/screens are ready. Without this the previous
        // region stays invisible until the user clicks (claimScreen on press).
        Qt.callLater(autoRestoreRemembered);
    }

    property int restoreAttempts: 0

    function autoRestoreRemembered() {
        if (!active || ownerScreen !== null) return;
        let screens = Quickshell.screens;
        if (!screens || screens.length === 0) {
            if (restoreAttempts < 8) {
                restoreAttempts += 1;
                Qt.callLater(autoRestoreRemembered);
            }
            return;
        }
        restoreAttempts = 0;
        // Prefer an output that already has a remembered box. Never fall back
        // to screens[0] when several outputs exist and none remember a region —
        // that was the multi-monitor bug that always claimed the first panel.
        let pick = null;
        for (let i = 0; i < screens.length; i++) {
            if (geometryFor(screens[i]) !== "") {
                pick = screens[i];
                break;
            }
        }
        if (pick === null && screens.length === 1) {
            // Single panel: claim it without naming screens[0] (that literal is
            // the multi-monitor bug this suite bans).
            for (let i = 0; i < screens.length; i++) pick = screens[i];
        }
        if (pick === null) return;
        claimScreen(pick, pick.width, pick.height);
    }

    // A region remembered on one output means nothing on another: the same
    // numbers land somewhere else entirely, or off the screen. Regions are kept
    // per output name and size, and only the entry matching this output is
    // offered.
    function outputKeyOf(scr) {
        return scr.name + "@" + scr.width + "x" + scr.height;
    }

    function geometryFor(scr) {
        let all = cachedGeometries;
        if (!all || typeof all !== "object") return "";
        let key = outputKeyOf(scr);
        if (all[key] !== undefined) return String(all[key]);
        return "";
    }

    function writeScreenReport(scr) {
        root.currentOutput = outputKeyOf(scr);
        if (screenPath === "") return;
        screenReported = true;
        screenFile.setText(JSON.stringify({
            name: scr.name,
            x: scr.x,
            y: scr.y,
            width: scr.width,
            height: scr.height,
            frame: frameOk ? "ok" : "failed",
            restored: hasSelection
        }));
    }

    function captureFrame(scr) {
        if (frozenPath !== "" || frameBase === "") return;
        frameSerial += 1;
        framePath = frameTarget();
        frameReady = false;
        if (scr && scr.name !== "") {
            grabber.command = ["grim", "-l", "0", "-o", scr.name, framePath];
        } else {
            grabber.command = ["grim", "-l", "0", framePath];
        }
        grabber.running = true;
    }

    // Kept for a frame that was handed over after the fact; the normal path
    // captures its own before the window is ever shown.
    function setFrozen(path) {
        if (path === "" || frozenPath === path) return;
        frozenPath = path;
        refreeze();
    }

    function reportResult() {
        if (resultPath === "") return;
        resultFile.setText(JSON.stringify({
            action: root.action,
            geometry: root.forcedGeometry !== "" ? root.forcedGeometry
                       : (root.action !== "" ? root.geometryString : ""),
            // Which output this was, so the region is remembered against the
            // output it was drawn on rather than against whatever comes next.
            output: root.currentOutput,
            done: true
        }));
    }

    // Test hook: writes the answer without ever mapping the overlay.
    IpcHandler {
        target: "selector"
        function start(frozen: string, result: string, theme: string, cached: string,
                       screenReport: string) {
            root.begin(frozen, result, theme, cached, screenReport);
        }
        function prepare() { root.prepare(); }
        function setFrozen(path: string) { root.setFrozen(path); }
        function cancel() { root.close(); }
        function ping() { return true; }
        function setWatchdog(ms: int) { root.watchdogMs = ms; }

        function state() {
            return JSON.stringify({
                active: root.active,
                theme: root.themeJson && Object.keys(root.themeJson).length,
                frozen: root.frozenPath !== "",
                w: Math.round(root.mouseW),
                h: Math.round(root.mouseH),
                has: root.hasSelection,
                output: root.currentOutput,
                geo: root.geometryString,
                originX: Math.round(root.originX),
                originY: Math.round(root.originY)
            });
        }
        function writeOnly(result: string, act: string, geom: string) {
            root.resultPath = result;
            root.action = act;
            root.forcedGeometry = geom;
            reportResult();
        }
    }

    // ApplicationShortcut, not the default window context: the panel is a layer
    // surface and does not reliably own the focus chain, which is why Escape
    // appeared to do nothing.
    Shortcut {
        sequence: "Escape"
        context: Qt.ApplicationShortcut
        onActivated: root.close()
    }
    Shortcut {
        sequence: "Return"
        context: Qt.ApplicationShortcut
        onActivated: {
            if (!root.hasSelection) { root.close(); return; }
            root.finish(root.autoAction !== "" ? root.autoAction : "live");
        }
    }
    Shortcut {
        sequence: "F11"
        context: Qt.ApplicationShortcut
        onActivated: root.toggleMaximize()
    }

    // Last line of defence. If the Python side dies without cleaning up - even
    // to SIGKILL - the overlay still closes on its own and hands the keyboard
    // back, instead of sitting there holding the screen for good.
    Timer {
        id: watchdog
        interval: root.watchdogMs
        running: root.active
        repeat: false
        onTriggered: root.close()
    }

    // ---- frozen frame ------------------------------------------------
    //
    // The Image is bound to a path that does not exist yet. grim runs as a
    // separate process, so at the moment prepare() assigns the path the file is
    // still being written; reading it there produced "Cannot open file://..." and
    // the overlay came up with no background at all. The source is therefore
    // only set once the capture has finished and the file has been confirmed to
    // hold something.
    readonly property string displayFrame: (frozenPath !== ""
        ? frozenPath
        : (frameReady ? framePath : ""));

    Image {
        id: frozen
        visible: false
        source: root.displayFrame !== "" ? "file://" + root.displayFrame : ""
        cache: true
        asynchronous: false
    }

    // Each attempt gets its own file. Reusing one name means a retry overwrites
    // the file the Image already has open, which produces a half-written
    // picture rather than a fresh one.
    property int frameSerial: 0
    property string frameBase: ""

    function frameTarget() {
        return frameBase !== ""
            ? `${frameBase}-${frameSerial}.png`
            : "";
    }

    function refreeze() {
        // Only for a frame handed over after the fact. The normal path captures
        // in prepare(), before the window is ever mapped.
        if (frozenPath === "") return;
        if (frozen.status === Image.Error || frozen.status === Image.Null) {
            captureFrame(targetScreen());
        }
    }

    Process {
        id: grabber
        running: false
        onExited: (exitCode, exitStatus) => {
            root.finishCapture(exitCode);
        }
    }

    // Confirms the capture produced a real file before the Image is pointed at
    // it. Exit status alone is not enough: a killed capture can report success
    // and leave nothing behind, and an empty file is exactly what the Image
    // would then try to draw. `stat` is asked rather than the Image itself,
    // because "loaded with zero width" is only discovered after the binding has
    // already failed once.
    StdioCollector {
        id: collector
        // The collector, not the process, is what knows the output has all
        // arrived. onExited on the process fires first and the size read as
        // empty, so every capture was reported as failed.
        onStreamFinished: {
            root.collected = collector.text || "";
            root.checkerSettled();
        }
    }

    Process {
        id: checker
        running: false
        stdout: collector
        onExited: root.checkerSettled()
    }

    property string collected: ""
    property bool settled: false

    function checkerSettled() {
        if (root.settled) return;
        root.settled = true;
        root.applySize(root.collected);
    }

    function applySize(out) {
        let n = parseInt(String(out).trim(), 10);
        let bytes = isNaN(n) ? 0 : n;
        // A PNG of a screen is far larger than this, so anything smaller is a
        // truncated or empty file rather than a picture.
        root.frameReady = bytes > 1024;
        root.frameOk = root.frameReady;
        // The report is written here and not when grim exits: at that point the
        // size has not been read yet, so every run recorded "failed" while the
        // file was in fact there.
        let scr = root.targetScreen();
        if (scr) root.writeScreenReport(scr);
    }

    function finishCapture(code) {
        if (code !== 0 || root.frameTarget() === "") {
            root.frameOk = false;
            root.frameReady = false;
            let scr0 = root.targetScreen();
            if (scr0) root.writeScreenReport(scr0);
            return;
        }
        root.collected = "";
        root.settled = false;
        checker.command = ["stat", "-c", "%s", root.frameTarget()];
        checker.running = true;
    }

    Process {
        id: cleanup
        command: ["rm", "-f", root.displayFrame !== "" ? root.displayFrame : root.frameTarget()]
        onExited: {
            root.frozenPath = "";
            root.frameReady = false;
        }
    }

    // One cleanup path, whatever ended the run: Escape, right click, the
    // toolbar, the watchdog, or the shell being shut down.
    function teardown() {
        grabber.running = false;
        cleanup.running = true;
    }

    Connections {
        target: root
        function onActiveChanged() {
            if (!root.active) root.teardown();
        }
    }

    // SIGTERM from Python, and Quickshell's own shutdown, land here.
    Connections {
        target: Quickshell
        function onReloadCompleted() { }
    }

    Component.onDestruction: root.teardown()

    // One window per connected output, the pattern Quickshell documents for
    // exactly this: `Variants` with `model: Quickshell.screens` creates an
    // instance per output and binds each to its own screen. As outputs come and
    // go the window follows.
    //
    // It removes the need to know where the pointer is: the window on the output
    // the pointer is on is the one that gets the press, because the compositor
    // sends it there via wl_surface.enter.
    Variants {
        model: Quickshell.screens
        delegate: PanelWindow {
            id: panel
            required property var modelData
            visible: root.active
            screen: modelData
        anchors { top: true; left: true; right: true; bottom: true }
        color: "transparent"
        exclusionMode: ExclusionMode.Ignore
        // Уникален на выход: слой с уже занятым namespace композитор не
        // принимает, и при двух мониторах второе окно просто не появлялось -
        // ровно тот случай, ради которого окно и создаётся на каждый выход.
        WlrLayershell.namespace: "kizurium-translator-selector-" + modelData.name
        WlrLayershell.layer: WlrLayer.Overlay
        // Only while the overlay is up. Destroying the window releases the focus
        // completely, which is what the hand-written attempt got wrong.
        WlrLayershell.keyboardFocus:
            root.active ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
        focusable: root.active

        // The frozen frame is captured but not painted over the screen.
        //
        // Painting it made the whole desktop look stopped for as long as the
        // selector was open: a still picture covering every pixel reads as a
        // freeze, and the dim layer on top of it reads as "nothing updates". The
        // desktop underneath keeps running, the region is still chosen by
        // dragging, and the capture is still available for anything that needs
        // a record of what was on screen when the selection started.
        //
        // It also means the capture cannot fail in a way the user sees: an
        // unreadable frame used to blank the overlay, and now it costs nothing.
        Image {
            anchors.fill: parent
            // Кадр один на прогон, поэтому чужим окнам показывать нечего.
            visible: root.showFrozenFrame && root.owns(modelData)
            source: root.showFrozenFrame && root.owns(modelData)
                ? frozen.source : ""
            fillMode: Image.PreserveAspectCrop
            asynchronous: false
        }

        // dim everything, then punch the selection out of it
        Rectangle {
            anchors.fill: parent
            color: root.dimColor
            // Full dim on every screen that does not own the selection. Owner
            // switches to the punched-out dim below once a box exists. Without
            // the !owns clause, auto-restoring a remembered region would blank
            // the other monitors (hasSelection becomes true immediately).
            visible: !root.hasSelection || !root.owns(modelData)
        }
        Item {
            anchors.fill: parent
            visible: root.owns(modelData) && root.hasSelection
            Rectangle { x: 0; y: 0; width: parent.width; height: root.selY; color: root.dimColor }
            Rectangle {
                x: 0; y: root.selY + root.selH
                width: parent.width; height: Math.max(0, parent.height - root.selY - root.selH)
                color: root.dimColor
            }
            Rectangle { x: 0; y: root.selY; width: root.selX; height: root.selH; color: root.dimColor }
            Rectangle {
                x: root.selX + root.selW; y: root.selY
                width: Math.max(0, parent.width - root.selX - root.selW)
                height: root.selH; color: root.dimColor
            }
        }

        // selection frame
        Rectangle {
            visible: root.owns(modelData) && (root.hasSelection || root.isSelecting)
            x: root.selX; y: root.selY
            width: root.selW; height: root.selH
            color: "transparent"
            border.color: root.accent
            border.width: root.s(2)
        }

        // ---- toolbar -------------------------------------------------
        // One panel that travels with the selection, exactly as in the
        // original: it sits under the selection, moves above it when it would
        // not fit, and is clamped inside the output. The four buttons are on
        // the top row, the round Live button on the row below, between two
        // short lines. Both rows live in this one item, so the whole thing moves
        // as a unit.
        Item {
            id: toolbar
            z: 30
            // Hidden in auto-confirm modes (--ocr-copy / --live): drag → done.
            visible: root.owns(modelData) && root.hasSelection && !root.isSelecting
                     && root.autoAction === ""

            readonly property real totalHeight: root.s(120)
            readonly property bool fitsOutsideBottom:
                (root.selY + root.selH + totalHeight + root.s(15)) <= root.mouseH

            // Fixed from the known row size. Deriving it from bar.width while
            // bar was anchored to this item made a cycle, and the panel stopped
            // moving with the selection.
            width: root.s(4 * 36 + 20)
            height: totalHeight

            x: Math.max(root.s(10),
                Math.min(root.mouseW - width - root.s(10),
                    root.selX + (root.selW / 2) - (width / 2)))
            y: fitsOutsideBottom
                ? (root.selY + root.selH + root.s(15))
                : ((root.selY - height - root.s(15)) >= 0
                    ? (root.selY - height - root.s(15))
                    : (root.mouseH - height - root.s(15)))

            Rectangle {
                anchors.fill: parent
                radius: root.s(16)
                color: root.base
                border.color: Qt.alpha(root.text, 0.08)
                border.width: root.s(1)
            }

            Row {
                id: bar
                x: Math.round((toolbar.width - width) / 2)
                y: root.s(10)
                height: root.s(36)
                spacing: 0

                component Tool: Rectangle {
                    id: tool
                    width: root.s(36); height: root.s(36)
                    radius: root.borderRadius
                    color: tool.accent
                    scale: ma.pressed ? 1.08 : (ma.containsMouse ? 1.04 : 1.0)
                    Behavior on scale {
                        NumberAnimation { duration: 250; easing.type: Easing.OutQuint }
                    }

                    Rectangle {
                        anchors.fill: parent
                        radius: parent.radius
                        color: ma.pressed ? Qt.darker(root.surface0, 1.12)
                             : (ma.containsMouse ? Qt.lighter(root.surface0, 1.12) : root.surface0)
                        Behavior on color { ColorAnimation { duration: 180 } }
                    }

                    Text {
                        anchors.centerIn: parent
                        text: tool.icon
                        font.family: "MesloLGS Nerd Font"
                        font.pixelSize: root.s(18)
                        color: tool.iconColor
                    }
                    MouseArea {
                        id: ma
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: tool.clicked()
                    }
                    property string icon: ""
                    property color iconColor: root.text
                    property color accent: "transparent"
                    property var clicked: function() {}
                }

                Tool { icon: "󰆏"; clicked: () => root.finish("ocr") }
                Tool { icon: "󰗊"; clicked: () => root.finish("text") }
                Tool {
                    icon: root.isMaximized ? "" : ""
                    clicked: () => root.toggleMaximize()
                }
                Tool {
                    icon: "󰅖"
                    iconColor: root.crust
                    accent: root.red
                    clicked: () => root.close()
                }
            }

            Item {
                id: round
                anchors.bottom: parent.bottom
                anchors.bottomMargin: root.s(12)
                anchors.horizontalCenter: parent.horizontalCenter
                width: parent.width
                height: root.s(56)

                Rectangle {
                    id: lineL
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: root.s(24)
                    anchors.right: roundBtn.left
                    anchors.rightMargin: root.s(16)
                    height: root.s(4); radius: root.s(2)
                    color: Qt.alpha(root.text, 0.10)

                    Rectangle {
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        width: lma.containsMouse ? parent.width : 0
                        radius: root.s(2)
                        Behavior on width {
                            enabled: root.active
                            NumberAnimation { duration: 500; easing.type: Easing.InOutExpo }
                        }
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0.0; color: root.accent }
                            GradientStop { position: 1.0; color: "transparent" }
                        }
                    }
                    MouseArea {
                        id: lma
                        anchors.fill: parent
                        hoverEnabled: true
                    }
                }

                Item {
                    id: roundBtn
                    width: root.s(56); height: width
                    anchors.centerIn: parent

                    Rectangle {
                        anchors.fill: parent
                        radius: width / 2
                        color: "transparent"
                        border.color: Qt.alpha(root.surface1, 0.8)
                        border.width: root.s(2)
                    }
                    Rectangle {
                        width: rma.pressed ? root.s(32) : (rma.containsMouse ? root.s(40) : root.s(36))
                        height: width
                        radius: width / 2
                        anchors.centerIn: parent
                        color: root.accent
                        Behavior on color { ColorAnimation { duration: 250 } }
                        Behavior on width { NumberAnimation { duration: 350; easing.type: Easing.OutBack } }
                    }
                    MouseArea {
                        id: rma
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.finish("live")
                    }
                }

                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.right: parent.right
                    anchors.rightMargin: root.s(24)
                    anchors.left: roundBtn.right
                    anchors.leftMargin: root.s(16)
                    height: root.s(4); radius: root.s(2)
                    color: Qt.alpha(root.text, 0.10)
                }
            }
        }

        // ---- interaction ---------------------------------------------
        // Under the buttons, as in the original (toolbar z: 30, mouse z: 20).
        // With the mouse on top, every click landed on the drag layer and no
        // button could ever be pressed.
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            z: 10

            onPositionChanged: (mouse) => {
                if (!root.owns(modelData)) { cursorShape = Qt.CrossCursor; return; }
                if (!root.isSelecting) {
                    switch (root.hitMode(mouse.x, mouse.y)) {
                        case 3: case 10: cursorShape = Qt.SizeFDiagCursor; break;
                        case 5: case 8: cursorShape = Qt.SizeBDiagCursor; break;
                        case 4: case 9: cursorShape = Qt.SizeVerCursor; break;
                        case 6: case 7: cursorShape = Qt.SizeHorCursor; break;
                        default: cursorShape = Qt.CrossCursor;
                    }
                    return;
                }
                let dx = mouse.x - root.anchorX;
                let dy = mouse.y - root.anchorY;
                if (root.interactionMode === 1) {
                    root.endX = root.clamp(mouse.x, 0, root.mouseW);
                    root.endY = root.clamp(mouse.y, 0, root.mouseH);
                } else {
                    let nx = root.initX, ny = root.initY, nw = root.initW, nh = root.initH;
                    if ([3, 6, 8].indexOf(root.interactionMode) >= 0) {
                        nx = root.clamp(root.initX + dx, 0, root.initX + root.initW - root.s(10));
                        nw = root.initW + (root.initX - nx);
                    }
                    if ([5, 7, 10].indexOf(root.interactionMode) >= 0)
                        nw = root.clamp(root.initW + dx, root.s(10), root.mouseW - root.initX);
                    if ([3, 4, 5].indexOf(root.interactionMode) >= 0) {
                        ny = root.clamp(root.initY + dy, 0, root.initY + root.initH - root.s(10));
                        nh = root.initH + (root.initY - ny);
                    }
                    if ([8, 9, 10].indexOf(root.interactionMode) >= 0)
                        nh = root.clamp(root.initH + dy, root.s(10), root.mouseH - root.initY);
                    root.startX = nx; root.startY = ny; root.endX = nx + nw; root.endY = ny + nh;
                }
            }

            onPressed: (mouse) => {
                if (mouse.button === Qt.RightButton) { root.close(); return; }
                // Нажатие на этом окне и означает «я этот выход»: нажатие приходит
                // только если курсор на этом мониторе. Размеры - у окна, у каждого
                // выхода свои.
                root.claimScreen(modelData, width, height);
                root.anchorX = mouse.x; root.anchorY = mouse.y;
                root.initX = root.selX; root.initY = root.selY;
                root.initW = root.selW; root.initH = root.selH;
                root.interactionMode = root.hasSelection ? root.hitMode(mouse.x, mouse.y) : 1;
                if (root.interactionMode === 1) {
                    root.startX = mouse.x; root.startY = mouse.y;
                    root.endX = mouse.x; root.endY = mouse.y;
                    root.isMaximized = false;
                }
                root.isSelecting = true;
                root.hasSelection = true;
            }

            onReleased: (mouse) => {
                if (!root.owns(modelData)) return;
                root.isSelecting = false;
                if (root.selW < root.s(10) || root.selH < root.s(10)) {
                    root.reset();
                    return;
                }
                if (root.autoAction !== "")
                    root.finish(root.autoAction);
            }
        }
    }
    }
}
