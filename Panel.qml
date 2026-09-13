pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

Panel {
    id: root
    moduleName: "onelegdave.omadroid"
    ipcTarget: moduleName
    manageIpc: false
    implicitWidth: barButton.implicitWidth
    implicitHeight: barButton.implicitHeight
    readonly property var fontStyle: Style.font
    readonly property var colors: Color.popups
    readonly property color ink: colors.text
    readonly property color accent: Color.accent
    readonly property color muted: fade(ink, 0.62)
    readonly property color success: accent
    readonly property color accentInk: contrast(ink, accent) >= contrast(colors.background, accent) ? ink : colors.background
    readonly property var shellHost: bar
    readonly property string backend: localFile("phone_mirror.py")
    readonly property var backendEnvironment: {
        const env = {
            "PATH": "/usr/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8"
        };
        for (const key of ["HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "WAYLAND_DISPLAY", "DISPLAY"]) {
            const value = Quickshell.env(key);
            if (value)
                env[key] = value;
        }
        return env;
    }
    property var snapshot: ({
            devices: [],
            services: [],
            dependencies: {},
            kde: {
                devices: []
            },
            saved: [],
            errors: [],
            remembered: [],
            reconnect: []
        })
    property string page: "phones"
    property string method: "wifi"
    property int step: 0
    property string message: ""
    property bool failed: false
    property string currentAction: ""
    property bool paired: false
    property double fastChecksUntil: 0
    property int statusChecks: 0
    readonly property int pollInterval: !opened ? 30000 : page === "connect" && (step < 3 || paired) && now < fastChecksUntil ? 3000 : 10000
    property double received: 0
    property double now: Date.now()
    property var retryTimes: ({})
    property int previousReady: -1
    property string connectionEvent: ""
    readonly property bool busy: actionProcess.running
    readonly property bool ready: !!snapshot.dependencies.adb && !!snapshot.dependencies.scrcpy
    readonly property int readyCount: snapshot.devices.filter(d => d.state === "device").length
    readonly property int mirroringCount: snapshot.devices.filter(d => d.mirroring).length
    readonly property var connectServices: snapshot.services.filter(s => s.kind === "connect")
    readonly property var pairServices: snapshot.services.filter(s => s.kind === "pair")
    readonly property var nearby: connectServices.filter(s => !snapshot.devices.some(d => (d.transports || [d]).some(t => t.serial === s.address || t.serial.indexOf(s.name) >= 0) || (s.identity && d.hardwareId === s.identity)) && !(snapshot.remembered || []).some(p => p.address === s.address))
    readonly property string connectionLabel: {
        if (busy)
            return ({
                    pair: "Pairing your phone",
                    connect: "Connecting your phone",
                    mirror: "Opening the mirror",
                    wake: "Waking your phone",
                    stop: "Closing the mirror",
                    disconnect: "Disconnecting"
                })[currentAction] || "Working";
        if (!received)
            return "Looking for your droid";
        if (now - received > 60000)
            return "Waiting for a status update";
        if (!ready)
            return "A little desktop setup first";
        if (mirroringCount)
            return "Your droid is on the desktop";
        if (readyCount)
            return "Ready when you are";
        if ((snapshot.remembered || []).length)
            return "Waiting for your phone";
        if (connectServices.length)
            return "A droid has been detected";
        return "Bring your phone to the desktop";
    }
    function luminance(c) {
        const linear = x => x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
        return 0.2126 * linear(c.r) + 0.7152 * linear(c.g) + 0.0722 * linear(c.b);
    }
    function contrast(a, b) {
        const x = luminance(a), y = luminance(b);
        return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
    }
    function corner(limit) {
        return Math.min(Style.cornerRadius, Style.space(limit));
    }
    function fade(c, a) {
        return Qt.rgba(c.r, c.g, c.b, a);
    }
    function localFile(name) {
        return decodeURIComponent(Qt.resolvedUrl(name).toString().replace("file://", ""));
    }
    function save(key, value) {
        const next = Object.assign({}, settings);
        next[key] = value;
        if (shellHost && shellHost.shell)
            shellHost.shell.updateEntryInline(moduleName, next);
    }
    function navigate(value) {
        page = value;
        if (value === "connect")
            fastChecksUntil = Date.now() + 120000;
        message = "";
        failed = false;
        Qt.callLater(function () {
            const flick = scroll.contentItem as Flickable;
            if (flick)
                flick.contentY = 0;
        });
    }
    function showStep(value) {
        step = value;
        navigate("connect");
    }
    function refresh() {
        if (!statusProcess.running)
            statusProcess.running = true;
    }
    function act(request) {
        if (busy)
            return;
        currentAction = request.action;
        if (request.action === "pair" || request.action === "connect")
            fastChecksUntil = Date.now() + 120000;
        failed = false;
        message = ({
                pair: "Pairing… Keep the code screen open on your phone.",
                connect: "Connecting…",
                mirror: "Opening your phone display…",
                wake: "Waking your phone…",
                stop: "Closing the mirror…",
                disconnect: "Disconnecting…"
            })[request.action] || "Working…";
        actionProcess.payload = JSON.stringify(request);
        actionProcess.running = true;
    }
    function updateStatus(data) {
        // Keep unchanged delegates alive during polling, including keyboard focus.
        for (const key of ["devices", "services", "saved", "remembered"]) {
            if (JSON.stringify(data[key]) === JSON.stringify(snapshot[key]))
                data[key] = snapshot[key];
        }
        if (data.kde && snapshot.kde && JSON.stringify(data.kde.devices) === JSON.stringify(snapshot.kde.devices))
            data.kde.devices = snapshot.kde.devices;
        const count = data.devices.filter(d => d.state === "device").length;
        if (previousReady >= 0 && count !== previousReady) {
            connectionEvent = (count > previousReady ? "Connected" : "Disconnected") + " at " + Qt.formatTime(new Date(), "HH:mm:ss");
            if (count > 0 && !busy) {
                message = "";
                failed = false;
            }
        }
        previousReady = count;
        snapshot = data;
        statusChecks++;
        received = Date.now();
        now = received;
        if (count > 0 && paired) {
            step = 3;
            paired = false;
        }
        if (!busy && setting("autoReconnect", true)) {
            const candidate = (data.reconnect || []).find(s => Date.now() - (retryTimes[s.address] || 0) > 30000);
            if (candidate) {
                retryTimes[candidate.address] = Date.now();
                Qt.callLater(function () {
                    if (!root.busy)
                        root.act({
                            action: "connect",
                            address: candidate.address
                        });
                });
            }
        }
    }
    function mirror(serial) {
        act({
            action: "mirror",
            serial: serial,
            quality: setting("quality", "Balanced"),
            audio: setting("audio", true),
            screenOff: setting("screenOff", false),
            keepAwake: setting("keepAwake", true)
        });
    }
    function address(ip, port) {
        const host = ip.trim();
        return (host.indexOf(":") >= 0 && !host.startsWith("[") ? "[" + host + "]" : host) + ":" + port.trim();
    }
    function useAddress(value, pairing) {
        const split = value.lastIndexOf(":"), host = value.slice(0, split).replace(/^\[|\]$/g, ""), port = value.slice(split + 1);
        if (pairing) {
            pairIp.text = host;
            pairPort.text = port;
        } else {
            connectIp.text = host;
            connectPort.text = port;
        }
    }
    function pairPhone() {
        act({
            action: "pair",
            address: address(pairIp.text, pairPort.text),
            code: pairCode.text
        });
        pairCode.text = "";
    }
    function installTools(group) {
        if (["core", "companion", "discovery", "usb"].indexOf(group) < 0)
            return;
        message = "The installer is opening in a terminal. Enter your desktop password there if asked, then return here. Tool status updates automatically.";
        failed = false;
        act({
            action: "install-tools",
            group: group
        });
    }
    onOpenedChanged: {
        if (opened) {
            now = Date.now();
            refresh();
        } else
            pairCode.text = "";
    }
    IpcHandler {
        target: root.moduleName
        function open(): void {
            root.open();
        }
        function close(): void {
            root.close();
        }
        function toggle(): void {
            root.toggle();
        }
        function setup(): void {
            root.navigate("connect");
            root.open();
        }
        function help(): void {
            root.navigate("help");
            root.open();
        }
        function settings(): void {
            root.navigate("settings");
            root.open();
        }
        function phones(): void {
            root.navigate("phones");
            root.open();
        }
        function refresh(): void {
            root.refresh();
        }
        function status(): string {
            return JSON.stringify({
                version: "0.3.4",
                name: "OmaDroid",
                theme: {
                    background: root.colors.background.toString(),
                    text: root.ink.toString(),
                    accent: root.accent.toString(),
                    status: root.success.toString(),
                    buttonText: root.accentInk.toString(),
                    font: root.fontStyle.family,
                    bodySize: root.fontStyle.body,
                    radius: Style.cornerRadius
                },
                pollInterval: root.pollInterval,
                statusChecks: root.statusChecks,
                snapshot: root.snapshot,
                message: root.message,
                failed: root.failed,
                busy: root.busy,
                page: root.page,
                step: root.step,
                opened: root.opened,
                geometry: {
                    x: panel.cardOrigin.x,
                    y: panel.cardOrigin.y,
                    width: panel.contentWidth,
                    height: panel.contentHeight
                }
            });
        }
    }
    Process {
        id: statusProcess
        command: ["/usr/bin/python3", "-E", "-s", root.backend, "status"]
        workingDirectory: "/"
        clearEnvironment: true
        environment: root.backendEnvironment
        running: true
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const data = JSON.parse(text);
                    if (data.devices && data.dependencies)
                        root.updateStatus(data);
                    else {
                        root.message = data.message || "Unable to read phone status.";
                        root.failed = true;
                    }
                } catch (e) {
                    root.message = "Could not check your phone. Click Refresh to try again. If this continues, open Help → Desktop tools.";
                    root.failed = true;
                }
            }
        }
    }
    Process {
        id: actionProcess
        property string payload: ""
        command: ["/usr/bin/python3", "-E", "-s", root.backend, "action"]
        workingDirectory: "/"
        clearEnvironment: true
        environment: root.backendEnvironment
        stdinEnabled: true
        onStarted: {
            write(payload + "\n");
            payload = "";
        }
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const result = JSON.parse(text);
                    root.message = result.message || "Done.";
                    root.failed = result.ok !== true;
                    if (result.paired) {
                        root.paired = true;
                        root.step = 3;
                    }
                    if (result.connected && root.page === "connect") {
                        root.step = 3;
                        root.paired = false;
                    }
                } catch (e) {
                    root.message = "That did not finish. Check your phone and try again.";
                    root.failed = true;
                }
                root.refresh();
            }
        }
    }
    Timer {
        interval: root.pollInterval
        running: true
        repeat: true
        onTriggered: root.refresh()
    }
    Timer {
        interval: 5000
        running: root.opened
        repeat: true
        onTriggered: root.now = Date.now()
    }
    WidgetButton {
        id: barButton
        bar: root.bar
        text: "󰄜"
        tooltipText: "OmaDroid · " + root.connectionLabel
        onPressed: mouseButton => {
            if (mouseButton === Qt.RightButton) {
                root.navigate("help");
                root.open();
            } else
                root.toggle();
        }
    }
    KeyboardPanel {
        id: panel
        anchorItem: barButton
        owner: root
        bar: root.bar
        open: root.opened
        focusTarget: keys
        contentWidth: panel.fittedContentWidth(Style.space(560))
        contentHeight: panel.fittedContentHeight(chrome.implicitHeight + pageBody.implicitHeight + footer.implicitHeight + Style.space(34), Style.space(880))
        Item {
            id: keys
            anchors.fill: parent
            Keys.onEscapePressed: root.close()
            Column {
                id: chrome
                width: parent.width
                spacing: Style.space(16)
                RowLayout {
                    width: parent.width
                    spacing: Style.space(13)
                    DroidMark {
                        Layout.preferredWidth: Style.space(52)
                        Layout.preferredHeight: Style.space(56)
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Style.space(3)
                        Label {
                            text: "OmaDroid"
                            font.pixelSize: root.fontStyle.displayLarge
                            font.bold: true
                            font.letterSpacing: -0.6
                        }
                        Label {
                            text: "This is the droid you’re looking for."
                            color: root.muted
                            font.pixelSize: root.fontStyle.bodySmall
                        }
                    }
                    ActionButton {
                        text: "Refresh"
                        helpText: "Refresh phone and tool status"
                        onClicked: root.refresh()
                    }
                }
                RowLayout {
                    width: parent.width
                    spacing: Style.space(4)
                    Repeater {
                        model: [
                            {
                                key: "phones",
                                label: "Phones"
                            },
                            {
                                key: "connect",
                                label: "Connect"
                            },
                            {
                                key: "settings",
                                label: "Settings"
                            },
                            {
                                key: "help",
                                label: "Help"
                            }
                        ]
                        ActionButton {
                            required property var modelData
                            Layout.fillWidth: true
                            text: modelData.label
                            primary: root.page === modelData.key
                            onClicked: root.navigate(modelData.key)
                        }
                    }
                }
                Rectangle {
                    width: parent.width
                    height: 1
                    color: root.fade(root.ink, 0.10)
                }
                Notice {
                    width: parent.width
                    visible: root.message !== ""
                    text: root.message
                    error: root.failed
                    onDismissed: root.message = ""
                }
            }
            Controls.ScrollView {
                id: scroll
                anchors {
                    top: chrome.bottom
                    bottom: footer.top
                    left: parent.left
                    right: parent.right
                    topMargin: Style.space(16)
                    bottomMargin: Style.space(12)
                }
                clip: true
                contentWidth: availableWidth
                contentHeight: pageBody.implicitHeight
                Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff
                Controls.ScrollBar.vertical: Controls.ScrollBar {
                    policy: Controls.ScrollBar.AsNeeded
                    contentItem: Rectangle {
                        implicitWidth: Style.space(4)
                        radius: width / 2
                        color: root.fade(root.ink, 0.35)
                    }
                }
                Column {
                    id: pageBody
                    width: scroll.availableWidth
                    spacing: Style.space(14)
                    Column {
                        width: parent.width
                        spacing: Style.space(14)
                        visible: root.page === "phones"
                        Card {
                            width: parent.width
                            tinted: true
                            RowLayout {
                                width: parent.width
                                spacing: Style.space(10)
                                Rectangle {
                                    implicitWidth: Style.space(9)
                                    implicitHeight: implicitWidth
                                    radius: width / 2
                                    color: root.readyCount ? root.success : root.accent
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: root.connectionLabel
                                    font.pixelSize: root.fontStyle.heading
                                    font.bold: true
                                }
                                Chip {
                                    text: root.mirroringCount ? "LIVE" : root.readyCount ? "READY" : "STANDBY"
                                    positive: root.readyCount > 0
                                }
                            }
                            Label {
                                width: parent.width
                                color: root.muted
                                text: root.mirroringCount ? "Your phone is open in a mirror window. Click, type, and scroll as usual." : root.readyCount ? "Your phone is connected. Choose Mirror to open its display." : root.ready ? "Connect an Android phone over Wi-Fi or USB. No extra mirroring app needed on the phone." : "Install the desktop tools, then connect your Android phone."
                            }
                            Label {
                                text: (root.connectionEvent ? root.connectionEvent + " · " : "") + (root.received ? "Last checked " + Qt.formatTime(new Date(root.received), "HH:mm:ss") : "Checking…")
                                color: root.muted
                                font.pixelSize: root.fontStyle.caption
                            }
                            ActionButton {
                                visible: !root.ready || (!root.readyCount && !(root.snapshot.remembered || []).length && !root.nearby.length)
                                text: root.ready ? "Connect a phone →" : "Set up desktop tools →"
                                primary: true
                                onClicked: {
                                    root.step = 0;
                                    root.navigate("connect");
                                }
                            }
                        }
                        Notice {
                            width: parent.width
                            visible: root.snapshot.errors.length > 0
                            text: root.snapshot.errors.join("\n")
                            error: true
                            dismissible: false
                        }
                        SectionHeading {
                            width: parent.width
                            title: "Your phones"
                            detail: "One phone, one place — over Wi-Fi or USB"
                            visible: root.snapshot.devices.length > 0 || (root.snapshot.remembered || []).length > 0
                        }
                        Repeater {
                            model: root.snapshot.devices
                            Card {
                                id: deviceCard
                                required property var modelData
                                width: pageBody.width
                                RowLayout {
                                    width: parent.width
                                    spacing: Style.space(12)
                                    DroidMark {
                                        Layout.preferredWidth: Style.space(32)
                                        Layout.preferredHeight: Style.space(40)
                                        subtle: true
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: Style.space(4)
                                        Label {
                                            Layout.fillWidth: true
                                            text: deviceCard.modelData.name
                                            font.pixelSize: root.fontStyle.heading
                                            font.bold: true
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            color: root.muted
                                            font.pixelSize: root.fontStyle.bodySmall
                                            text: deviceCard.modelData.connectionSummary + " · " + (deviceCard.modelData.state === "device" ? deviceCard.modelData.mirroring ? "Mirroring active" : "Ready to mirror" : deviceCard.modelData.state)
                                        }
                                    }
                                    Chip {
                                        text: deviceCard.modelData.mirroring ? "LIVE" : deviceCard.modelData.state === "device" ? "READY" : "CHECK PHONE"
                                        positive: deviceCard.modelData.state === "device"
                                    }
                                }
                                Label {
                                    width: parent.width
                                    text: deviceCard.modelData.serial
                                    color: root.muted
                                    font.pixelSize: root.fontStyle.caption
                                    elide: Text.ElideMiddle
                                    wrapMode: Text.NoWrap
                                }
                                Label {
                                    width: parent.width
                                    visible: deviceCard.modelData.state !== "device"
                                    text: deviceCard.modelData.state === "unauthorized" ? "Unlock your phone and accept “Allow USB debugging?”. Then click Refresh." : deviceCard.modelData.state === "no permissions" ? "USB access needs device rules. Open Help → Desktop tools to install USB support, then reconnect the cable." : "Reconnect the cable, or check that Wireless debugging is enabled on the phone."
                                }
                                RowLayout {
                                    width: parent.width
                                    spacing: Style.space(7)
                                    ActionButton {
                                        Layout.fillWidth: true
                                        text: deviceCard.modelData.mirroring ? "Stop mirror" : "Mirror →"
                                        primary: !deviceCard.modelData.mirroring
                                        enabled: root.ready && !root.busy && deviceCard.modelData.state === "device"
                                        helpText: deviceCard.modelData.mirroring ? "Close the mirror window; keep the phone connected" : "Open your phone display and control it with mouse and keyboard"
                                        onClicked: deviceCard.modelData.mirroring ? root.act({
                                            action: "stop",
                                            scope: "phone",
                                            serial: deviceCard.modelData.serial
                                        }) : root.mirror(deviceCard.modelData.serial)
                                    }
                                    ActionButton {
                                        text: "Wake / unlock"
                                        enabled: !root.busy && deviceCard.modelData.state === "device"
                                        helpText: "Wake the phone and show its normal unlock prompt"
                                        onClicked: root.act({
                                            action: "wake",
                                            serial: deviceCard.modelData.serial
                                        })
                                    }
                                    ActionButton {
                                        text: "Disconnect"
                                        visible: deviceCard.modelData.wireless
                                        enabled: !root.busy
                                        helpText: "Disconnect this phone’s Wi-Fi connections and pause automatic reconnect"
                                        onClicked: root.act({
                                            action: "disconnect",
                                            scope: "phone",
                                            serial: deviceCard.modelData.serial
                                        })
                                    }
                                }
                            }
                        }
                        Repeater {
                            model: root.snapshot.remembered || []
                            Card {
                                id: remembered
                                required property var modelData
                                width: pageBody.width
                                RowLayout {
                                    width: parent.width
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        Label {
                                            Layout.fillWidth: true
                                            text: remembered.modelData.name
                                            font.bold: true
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            color: root.muted
                                            text: remembered.modelData.paused ? "Reconnect paused · choose Connect to resume; no new pairing needed" : remembered.modelData.nearby ? "Nearby · ready to reconnect" : "Offline · check Wireless debugging on the phone"
                                        }
                                    }
                                    ActionButton {
                                        text: "Connect"
                                        primary: true
                                        enabled: !root.busy
                                        onClicked: root.act({
                                            action: "connect",
                                            address: remembered.modelData.address
                                        })
                                    }
                                }
                            }
                        }
                        Repeater {
                            model: root.nearby
                            Card {
                                id: found
                                required property var modelData
                                width: pageBody.width
                                Label {
                                    width: parent.width
                                    text: found.modelData.label || "Android phone nearby"
                                    font.bold: true
                                }
                                Label {
                                    width: parent.width
                                    text: "Discovered live on your local network; this is not a saved pairing. Already paired with this computer? Choose Connect. Otherwise, use the Connect tab to pair."
                                    color: root.muted
                                }
                                RowLayout {
                                    width: parent.width
                                    Label {
                                        Layout.fillWidth: true
                                        text: found.modelData.address
                                        color: root.muted
                                        font.pixelSize: root.fontStyle.bodySmall
                                    }
                                    ActionButton {
                                        text: "Connect"
                                        enabled: !root.busy
                                        onClicked: root.act({
                                            action: "connect",
                                            address: found.modelData.address
                                        })
                                    }
                                }
                            }
                        }
                        SectionHeading {
                            width: parent.width
                            title: "KDE Connect"
                            detail: "Optional companion features · a separate connection"
                        }
                        Repeater {
                            model: root.snapshot.kde.devices
                            Card {
                                id: companion
                                required property var modelData
                                width: pageBody.width
                                RowLayout {
                                    width: parent.width
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        Label {
                                            Layout.fillWidth: true
                                            text: companion.modelData.name
                                            font.bold: true
                                        }
                                        Label {
                                            color: root.muted
                                            text: (companion.modelData.reachable ? "KDE Connect online" : "KDE Connect offline") + (companion.modelData.battery !== null ? " · " + companion.modelData.battery + "%" + (companion.modelData.charging ? " · Charging" : "") : "")
                                        }
                                    }
                                    ActionButton {
                                        text: "Ring"
                                        enabled: companion.modelData.reachable && !root.busy
                                        helpText: "Make this phone ring so you can find it"
                                        onClicked: root.act({
                                            action: "ring",
                                            id: companion.modelData.id
                                        })
                                    }
                                }
                            }
                        }
                        Label {
                            width: parent.width
                            color: root.muted
                            text: "KDE Connect handles battery status, notifications, and file sharing. “Online” here does not mean screen mirroring is connected."
                            font.pixelSize: root.fontStyle.bodySmall
                        }
                        ActionButton {
                            width: parent.width
                            text: root.snapshot.dependencies.kdeconnect ? "Open KDE Connect" : "Install KDE Connect"
                            onClicked: root.snapshot.dependencies.kdeconnect ? root.act({
                                action: "open-companion"
                            }) : root.installTools("companion")
                        }
                    }
                    Column {
                        width: parent.width
                        spacing: Style.space(14)
                        visible: root.page === "connect"
                        SectionHeading {
                            width: parent.width
                            title: "Connect your Android"
                            detail: "One setup. Many fewer trips to your pocket."
                        }
                        RowLayout {
                            width: parent.width
                            spacing: Style.space(4)
                            Repeater {
                                model: ["Desktop", "Phone", "Pair", "Ready"]
                                ActionButton {
                                    required property string modelData
                                    required property int index
                                    Layout.fillWidth: true
                                    text: (index + 1) + ". " + modelData
                                    primary: root.step === index
                                    onClicked: root.showStep(index)
                                }
                            }
                        }
                        Column {
                            width: parent.width
                            spacing: Style.space(12)
                            visible: root.step === 0
                            Card {
                                width: parent.width
                                Label {
                                    text: "1. Get the desktop ready"
                                    font.bold: true
                                    font.pixelSize: root.fontStyle.heading
                                }
                                Label {
                                    width: parent.width
                                    text: "OmaDroid uses two desktop tools to display and control your phone. Install them here; no terminal commands to memorize."
                                    color: root.muted
                                }
                                ToolsList {
                                    width: parent.width
                                    coreOnly: true
                                }
                            }
                            ActionButton {
                                width: parent.width
                                text: "Desktop ready · continue →"
                                primary: true
                                enabled: root.ready
                                onClicked: root.showStep(1)
                            }
                            Label {
                                width: parent.width
                                text: "Nothing extra needs installing on your phone for mirroring. KDE Connect is optional."
                                color: root.muted
                                font.pixelSize: root.fontStyle.bodySmall
                            }
                        }
                        Column {
                            width: parent.width
                            spacing: Style.space(12)
                            visible: root.step === 1
                            RowLayout {
                                width: parent.width
                                ActionButton {
                                    Layout.fillWidth: true
                                    text: "Wi-Fi · Android 11+"
                                    primary: root.method === "wifi"
                                    onClicked: root.method = "wifi"
                                }
                                ActionButton {
                                    Layout.fillWidth: true
                                    text: "USB cable · Android 5+"
                                    primary: root.method === "usb"
                                    onClicked: root.method = "usb"
                                }
                            }
                            Card {
                                width: parent.width
                                Label {
                                    text: "2. Prepare your phone"
                                    font.bold: true
                                    font.pixelSize: root.fontStyle.heading
                                }
                                StepText {
                                    width: parent.width
                                    number: "1"
                                    title: "Enable Developer options"
                                    detail: "Open Settings → About phone. Find Build number and tap it seven times. Enter your phone PIN if asked. On Samsung: About phone → Software information → Build number."
                                }
                                StepText {
                                    width: parent.width
                                    number: "2"
                                    title: root.method === "wifi" ? "Use the same local network" : "Connect a data-capable USB cable"
                                    detail: root.method === "wifi" ? "Put the phone and computer on the same home or office network. The computer may use Ethernet. Guest networks and VPNs can block discovery." : "Some cables only charge. If the phone never appears, try another data cable or USB port."
                                }
                                StepText {
                                    width: parent.width
                                    number: "3"
                                    title: root.method === "wifi" ? "Open Wireless debugging" : "Enable USB debugging"
                                    detail: root.method === "wifi" ? "In Settings → Developer options, turn on Wireless debugging. Tap its name to open the settings. Allow this network if Android asks." : "In Settings → Developer options, enable USB debugging. Unlock your phone and accept “Allow USB debugging?” for this computer."
                                }
                            }
                            ActionButton {
                                width: parent.width
                                text: root.method === "wifi" ? "Wireless debugging is open →" : "Check for my USB phone →"
                                primary: true
                                onClicked: {
                                    root.showStep(root.method === "wifi" ? 2 : 3);
                                    root.refresh();
                                }
                            }
                        }
                        Column {
                            width: parent.width
                            spacing: Style.space(12)
                            visible: root.step === 2
                            Card {
                                width: parent.width
                                Label {
                                    text: "3. Pair with a code"
                                    font.bold: true
                                    font.pixelSize: root.fontStyle.heading
                                }
                                Label {
                                    width: parent.width
                                    text: "On your phone’s Wireless debugging screen, tap “Pair device with pairing code”. Keep that dialog open while you fill in the fields below."
                                }
                                NoteBox {
                                    width: parent.width
                                    title: "Use the pairing dialog’s details"
                                    text: "It shows an IP address, a pairing port, and a six-digit code. These can change each time you open it. Use detected address when available."
                                }
                                NoteBox {
                                    width: parent.width
                                    title: "Tailscale or another phone VPN?"
                                    text: "Android may show a VPN address instead of Wi-Fi. OmaDroid does not accept Tailscale's 100.64–100.127 addresses. Use a detected Wi-Fi pairing address, or pause the phone VPN and reopen the pairing dialog. After pairing, you can turn the VPN back on and choose Connect beside the saved phone. Keep both devices on the same local network."
                                }
                                Repeater {
                                    model: root.pairServices
                                    ActionButton {
                                        required property var modelData
                                        width: parent.width
                                        text: "Use detected address · " + modelData.address
                                        onClicked: root.useAddress(modelData.address, true)
                                    }
                                }
                                RowLayout {
                                    width: parent.width
                                    spacing: Style.space(10)
                                    Field {
                                        id: pairIp
                                        Layout.fillWidth: true
                                        label: "Phone IP address"
                                        placeholder: "192.168.1.20"
                                        enabled: !root.busy
                                    }
                                    Field {
                                        id: pairPort
                                        Layout.preferredWidth: Style.space(132)
                                        label: "Pairing port"
                                        placeholder: "37123"
                                        enabled: !root.busy
                                    }
                                }
                                Field {
                                    id: pairCode
                                    width: parent.width
                                    label: "Six-digit pairing code"
                                    placeholder: "••••••"
                                    secret: true
                                    maxLength: 6
                                    enabled: !root.busy
                                    onAccepted: root.pairPhone()
                                }
                                ActionButton {
                                    width: parent.width
                                    text: root.busy && root.currentAction === "pair" ? "Pairing…" : "Pair my phone →"
                                    primary: true
                                    enabled: root.ready && !root.busy && pairCode.text.length === 6 && pairIp.text.trim() !== "" && pairPort.text.trim() !== ""
                                    onClicked: root.pairPhone()
                                }
                                Label {
                                    width: parent.width
                                    text: "The code is used once and never saved. This pairs mirroring; KDE Connect pairing is separate."
                                    color: root.muted
                                    font.pixelSize: root.fontStyle.bodySmall
                                }
                            }
                            ActionButton {
                                width: parent.width
                                text: "Already paired? Skip to connection →"
                                onClicked: root.showStep(3)
                            }
                        }
                        Column {
                            width: parent.width
                            spacing: Style.space(12)
                            visible: root.step === 3
                            Card {
                                width: parent.width
                                tinted: root.readyCount > 0
                                Label {
                                    text: root.readyCount ? "4. Your droid is ready" : "4. Open the mirroring connection"
                                    font.bold: true
                                    font.pixelSize: root.fontStyle.heading
                                }
                                Label {
                                    width: parent.width
                                    text: root.readyCount ? "Your phone is connected. Open Phones, then choose Mirror. Mouse clicks act like taps; click and drag to swipe." : root.method === "usb" ? "Unlock the phone and accept its USB debugging prompt. It will appear in Phones when ready. If it does not, check the cable or install USB support under Help → Desktop tools." : "Return to the main Wireless debugging screen on your phone. OmaDroid normally connects automatically. If it does not, use the connection address below."
                                    color: root.muted
                                }
                                ActionButton {
                                    width: parent.width
                                    text: "Go to my phones →"
                                    primary: true
                                    onClicked: root.navigate("phones")
                                }
                            }
                            Card {
                                width: parent.width
                                visible: root.method === "wifi"
                                Label {
                                    text: "Manual connection"
                                    font.bold: true
                                }
                                NoteBox {
                                    width: parent.width
                                    title: "Different screen. Different port."
                                    text: "Use “IP address & Port” on the MAIN Wireless debugging screen. Do not reuse the port from the pairing-code dialog."
                                }
                                Repeater {
                                    model: root.connectServices
                                    ActionButton {
                                        required property var modelData
                                        width: parent.width
                                        text: "Connect · " + modelData.address
                                        enabled: !root.busy
                                        onClicked: root.act({
                                            action: "connect",
                                            address: modelData.address
                                        })
                                    }
                                }
                                RowLayout {
                                    width: parent.width
                                    spacing: Style.space(10)
                                    Field {
                                        id: connectIp
                                        Layout.fillWidth: true
                                        label: "Phone IP address"
                                        placeholder: "192.168.1.20"
                                        enabled: !root.busy
                                    }
                                    Field {
                                        id: connectPort
                                        Layout.preferredWidth: Style.space(132)
                                        label: "Connection port"
                                        placeholder: "44923"
                                        enabled: !root.busy
                                    }
                                }
                                ActionButton {
                                    width: parent.width
                                    text: "Connect to my phone"
                                    primary: true
                                    enabled: root.ready && !root.busy && connectIp.text.trim() !== "" && connectPort.text.trim() !== ""
                                    onClicked: root.act({
                                        action: "connect",
                                        address: root.address(connectIp.text, connectPort.text)
                                    })
                                }
                                Label {
                                    width: parent.width
                                    text: "After a reboot or network change, turn Wireless debugging back on if needed. Saved ports can change; the phone’s current screen is the source of truth."
                                    color: root.muted
                                    font.pixelSize: root.fontStyle.bodySmall
                                }
                            }
                        }
                    }
                    Column {
                        width: parent.width
                        spacing: Style.space(14)
                        visible: root.page === "settings"
                        SectionHeading {
                            width: parent.width
                            title: "Make yourself at home"
                            detail: "Mirror settings apply when you next open a mirror."
                        }
                        Card {
                            width: parent.width
                            Label {
                                text: "Picture quality"
                                font.bold: true
                                font.pixelSize: root.fontStyle.heading
                            }
                            Label {
                                width: parent.width
                                text: "Balanced is a good starting point. Lower quality helps on a busy Wi-Fi network."
                                color: root.muted
                            }
                            Repeater {
                                model: [
                                    {
                                        name: "Balanced",
                                        detail: "1600 px · 60 fps · 8 Mbps"
                                    },
                                    {
                                        name: "Sharp",
                                        detail: "2560 px · 60 fps · 16 Mbps"
                                    },
                                    {
                                        name: "Low bandwidth",
                                        detail: "1024 px · 30 fps · 3 Mbps"
                                    }
                                ]
                                QualityChoice {
                                    required property var modelData
                                    width: parent.width
                                    title: modelData.name
                                    detail: modelData.detail
                                    selected: root.setting("quality", "Balanced") === modelData.name
                                    onChosen: root.save("quality", modelData.name)
                                }
                            }
                        }
                        Card {
                            width: parent.width
                            SettingToggle {
                                width: parent.width
                                title: "Play phone audio here"
                                detail: "Forward supported phone audio to this computer. Requires Android 11 or newer; some apps limit audio capture."
                                checked: root.setting("audio", true)
                                onToggled: root.save("audio", checked)
                            }
                            Divider {
                                width: parent.width
                            }
                            SettingToggle {
                                width: parent.width
                                title: "Keep the physical screen off"
                                detail: "The phone display stays dark while its mirror remains usable. A little less glow, a little more battery."
                                checked: root.setting("screenOff", false)
                                onToggled: root.save("screenOff", checked)
                            }
                            Divider {
                                width: parent.width
                            }
                            SettingToggle {
                                width: parent.width
                                title: "Keep the phone awake"
                                detail: "Prevent idle sleep during mirroring, including over Wi-Fi. Normal sleep resumes when the mirror closes."
                                checked: root.setting("keepAwake", true)
                                onToggled: root.save("keepAwake", checked)
                            }
                            Divider {
                                width: parent.width
                            }
                            SettingToggle {
                                width: parent.width
                                title: "Reconnect remembered phones"
                                detail: "Reconnect when a known phone returns. A deliberate Disconnect pauses reconnection for that connection."
                                checked: root.setting("autoReconnect", true)
                                onToggled: root.save("autoReconnect", checked)
                            }
                        }
                        ActionButton {
                            width: parent.width
                            text: "Manage desktop tools →"
                            onClicked: root.navigate("help")
                        }
                    }
                    Column {
                        width: parent.width
                        spacing: Style.space(14)
                        visible: root.page === "help"
                        SectionHeading {
                            width: parent.width
                            title: "A field guide to your droid"
                            detail: "Clear answers. Minimal technobabble."
                        }
                        ActionButton {
                            width: parent.width
                            text: "Walk me through connecting a phone →"
                            primary: true
                            onClicked: {
                                root.step = 0;
                                root.navigate("connect");
                            }
                        }
                        Card {
                            width: parent.width
                            Label {
                                text: "Desktop tools"
                                font.bold: true
                                font.pixelSize: root.fontStyle.heading
                            }
                            Label {
                                width: parent.width
                                text: "Choose what to install. A terminal shows the packages and asks for your desktop password if needed. Return here when it finishes."
                                color: root.muted
                            }
                            ToolsList {
                                width: parent.width
                            }
                        }
                        SectionHeading {
                            width: parent.width
                            title: "Using OmaDroid"
                            detail: "Click a topic to open its instructions"
                        }
                        HelpTopic {
                            width: parent.width
                            title: "Mouse, keyboard, and swiping"
                            detail: "Click = tap. Click and drag = swipe. Scroll with the mouse wheel. Type with your keyboard.\n\nRight-click = Back (or wake if the phone is asleep). Middle-click = Home. Left Alt+F or F11 toggles fullscreen. Left Alt+H goes Home.\n\nIf typing or gestures do not work, unlock the phone first. Some manufacturers require an extra USB debugging/security setting for remote input."
                        }
                        HelpTopic {
                            width: parent.width
                            title: "My phone went to sleep or locked"
                            detail: "Choose Wake / unlock beside the phone. Enter your normal PIN or use the phone’s normal unlock method if asked. OmaDroid does not bypass a secure lock.\n\nKeep phone awake in Settings prevents idle sleep while a mirror is open, even with the physical screen off. Close and reopen the mirror after changing this setting. In the mirror, right-click can also wake the phone."
                        }
                        HelpTopic {
                            width: parent.width
                            title: "Stop mirroring or disconnect completely"
                            detail: "Stop mirror closes this phone’s mirror windows but leaves the phone connected for next time. Closing the mirror window does the same thing.\n\nDisconnect closes this phone’s Wi-Fi connections and their mirrors. Automatic reconnect stays paused until you choose Connect again. USB stays available until you unplug the cable. Multiple connections to one identified phone share a single card.\n\nKDE Connect is separate and may remain online. Turning off Wireless debugging on the phone also stops its wireless mirroring connection."
                        }
                        HelpTopic {
                            width: parent.width
                            title: "Paired, but there is no Mirror button"
                            detail: "Pairing authorizes this computer; connecting opens the mirroring link. They are two steps.\n\nReturn to the MAIN Wireless debugging screen on the phone. Use its current IP address and connection port in Connect → Ready → Manual connection. Do not use the pairing-code dialog’s port.\n\nWhen the connection is ready, choose Mirror on the Phones tab. KDE Connect being online does not mean mirroring is connected."
                        }
                        HelpTopic {
                            width: parent.width
                            title: "Wi-Fi discovery or reconnection is failing"
                            detail: "Keep both devices on the same local network. The computer can use Ethernet. Avoid guest Wi-Fi or client-isolated networks; a VPN may block local discovery.\n\nCheck that Wireless debugging is still enabled after rebooting or changing networks. Try the current IP address and port shown on the phone. Install automatic discovery above if needed.\n\nIf pairing failed, open a fresh pairing-code dialog and use its new code and port. Automatic reconnect only targets remembered phones; it never authorizes an unknown phone."
                        }
                        HelpTopic {
                            width: parent.width
                            title: "USB phone not detected"
                            detail: "Use a data-capable cable. Unlock the phone, enable USB debugging in Developer options, and accept the computer’s authorization prompt.\n\nIf the phone says unauthorized, approve the prompt on the phone. If it reports no permissions, install USB support above, then unplug and reconnect the cable. Try another cable or USB port if nothing appears."
                        }
                        HelpTopic {
                            width: parent.width
                            title: "Tailscale, VPNs, and reconnecting"
                            detail: "Pair using the phone’s Wi-Fi address. Android may show its VPN address in Wireless debugging; Tailscale’s 100.64–100.127 addresses are not accepted by OmaDroid. Choose Use detected address while the pairing-code dialog is open, or temporarily pause the phone VPN and reopen that dialog. Use its new pairing port and code.\n\nAfter pairing, turn the VPN back on if needed. Keep the phone and computer on the same local network, refresh OmaDroid, and choose Connect beside the saved phone. A network change can briefly interrupt Android debugging; wait for Wireless debugging to settle and try Connect again with its current connection address. You do not need to pair again merely because you disconnected.\n\nDisconnect deliberately pauses automatic reconnect until Connect succeeds. Stop mirror only closes the mirror and keeps the phone connected. If a VPN exit node or kill switch blocks local traffic, allow LAN access in the VPN settings or pause the VPN while mirroring. OmaDroid does not change VPN settings or provide remote mirroring over Tailscale."
                        }
                        HelpTopic {
                            width: parent.width
                            title: "Why can I see a phone before pairing?"
                            detail: "Nearby phones advertise their name and debugging address on the local network. That live discovery is separate from saved connections and does not authorize this computer. Each computer needs its own Android pairing approval. Copying the plugin does not copy ADB keys or saved phone profiles."
                        }
                        HelpTopic {
                            width: parent.width
                            title: "KDE Connect: what it adds"
                            detail: "KDE Connect is optional. Install it on both the phone and computer, then pair them inside KDE Connect.\n\nIt provides battery information, notifications, file sharing, and Ring. OmaDroid’s screen mirroring uses its own Android debugging connection. The two pairing processes and online statuses are independent."
                        }
                        HelpTopic {
                            width: parent.width
                            title: "Privacy, permissions, and compatibility"
                            detail: "Mirroring uses a direct connection to your phone. OmaDroid does not record the screen. Pairing codes are never saved. Android debugging authorizes this computer to control the phone; forget the computer in Wireless debugging settings when you no longer trust it.\n\nUSB mirroring: Android 5+. Wireless pairing and audio: Android 11+. Protected screens and some apps can limit capture or audio. Keep-awake needs a recent scrcpy with --keep-active support.\n\nOmaDroid has no telemetry, ads, cloud relay, or update-check service. Normal connections use USB or private local-network phone addresses. Install buttons contact your configured package repositories only when you choose them. Automatic clipboard synchronization is disabled. Use left Alt+V to explicitly paste the computer clipboard into the phone.\n\nLocal logs: ~/.cache/phone-mirror. Saved phone identities: ~/.local/state/phone-mirror. These follow your XDG directory settings."
                        }
                        Card {
                            width: parent.width
                            Label {
                                width: parent.width
                                text: "OmaDroid · 0.3.4"
                                font.bold: true
                            }
                            Label {
                                width: parent.width
                                text: "An independent Omarchy community plugin. Powered by scrcpy, ADB, and optional KDE Connect. No Jedi mind tricks were used to pair your phone."
                                color: root.muted
                                font.pixelSize: root.fontStyle.bodySmall
                            }
                            ActionButton {
                                text: "OneLegDave ↗"
                                helpText: "Open www.onelegdave.dev in your browser"
                                onClicked: Qt.openUrlExternally("https://www.onelegdave.dev/")
                            }
                        }
                    }
                }
            }
            Label {
                id: footer
                anchors {
                    left: parent.left
                    right: parent.right
                    bottom: parent.bottom
                }
                text: "OMADROID  /  YOUR PHONE. YOUR COMPUTER. YOUR CONTROLS."
                font.pixelSize: root.fontStyle.caption
                font.letterSpacing: 0.5
                color: root.fade(root.ink, 0.4)
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }
    component Label: Text {
        textFormat: Text.PlainText
        color: root.ink
        font.family: root.fontStyle.family
        font.pixelSize: root.fontStyle.subtitle
        wrapMode: Text.Wrap
        lineHeight: 1.15
    }
    component Divider: Rectangle {
        height: 1
        color: root.fade(root.ink, 0.08)
    }
    component SectionHeading: Column {
        property string title
        property string detail: ""
        spacing: Style.space(4)
        Label {
            width: parent.width
            text: parent.title
            font.bold: true
            font.pixelSize: root.fontStyle.heading
        }
        Label {
            width: parent.width
            text: parent.detail
            visible: text !== ""
            color: root.muted
            font.pixelSize: root.fontStyle.bodySmall
        }
    }
    component Card: Rectangle {
        id: card
        property bool tinted: false
        default property alias contents: cardBody.data
        implicitHeight: cardBody.implicitHeight + Style.space(30)
        radius: root.corner(12)
        color: root.fade(tinted ? root.accent : root.ink, tinted ? 0.065 : 0.035)
        border.width: 1
        border.color: root.fade(tinted ? root.accent : root.ink, tinted ? 0.24 : 0.11)
        Column {
            id: cardBody
            x: Style.space(15)
            y: Style.space(15)
            width: card.width - Style.space(30)
            spacing: Style.space(12)
        }
    }
    component ActionButton: Controls.Button {
        id: control
        property bool primary: false
        property string helpText: ""
        implicitHeight: Style.space(38)
        implicitWidth: buttonLabel.implicitWidth + Style.space(24)
        opacity: enabled ? 1 : 0.42
        padding: Style.space(10)
        contentItem: Label {
            id: buttonLabel
            text: control.text
            color: control.primary ? root.accentInk : root.ink
            font.bold: control.primary
            font.pixelSize: root.fontStyle.body
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            wrapMode: Text.NoWrap
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: root.corner(7)
            color: control.primary ? root.accent : control.down ? Style.pressedFillFor(root.ink, root.accent, Color.urgent) : control.hovered ? Style.hoverFillFor(root.ink, root.accent, Color.urgent) : Style.normalFillFor(root.ink, root.accent, Color.urgent)
            border.width: control.activeFocus ? 2 : 1
            border.color: control.activeFocus ? root.accent : control.primary ? root.accent : root.fade(root.ink, 0.13)
            Behavior on color {
                ColorAnimation {
                    duration: 120
                }
            }
        }
        PanelToolTip {
            visible: control.hovered && control.helpText !== ""
            text: control.helpText
            fontFamily: root.fontStyle.family
        }
    }
    component Chip: Rectangle {
        id: chip
        property string text
        property bool positive: false
        implicitHeight: Style.space(23)
        implicitWidth: chipText.implicitWidth + Style.space(16)
        radius: root.corner(5)
        color: root.fade(positive ? root.success : root.ink, 0.12)
        Label {
            id: chipText
            anchors.centerIn: parent
            text: chip.text
            font.pixelSize: root.fontStyle.caption
            font.bold: true
            font.letterSpacing: 0.4
            color: chip.positive ? root.success : root.muted
        }
    }
    component DroidMark: Item {
        id: mark
        property bool subtle: false
        Rectangle {
            anchors.fill: parent
            radius: root.corner(12)
            color: root.fade(root.accent, mark.subtle ? 0.06 : 0.12)
        }
        Rectangle {
            anchors.centerIn: parent
            width: parent.width * 0.49
            height: parent.height * 0.68
            radius: root.corner(5)
            color: "transparent"
            border.width: Style.space(2)
            border.color: root.accent
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                y: parent.height * 0.1
                width: parent.width * 0.35
                height: Style.space(2)
                radius: 1
                color: root.accent
            }
            Row {
                anchors.centerIn: parent
                spacing: Style.space(4)
                Rectangle {
                    width: Style.space(3)
                    height: width
                    radius: width / 2
                    color: root.accent
                }
                Rectangle {
                    width: Style.space(3)
                    height: width
                    radius: width / 2
                    color: root.accent
                }
            }
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                y: parent.height * 0.84
                width: parent.width * 0.25
                height: Style.space(2)
                radius: 1
                color: root.accent
            }
        }
    }
    component Notice: Rectangle {
        id: notice
        property string text
        property bool error: false
        property bool dismissible: true
        signal dismissed
        implicitHeight: noticeRow.implicitHeight + Style.space(20)
        radius: root.corner(8)
        color: root.fade(error ? Color.urgent : root.accent, 0.08)
        border.width: 1
        border.color: root.fade(error ? Color.urgent : root.accent, 0.22)
        RowLayout {
            id: noticeRow
            x: Style.space(10)
            y: Style.space(10)
            width: notice.width - Style.space(20)
            Label {
                Layout.fillWidth: true
                text: notice.text
                font.pixelSize: root.fontStyle.bodySmall
                color: notice.error ? Color.urgent : root.ink
            }
            ActionButton {
                visible: notice.dismissible
                text: "×"
                implicitHeight: Style.space(24)
                implicitWidth: Style.space(25)
                onClicked: notice.dismissed()
            }
        }
    }
    component NoteBox: Rectangle {
        id: note
        property string title
        property string text
        implicitHeight: noteBody.implicitHeight + Style.space(24)
        radius: root.corner(7)
        color: root.fade(root.accent, 0.065)
        Column {
            id: noteBody
            x: Style.space(12)
            y: Style.space(12)
            width: note.width - Style.space(24)
            spacing: Style.space(5)
            Label {
                width: parent.width
                text: note.title
                font.bold: true
                font.pixelSize: root.fontStyle.body
            }
            Label {
                width: parent.width
                text: note.text
                color: root.muted
                font.pixelSize: root.fontStyle.bodySmall
            }
        }
    }
    component StepText: RowLayout {
        id: instruction
        property string number
        property string title
        property string detail
        spacing: Style.space(12)
        Chip {
            text: instruction.number
            Layout.alignment: Qt.AlignTop
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Style.space(5)
            Label {
                Layout.fillWidth: true
                text: instruction.title
                font.bold: true
            }
            Label {
                Layout.fillWidth: true
                text: instruction.detail
                color: root.muted
                font.pixelSize: root.fontStyle.body
            }
        }
    }
    component Field: Column {
        id: field
        property string label
        property string placeholder
        property bool secret: false
        property int maxLength: 256
        property alias text: input.text
        signal accepted
        spacing: Style.space(6)
        Label {
            text: field.label
            font.pixelSize: root.fontStyle.bodySmall
            font.bold: true
        }
        Controls.TextField {
            id: input
            width: parent.width
            implicitHeight: Style.space(43)
            placeholderText: field.placeholder
            echoMode: field.secret ? TextInput.Password : TextInput.Normal
            maximumLength: field.maxLength
            inputMethodHints: field.secret ? Qt.ImhSensitiveData | Qt.ImhDigitsOnly : Qt.ImhNoAutoUppercase
            color: root.ink
            placeholderTextColor: root.fade(root.ink, 0.35)
            selectionColor: root.accent
            selectedTextColor: root.accentInk
            font.family: root.fontStyle.family
            font.pixelSize: root.fontStyle.subtitle
            leftPadding: Style.space(12)
            rightPadding: Style.space(12)
            selectByMouse: true
            onAccepted: field.accepted()
            background: Rectangle {
                radius: root.corner(7)
                color: root.fade(root.ink, 0.025)
                border.width: input.activeFocus ? 2 : 1
                border.color: input.activeFocus ? root.accent : root.fade(root.ink, 0.23)
            }
        }
    }
    component SettingToggle: Controls.AbstractButton {
        id: toggle
        property string title
        property string detail
        checkable: true
        implicitHeight: toggleRow.implicitHeight
        padding: 0
        contentItem: RowLayout {
            id: toggleRow
            spacing: Style.space(16)
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Style.space(5)
                Label {
                    Layout.fillWidth: true
                    text: toggle.title
                    font.bold: true
                }
                Label {
                    Layout.fillWidth: true
                    text: toggle.detail
                    color: root.muted
                    font.pixelSize: root.fontStyle.bodySmall
                }
            }
            Rectangle {
                Layout.preferredWidth: Style.space(38)
                Layout.preferredHeight: Style.space(22)
                radius: height / 2
                color: toggle.checked ? root.accent : root.fade(root.ink, 0.18)
                border.width: toggle.activeFocus ? 2 : 0
                border.color: root.ink
                Rectangle {
                    x: toggle.checked ? parent.width - width - 3 : 3
                    y: 3
                    width: parent.height - 6
                    height: width
                    radius: width / 2
                    color: toggle.checked ? root.accentInk : root.ink
                    Behavior on x {
                        NumberAnimation {
                            duration: 120
                        }
                    }
                }
            }
        }
    }
    component QualityChoice: Controls.AbstractButton {
        id: quality
        property string title
        property string detail
        property bool selected
        signal chosen
        implicitHeight: qualityRow.implicitHeight + Style.space(18)
        onClicked: chosen()
        background: Rectangle {
            radius: root.corner(7)
            color: root.fade(root.accent, quality.selected ? 0.10 : 0)
            border.width: quality.activeFocus ? 2 : 1
            border.color: quality.selected || quality.activeFocus ? root.accent : root.fade(root.ink, 0.08)
        }
        contentItem: RowLayout {
            id: qualityRow
            anchors.fill: parent
            anchors.margins: Style.space(9)
            Label {
                text: quality.selected ? "●" : "○"
                color: quality.selected ? root.accent : root.muted
            }
            Label {
                Layout.fillWidth: true
                text: quality.title
                font.bold: quality.selected
            }
            Label {
                text: quality.detail
                color: root.muted
                font.pixelSize: root.fontStyle.caption
            }
        }
    }
    component HelpTopic: Card {
        id: topic
        property string title
        property string detail
        property bool expanded: false
        ActionButton {
            width: parent.width
            text: (topic.expanded ? "−  " : "+  ") + topic.title
            onClicked: topic.expanded = !topic.expanded
        }
        Label {
            width: parent.width
            visible: topic.expanded
            text: topic.detail
            color: root.muted
            font.pixelSize: root.fontStyle.body
        }
    }
    component ToolsList: Column {
        id: toolsList
        property bool coreOnly: false
        spacing: Style.space(12)
        ToolRow {
            width: parent.width
            title: "Mirroring tools"
            detail: "scrcpy + android-tools · required"
            available: root.ready
            group: "core"
            buttonText: "Install required tools"
        }
        ToolRow {
            width: parent.width
            visible: !toolsList.coreOnly
            title: "KDE Connect"
            detail: "Battery, notifications, file sharing · optional"
            available: !!root.snapshot.dependencies.kdeconnect
            group: "companion"
            buttonText: "Install"
        }
        ToolRow {
            width: parent.width
            visible: !toolsList.coreOnly
            title: "Wireless discovery"
            detail: "Avahi · optional; installs and enables its discovery service"
            available: !!root.snapshot.dependencies.discoveryReady
            group: "discovery"
            buttonText: root.snapshot.dependencies.avahi ? "Enable discovery" : "Install"
        }
        ToolRow {
            width: parent.width
            visible: !toolsList.coreOnly
            title: "USB support"
            detail: "android-udev · optional, for USB permission errors"
            available: !!root.snapshot.dependencies.usbRules
            group: "usb"
            buttonText: "Install USB rules"
        }
    }
    component ToolRow: Column {
        id: toolRow
        property string title
        property string detail
        property bool available
        property string group
        property string buttonText
        spacing: Style.space(7)
        RowLayout {
            width: parent.width
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Style.space(4)
                Label {
                    Layout.fillWidth: true
                    text: toolRow.title
                    font.bold: true
                }
                Label {
                    Layout.fillWidth: true
                    text: toolRow.detail
                    color: root.muted
                    font.pixelSize: root.fontStyle.bodySmall
                }
            }
            Chip {
                visible: toolRow.available
                text: "INSTALLED"
                positive: true
            }
        }
        ActionButton {
            width: parent.width
            visible: !toolRow.available
            text: toolRow.buttonText
            primary: toolRow.group === "core"
            onClicked: root.installTools(toolRow.group)
        }
    }
}
