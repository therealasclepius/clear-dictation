import QtQuick
import Quickshell
import Quickshell.Io
import qs.Ui

BarWidget {
  id: root
  moduleName: "kosta.clear-dictation"
  property string state: "idle"
  readonly property string runtime: Quickshell.env("XDG_RUNTIME_DIR")
  readonly property string launcher: Quickshell.env("HOME") + "/.local/bin/clear-dictation"
  // This launches a separate app, rather than owning a shell popup.
  // Omarchy requires opened/open/close to register a summonable widget.
  readonly property bool opened: false

  FileView {
    id: voiceState
    path: root.runtime + "/voxtype/state"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.state = text().trim()
  }

  implicitWidth: icon.implicitWidth
  implicitHeight: icon.implicitHeight

  function open() { Quickshell.execDetached([root.launcher, "ui"]) }
  function toggle() { open() }
  function close() {}

  BarIconButton {
    id: icon
    anchors.fill: parent
    bar: root.bar
    text: root.state === "recording" ? "󰍬" : "󰦨"
    active: root.state === "recording" || root.state === "transcribing"
    tooltipText: root.state === "recording" ? "Clear Dictation · Recording" : root.state === "transcribing" ? "Clear Dictation · Transcribing and cleaning up" : "Clear Dictation · History and writing modes"
    onPressed: function(button) {
      if (button === Qt.RightButton) Quickshell.execDetached(["voxtype", "record", "toggle"])
      else root.open()
    }
  }
}
