"""SettingsManager — persist and restore UI state, and the Preferences dialog.

Extracted from MainWindow (OSS6). Pure persistence: it reads/writes the various
MainWindow widgets and state via the ``mw`` reference — no state of its own. The
config path stays a MainWindow class attribute (``mw._CONFIG_PATH``) so existing
callers and tests keep working.
"""
from __future__ import annotations

import json

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QComboBox

from core import i18n
from ui.themes import tint_titlebar


class SettingsManager(QObject):
    """Loads/saves ~/.isodaq_studio/config.json and owns the Preferences dialog."""

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw

    def load(self) -> None:
        """Restore all UI controls from config.json. Silently ignored on first
        run or if the file is corrupt."""
        mw = self._mw
        try:
            data: dict = json.loads(mw._CONFIG_PATH.read_text())
        except Exception:
            return

        def _set_combo(combo: QComboBox, key: str):
            if key in data:
                idx = combo.findText(str(data[key]))
                if idx >= 0:
                    combo.setCurrentIndex(idx)

        _set_combo(mw._baud_combo,   "baud")
        _set_combo(mw._data_combo,   "data")
        _set_combo(mw._flow_combo,   "flow")
        _set_combo(mw._parser_combo, "parser")
        _set_combo(mw._eol_combo,    "eol")

        # Port is restored after _refresh_ports(); store for deferred apply
        mw._restore_port = data.get("port", "")

        if "prefix" in data:    mw._prefix_edit.setText(data["prefix"])
        if "sep"    in data:    mw._sep_edit.setText(data["sep"])

        if "timestamp"  in data: mw._chk_ts.setChecked(bool(data["timestamp"]))
        if "hex"        in data: mw._chk_hex.setChecked(bool(data["hex"]))
        if "autoscroll" in data: mw._chk_auto.setChecked(bool(data["autoscroll"]))
        if "echo"       in data: mw._chk_echo.setChecked(bool(data["echo"]))

        if "font_size" in data:
            mw._terminal_font_size = max(8, min(24, int(data["font_size"])))
            font = QFont("JetBrains Mono", mw._terminal_font_size)
            mw._terminal.setFont(font)
            mw._terminal.document().setDefaultFont(font)
            mw._font_size_lbl.setText(str(mw._terminal_font_size))

        if "scrollback" in data:
            mw._scrollback_limit = max(100, min(50000, int(data["scrollback"])))

        if "theme" in data and data["theme"] in ("light", "vscode"):
            mw._apply_theme(data["theme"])

        if "colorizer" in data:
            mw._log_colorizer_enabled = set(data["colorizer"])

        if "channels" in data:
            mw._parser.from_dict_list(data["channels"])
            mw._parse_panel._rebuild()
            mw._parse_panel.sync_display_panels()

        if "snippet" in data and data["snippet"]:
            mw._parser.set_snippet(data["snippet"])
            mw._parse_panel.load_snippet(data["snippet"])

        if "macros" in data:
            # _macro_panel is built during _build_ui which runs before load()
            mw._macro_panel.from_dict_list(data["macros"])

        if "indicator_thresholds" in data:
            mw._indicator_panel.set_thresholds(data["indicator_thresholds"])

        if "sections" in data:
            for key, is_collapsed in data["sections"].items():
                cs = mw._sidebar_sections.get(key)
                if cs is not None and cs.collapsed != is_collapsed:
                    cs.toggle()

        if "mode" in data:
            mw._set_mode(data["mode"])

        if "signaling_url" in data:
            mw._signaling_url = str(data["signaling_url"])

        if "language" in data:
            mw._language = str(data["language"])   # applied at next launch (main.py)

    def save(self) -> None:
        """Persist all UI state to config.json."""
        mw = self._mw
        try:
            mw._CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            mw._CONFIG_PATH.write_text(json.dumps({
                "port":       mw._port_combo.currentText(),
                "baud":       mw._baud_combo.currentText(),
                "data":       mw._data_combo.currentText(),
                "flow":       mw._flow_combo.currentText(),
                "eol":        mw._eol_combo.currentText(),
                "parser":     mw._parser_combo.currentText(),
                "prefix":     mw._prefix_edit.text(),
                "sep":        mw._sep_edit.text(),
                "timestamp":  mw._chk_ts.isChecked(),
                "hex":        mw._chk_hex.isChecked(),
                "autoscroll": mw._chk_auto.isChecked(),
                "echo":       mw._chk_echo.isChecked(),
                "font_size":  mw._terminal_font_size,
                "scrollback": mw._scrollback_limit,
                "theme":      mw._current_theme,
                "colorizer":  list(mw._log_colorizer_enabled),
                "channels":   mw._parser.to_dict_list(),
                "snippet":    mw._parser.get_snippet(),
                "macros":     mw._macro_panel.to_dict_list(),
                "sections":   {k: v.collapsed for k, v in mw._sidebar_sections.items()},
                "indicator_thresholds": mw._indicator_panel.get_thresholds(),
                "mode":          mw._mode,
                "signaling_url": mw._signaling_url,
                "language":      mw._language,
            }, indent=2))
        except Exception:
            import traceback
            traceback.print_exc()   # visible in terminal during development

    def open_preferences(self) -> None:
        """Open the Preferences dialog (scrollback limit, signaling URL)."""
        from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout,
                                     QLabel, QLineEdit, QSpinBox)
        mw = self._mw

        dlg = QDialog(mw)
        tint_titlebar(dlg)
        dlg.setWindowTitle(i18n.tr("Preferences"))
        dlg.setMinimumWidth(380)
        dlg.setModal(True)

        form = QFormLayout(dlg)
        form.setContentsMargins(16, 16, 16, 12)
        form.setSpacing(10)

        lang_combo = QComboBox()
        for code in i18n.available_languages():
            lang_combo.addItem(i18n.language_name(code), code)
        cur = lang_combo.findData(mw._language)
        if cur >= 0:
            lang_combo.setCurrentIndex(cur)
        lbl_lang = QLabel(i18n.tr("Language (restart to apply)"))
        lbl_lang.setObjectName("dimLabel")
        form.addRow(lbl_lang, lang_combo)

        scrollback_spin = QSpinBox()
        scrollback_spin.setRange(100, 50000)
        scrollback_spin.setSingleStep(500)
        scrollback_spin.setValue(mw._scrollback_limit)
        scrollback_spin.setSuffix(i18n.tr("  lines"))

        lbl_sb = QLabel(i18n.tr("Terminal scrollback limit"))
        lbl_sb.setObjectName("dimLabel")
        form.addRow(lbl_sb, scrollback_spin)

        sig_edit = QLineEdit()
        sig_edit.setPlaceholderText("https://your-relay.railway.app")
        sig_edit.setText(mw._signaling_url)
        lbl_sig = QLabel(i18n.tr("Signaling server URL"))
        lbl_sig.setObjectName("dimLabel")
        form.addRow(lbl_sig, sig_edit)

        sig_hint = QLabel(i18n.tr(
            "Deploy relay/server.py to Railway/Render once — all users share the same URL."
        ))
        sig_hint.setWordWrap(True)
        sig_hint.setObjectName("dimLabel")
        form.addRow(sig_hint)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        btns.button(QDialogButtonBox.StandardButton.Ok).setObjectName("save")
        form.addRow(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            mw._scrollback_limit = scrollback_spin.value()
            mw._signaling_url = sig_edit.text().strip()
            mw._language = lang_combo.currentData()
            self.save()
