"""Native GTK 4 desktop app; GTK is only imported for the UI command."""
import datetime
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

from . import core

CSS = b"""
window { background: #151a1b; color: #ebf1eb; }
headerbar { background: #151a1b; border-bottom: 1px solid #303a38; }
.hero { font-size: 30px; font-weight: 700; }
.subtitle { color: #a2b3ad; font-size: 14px; }
.eyebrow { color: #91cfb3; font-size: 11px; font-weight: 700; letter-spacing: 2px; }
.card { background: #202827; border: 1px solid #35423e; border-radius: 14px; padding: 16px; }
.meta { color: #9bad9f; font-size: 11px; }
.transcript { font-size: 15px; }
button { border-radius: 9px; padding: 7px 12px; }
button.suggested-action, button.mode-selected { background: #a5dfbf; color: #12261c; }
entry, textview, textview text { background: #202827; color: #ebf1eb; }
.error { color: #f1c78d; }
.hint { color: #93a79c; font-size: 12px; }
"""


def label(text, css=None, wrap=False):
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_wrap(wrap)
    if css:
        widget.add_css_class(css)
    return widget


def box(spacing=12, horizontal=False):
    return Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL if horizontal else Gtk.Orientation.VERTICAL, spacing=spacing)


def button(text, callback, css=None):
    widget = Gtk.Button(label=text)
    if css:
        widget.add_css_class(css)
    widget.connect("clicked", lambda *_: callback())
    return widget


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="local.cleardictation.App")
        self.connect("activate", self.activate)
        self.window = None
        self.fingerprint = None
        self.refresh_busy = False

    def activate(self, *_):
        if self.window:
            self.window.present()
            return
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = Gtk.ApplicationWindow(application=self, title="Clear Dictation")
        self.window.set_default_size(740, 780)
        header = Gtk.HeaderBar()
        header.set_title_widget(label("Clear Dictation"))
        self.window.set_titlebar(header)
        main = box(18)
        for side in ("top", "bottom", "start", "end"):
            getattr(main, "set_margin_" + side)(24)
        self.window.set_child(main)
        main.append(label("YOUR WORDS, A LITTLE CLEARER", "eyebrow"))
        main.append(label("Speak freely.", "hero"))
        main.append(label(core.load_config()["shortcut_hint"] + " Your text stays on this device.", "subtitle", True))
        self.status_label = label("Checking local model…", "hint")
        main.append(self.status_label)

        settings = box(10)
        settings.add_css_class("card")
        top = box(horizontal=True)
        title = label("AI cleanup")
        title.set_hexpand(True)
        top.append(title)
        self.enabled = Gtk.Switch()
        self.enabled.set_active(core.load_config()["enabled"])
        self.enabled.connect("notify::active", self.set_enabled)
        top.append(self.enabled)
        settings.append(top)
        modes = box(8, True)
        self.mode_buttons = {}
        for mode, caption in (("natural", "Natural"), ("polished", "Polished"), ("literal", "Literal")):
            widget = button(caption, lambda mode=mode: self.set_mode(mode))
            widget.set_hexpand(True)
            self.mode_buttons[mode] = widget
            modes.append(widget)
        settings.append(modes)
        self.mode_hint = label("", "hint", True)
        settings.append(self.mode_hint)
        main.append(settings)
        self.update_mode(core.load_config()["mode"])

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        switcher = Gtk.StackSwitcher(stack=self.stack)
        switcher.set_halign(Gtk.Align.CENTER)
        main.append(switcher)
        main.append(self.stack)
        self.make_history()
        self.make_dictionary()
        self.make_playground()
        main.append(label("Original transcripts are kept locally for recovery. Last 100 entries.", "hint", True))
        self.window.present()
        GLib.timeout_add_seconds(2, self.refresh)
        self.refresh()

    def set_enabled(self, switch, *_):
        cfg = core.load_config()
        cfg["enabled"] = switch.get_active()
        core.save_config(cfg)

    def set_mode(self, mode):
        cfg = core.load_config()
        cfg["mode"] = mode
        core.save_config(cfg)
        self.update_mode(mode)

    def update_mode(self, mode):
        for name, widget in self.mode_buttons.items():
            if name == mode:
                widget.add_css_class("mode-selected")
            else:
                widget.remove_css_class("mode-selected")
        self.mode_hint.set_text({
            "natural": "Clean up false starts and filler while keeping your own voice.",
            "polished": "Smooth out grammar for clear, professional writing.",
            "literal": "Keep the transcript as spoken. Apply only your personal dictionary.",
        }[mode])

    def make_history(self):
        page = box()
        actions = box(horizontal=True)
        heading = label("Recent dictations")
        heading.set_hexpand(True)
        actions.append(heading)
        actions.append(button("Clear history", self.confirm_clear))
        page.append(actions)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self.history_box = box(10)
        scroll.set_child(self.history_box)
        page.append(scroll)
        self.stack.add_titled(page, "history", "History")

    def confirm_clear(self):
        dialog = Gtk.MessageDialog(transient_for=self.window, modal=True, text="Clear your dictation history?", secondary_text="This removes the locally saved originals and cleaned text.", buttons=Gtk.ButtonsType.OK_CANCEL)
        def respond(dialog, response):
            if response == Gtk.ResponseType.OK:
                core.clear_history()
                self.fingerprint = None
                self.refresh()
            dialog.destroy()
        dialog.connect("response", respond)
        dialog.present()

    def render_history(self, rows):
        fingerprint = [(r["id"], r["status"], r["output"]) for r in rows]
        if fingerprint == self.fingerprint:
            return
        self.fingerprint = fingerprint
        while child := self.history_box.get_first_child():
            self.history_box.remove(child)
        if not rows:
            card = box()
            card.add_css_class("card")
            card.append(label("Your next thought starts here.", "transcript"))
            card.append(label("Dictate in any app, or try the cleanup playground.\nYour original words will always be available here.", "subtitle", True))
            self.history_box.append(card)
        for row in rows:
            card = box(8)
            card.add_css_class("card")
            stamp = datetime.datetime.fromtimestamp(row["created"]).strftime("%b %d · %I:%M %p")
            state = {"cleaned": "Cleaned", "literal": "Literal", "fallback": "Original kept", "processing": "Processing / original saved"}.get(row["status"], "Original")
            card.append(label(f"{stamp}  ·  {state}  ·  {row['seconds']:.1f}s", "meta"))
            text = label(row["output"], "transcript", True)
            text.set_selectable(True)
            card.append(text)
            if row["detail"]:
                card.append(label(row["detail"], "error", True))
            actions = box(8, True)
            actions.append(button("Copy text", lambda text=row["output"]: self.copy(text)))
            actions.append(button("Copy original", lambda text=row["original"]: self.copy(text)))
            card.append(actions)
            original = Gtk.Expander(label="View original")
            original_text = label(row["original"], "hint", True)
            original_text.set_selectable(True)
            original.set_child(original_text)
            card.append(original)
            self.history_box.append(card)

    def copy(self, text):
        try:
            core.copy_text(text)
            self.status_label.set_text("Copied. Paste into the app of your choice.")
        except Exception:
            self.status_label.set_text("Could not copy. You can select and copy the text directly.")

    def make_dictionary(self):
        page = box()
        page.append(label("Personal dictionary", "transcript"))
        page.append(label("Teach spellings for names, products, and technical terms. Changes apply to the next dictation.", "hint", True))
        row = box(8, True)
        self.heard = Gtk.Entry(placeholder_text="Heard as, e.g. voice type")
        self.preferred = Gtk.Entry(placeholder_text="Write as, e.g. Voxtype")
        self.heard.set_hexpand(True)
        self.preferred.set_hexpand(True)
        row.append(self.heard)
        row.append(self.preferred)
        row.append(button("Add", self.add_word, "suggested-action"))
        page.append(row)
        self.dictionary_note = label("", "hint")
        page.append(self.dictionary_note)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        self.dictionary_box = box(8)
        scroll.set_child(self.dictionary_box)
        page.append(scroll)
        self.stack.add_titled(page, "dictionary", "Dictionary")
        self.render_dictionary()

    def add_word(self):
        heard, preferred = self.heard.get_text().strip(), self.preferred.get_text().strip()
        if not heard or not preferred:
            self.dictionary_note.set_text("Enter both the heard phrase and preferred spelling.")
            return
        cfg = core.load_config()
        cfg["dictionary"][heard] = preferred
        core.save_config(cfg)
        self.heard.set_text("")
        self.preferred.set_text("")
        self.dictionary_note.set_text("Saved for your next dictation.")
        self.render_dictionary()

    def remove_word(self, key):
        cfg = core.load_config()
        cfg["dictionary"].pop(key, None)
        core.save_config(cfg)
        self.render_dictionary()

    def render_dictionary(self):
        while child := self.dictionary_box.get_first_child():
            self.dictionary_box.remove(child)
        for heard, preferred in core.load_config()["dictionary"].items():
            row = box(10, True)
            row.add_css_class("card")
            text = label(f"{heard}  →  {preferred}", wrap=True)
            text.set_hexpand(True)
            row.append(text)
            row.append(button("Remove", lambda key=heard: self.remove_word(key)))
            self.dictionary_box.append(row)

    def make_playground(self):
        page = box()
        page.append(label("Try a thought before dictating", "transcript"))
        page.append(label("This only shows the result here; it never types into another app.", "hint", True))
        self.draft = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.draft.set_top_margin(12)
        self.draft.set_left_margin(12)
        self.draft.set_right_margin(12)
        self.draft.get_buffer().set_text("Let's meet Tuesday, actually Wednesday at 3 pm.")
        scroll = Gtk.ScrolledWindow(min_content_height=90)
        scroll.set_child(self.draft)
        page.append(scroll)
        self.try_button = button("Clean up text", self.try_cleanup, "suggested-action")
        page.append(self.try_button)
        self.preview = label("Your cleaned text will appear here.", "transcript", True)
        self.preview.set_selectable(True)
        page.append(self.preview)
        self.stack.add_titled(page, "try", "Try it")

    def try_cleanup(self):
        buf = self.draft.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        if not text.strip():
            return
        self.try_button.set_sensitive(False)
        self.try_button.set_label("Cleaning up…")
        def work():
            result = core.process(text)
            GLib.idle_add(self.finish_preview, result)
        threading.Thread(target=work, daemon=True).start()

    def finish_preview(self, result):
        self.preview.set_text(result)
        self.try_button.set_sensitive(True)
        self.try_button.set_label("Clean up text")
        self.refresh()

    def refresh(self):
        if self.refresh_busy:
            return True
        self.refresh_busy = True
        def work():
            try:
                rows, ready = core.recent_history(), core.health()
                GLib.idle_add(self.finish_refresh, rows, ready)
            except Exception:
                GLib.idle_add(self.finish_refresh, [], False)
        threading.Thread(target=work, daemon=True).start()
        return True

    def finish_refresh(self, rows, ready):
        self.refresh_busy = False
        self.status_label.set_text("● Local model ready · Audio and text stay on your laptop" if ready else "Local model is starting or unavailable · Original dictation is still safe")
        self.render_history(rows)


def run():
    App().run([])
