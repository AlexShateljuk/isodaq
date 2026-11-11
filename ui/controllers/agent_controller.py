"""AgentController — remote serial ports through ``isodaq-agent`` (AGT2, AGT3).

Pairs with agents, lists the ports of the paired ones into the target field (↻
with the Agent transport selected), and owns the "Remote agents" dialog. Every
network call runs on a worker thread: an agent that is switched off must not
freeze the window for the seconds a connect takes to give up.
"""
from __future__ import annotations

import socket
import threading

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

import ui.main_window as _win   # for theme-updated colour globals (runtime access only)
from core import agent_client
from core.agent_client import AgentError, TrustStore, make_url, split_address
from core.i18n import tr
from isodaq_agent import protocol
from ui.number_box import NumberBox

class _Task(QObject):
    """Runs one blocking call on a daemon thread and reports back on the GUI thread
    (a queued signal). Not a QThread: closing the window while an unreachable agent
    is still timing out must not destroy a running QThread."""
    done = pyqtSignal(object, object)     # (result, exception or None)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def start(self) -> None:
        threading.Thread(target=self._run, name="agent-task", daemon=True).start()

    def _run(self) -> None:
        try:
            result, error = self._fn(), None
        except Exception as e:               # reported to the GUI, not raised here
            result, error = None, e
        try:
            self.done.emit(result, error)
        except RuntimeError:                 # the window closed meanwhile
            pass


def describe_error(e: Exception) -> str:
    if isinstance(e, AgentError):
        return str(e)
    return str(e) or e.__class__.__name__


class AgentController(QObject):
    def __init__(self, mw, store: TrustStore | None = None):
        super().__init__(mw)
        self._mw = mw
        self.store = store or TrustStore()
        self._tasks: set = set()
        #: Ports found at the last scan, by agent ("host:port" → [port info]).
        self.found: dict[str, list[dict]] = {}
        self._scanning = 0

    def run(self, fn, on_done) -> _Task:
        task = _Task(fn, self)
        self._tasks.add(task)

        def finish(result, error, t=task):
            self._tasks.discard(t)
            t.deleteLater()
            on_done(result, error)
        task.done.connect(finish, Qt.ConnectionType.QueuedConnection)
        task.start()
        return task

    def agents(self) -> list[dict]:
        """Paired agents, each with ``host`` and ``port``."""
        out = []
        for key, entry in sorted(self.store.all().items()):
            if not isinstance(entry, dict):
                continue
            host, port = entry.get("host"), entry.get("port")
            if not host or not isinstance(port, int):
                try:
                    host, port = split_address(key)
                except ValueError:
                    continue
            out.append({**entry, "host": host, "port": port})
        return out

    # ── Port list (↻) ─────────────────────────────────────────────────────────

    def scan(self) -> None:
        mw = self._mw
        agents = self.agents()
        if not agents:
            mw._log("SYS", tr("No agents yet: Device → Set up a Raspberry Pi… installs one over SSH."),
                    _win.C_SYS)
            self.open_dialog()
            return
        if self._scanning:
            return
        self.found = {}
        self._scanning = len(agents)
        mw._refresh_btn.setEnabled(False)
        mw._log("SYS", tr("Asking {count} agent(s) for their ports…").format(
            count=len(agents)), _win.C_DIM)
        for a in agents:
            self.run(lambda a=a: agent_client.list_ports(a["host"], a["port"], self.store),
                     lambda result, err, a=a: self._on_ports(a, result, err))

    def _on_ports(self, agent: dict, result, err) -> None:
        mw = self._mw
        self._scanning -= 1
        if self._scanning <= 0:
            mw._refresh_btn.setEnabled(True)
        label = agent.get("name") or agent["host"]
        if err is not None:
            mw._log("ERR", f"{label}: {describe_error(err)}", _win.C_ERR)
            return
        name, ports = result
        infos = []
        for p in ports:
            desc = p.get("description") or ""
            if p.get("busy"):
                desc = tr("{desc}  (in use by {client})").format(desc=desc,
                                                                  client=p["busy"]).strip()
            infos.append({"device": make_url(agent["host"], agent["port"], p["device"]),
                          "description": f"{name}  {desc}".strip(),
                          "vid": p.get("vid"), "pid": p.get("pid"),
                          "serial": p.get("serial")})
        self.found[TrustStore.key(agent["host"], agent["port"])] = infos
        mw._log("SYS", tr("{agent}: {count} port(s).").format(agent=name, count=len(ports)),
                _win.C_DIM)
        if mw._transport_combo.currentText() == "Agent":
            self.populate_combo()

    def populate_combo(self) -> None:
        from ui.port_list import fill
        mw = self._mw
        typed = mw._port_combo.currentText().strip() or mw._agent_url
        infos = [i for items in self.found.values() for i in items]
        fill(mw._port_combo, infos, "")
        if not infos:
            mw._port_combo.clear()
        if typed:
            mw._port_combo.setEditText(typed)
        elif infos:
            mw._port_combo.setCurrentIndex(0)

    # ── Pairing ───────────────────────────────────────────────────────────────

    def pair_dialog(self, address: str = "", then_connect: bool = False) -> bool:
        dlg = PairDialog(self, address, self._mw)
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        if ok and then_connect and not self._mw._reader.isRunning():
            self._mw._serial.toggle_connection()
        return ok

    def setup_dialog(self) -> bool:
        """Install the agent on a Pi over SSH, pair, and show its ports."""
        dlg = SetupDialog(self, self._mw)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        mw = self._mw
        if not mw._reader.isRunning():
            mw._transport_combo.setCurrentText("Agent")
            mw._port_combo.setEditText("")
            self.scan()
        return True

    def forget(self, host: str, port: int) -> None:
        self.store.forget(host, port)
        self.found.pop(TrustStore.key(host, port), None)

    def open_dialog(self) -> None:
        AgentsDialog(self, self._mw).exec()


class PairDialog(QDialog):
    def __init__(self, controller: AgentController, address: str = "", parent=None):
        super().__init__(parent)
        self._controller = controller
        self.entry: dict | None = None
        self.setWindowTitle(tr("Pair with agent"))
        self.setMinimumWidth(480)
        root = QVBoxLayout(self)
        hint = QLabel(tr("Run isodaq-agent pair on the agent: it prints a one-time code, "
                         "valid for 10 minutes."))
        hint.setWordWrap(True)
        root.addWidget(hint)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.address = QLineEdit(address)
        self.address.setPlaceholderText(tr("e.g. raspberrypi.local or 192.168.1.40"))
        form.addRow(tr("Agent address"), self.address)
        self.code = QLineEdit()
        self.code.setPlaceholderText(tr("e.g. K7QM-2XRA"))
        self.code.setMaxLength(12)
        form.addRow(tr("Pairing code"), self.code)
        self.name = QLineEdit(socket.gethostname())
        form.addRow(tr("This computer's name"), self.name)
        root.addLayout(form)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.pair_btn = buttons.addButton(tr("Pair"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.pair_btn.setDefault(True)
        self.pair_btn.clicked.connect(self._on_pair)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        (self.code if address else self.address).setFocus()

    def _error(self, text: str) -> None:
        self.status.setStyleSheet(f"color:{_win.C_ERR.name()}")
        self.status.setText(text)

    def _on_pair(self) -> None:
        try:
            host, port = split_address(self.address.text())
        except ValueError as e:
            self._error(str(e))
            return
        code = protocol.normalise_code(self.code.text())
        if len(code) != protocol.CODE_LENGTH:
            self._error(tr("The code has {n} letters and digits, like ABCD-EFGH.").format(
                n=protocol.CODE_LENGTH))
            return
        self.pair_btn.setEnabled(False)
        self.status.setStyleSheet("")
        self.status.setText(tr("Pairing with {host}…").format(host=host))
        name = self.name.text().strip()
        store = self._controller.store
        self._controller.run(lambda: agent_client.pair(host, port, code, store, name),
                             self._on_done)

    def _on_done(self, entry, err) -> None:
        self.pair_btn.setEnabled(True)
        if err is not None:
            self._error(describe_error(err))
            return
        self.entry = entry
        self._controller._mw._log("SYS", tr(
            "Paired with agent {agent} ({address}). Fingerprint {fp}").format(
            agent=entry["name"], address=f"{entry['host']}:{entry['port']}",
            fp=protocol.show_fingerprint(entry["fingerprint"])), _win.C_OK)
        self.accept()


class AgentsDialog(QDialog):
    COLUMNS = ("Agent", "Address", "Fingerprint", "Paired")

    def __init__(self, controller: AgentController, parent=None):
        super().__init__(parent)
        self._controller = controller
        self.setWindowTitle(tr("Remote agents"))
        self.setMinimumSize(640, 320)
        root = QVBoxLayout(self)
        intro = QLabel(tr("Serial ports on other computers, through isodaq-agent. Set up a "
                          "Raspberry Pi installs it over SSH and pairs. Then pick the Agent "
                          "transport and press ↻ to list the ports."))
        intro.setWordWrap(True)
        intro.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(intro)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in self.COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        for col in (0, 1, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table)
        row = QHBoxLayout()
        self.setup_btn = QPushButton(tr("Set up a Raspberry Pi…"))
        self.setup_btn.setToolTip(tr("Install the agent on a Pi over SSH and pair with it"))
        self.setup_btn.clicked.connect(self._on_setup)
        self.pair_btn = QPushButton(tr("Pair with a running agent…"))
        self.pair_btn.clicked.connect(self._on_pair)
        self.forget_btn = QPushButton(tr("Forget"))
        self.forget_btn.setToolTip(tr("Remove it here. To stop it accepting this computer, "
                                      "also run isodaq-agent revoke on the agent."))
        self.forget_btn.clicked.connect(self._on_forget)
        row.addWidget(self.setup_btn)
        row.addWidget(self.pair_btn)
        row.addWidget(self.forget_btn)
        row.addStretch(1)
        root.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.table.itemSelectionChanged.connect(self._sync)
        self._agents: list[dict] = []
        self.refresh()

    def refresh(self) -> None:
        self._agents = self._controller.agents()
        self.table.setRowCount(len(self._agents))
        for r, a in enumerate(self._agents):
            values = (a.get("name") or a["host"], f"{a['host']}:{a['port']}",
                      protocol.show_fingerprint(a.get("fingerprint", "")), a.get("paired", ""))
            for c, value in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(value))
        self._sync()

    def _sync(self) -> None:
        self.forget_btn.setEnabled(bool(self.table.selectedItems()))

    def _on_pair(self) -> None:
        if self._controller.pair_dialog():
            self.refresh()

    def _on_setup(self) -> None:
        if SetupDialog(self._controller, self).exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _on_forget(self) -> None:
        rows = {i.row() for i in self.table.selectedItems()}
        for r in rows:
            a = self._agents[r]
            self._controller.forget(a["host"], a["port"])
        self.refresh()


class SetupDialog(QDialog):
    """Address, user and password: IsoDAQ does the rest over SSH (AGT7)."""
    progress = pyqtSignal(str)

    def __init__(self, controller: AgentController, parent=None):
        super().__init__(parent)
        self._controller = controller
        self.entry: dict | None = None
        self._busy = False
        self.setWindowTitle(tr("Set up a Raspberry Pi"))
        self.setMinimumWidth(520)
        root = QVBoxLayout(self)
        intro = QLabel(tr(
            "IsoDAQ connects to the Pi over SSH, installs isodaq-agent as a service that "
            "starts at boot, and pairs with it. SSH must be on (Raspberry Pi Imager: "
            "Enable SSH). The password is used for this only and isn't saved."))
        intro.setWordWrap(True)
        root.addWidget(intro)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.host = QLineEdit()
        self.host.setPlaceholderText(tr("e.g. raspberrypi.local or 192.168.1.40"))
        form.addRow(tr("Pi address"), self.host)
        self.user = QLineEdit()
        self.user.setPlaceholderText(tr("e.g. pi"))
        form.addRow(tr("User"), self.user)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText(tr("blank uses your SSH key"))
        form.addRow(tr("Password"), self.password)
        self.more_btn = QPushButton(tr("More options") + "  ▸")
        self.more_btn.setCheckable(True)
        form.addRow("", self.more_btn)
        # Extra rows of the same form, so labels and fields line up.
        self.ssh_port = NumberBox()
        self.ssh_port.setDecimals(0)
        self.ssh_port.setRange(1, 65535)
        self.ssh_port.setValue(22)
        self.ssh_port.setFixedWidth(90)
        self.key_path = QLineEdit()
        self.key_path.setPlaceholderText(tr("default keys and ssh-agent"))
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("the Pi's host name"))
        self.agent_port = NumberBox()
        self.agent_port.setDecimals(0)
        self.agent_port.setRange(1, 65535)
        self.agent_port.setValue(protocol.DEFAULT_PORT)
        self.agent_port.setFixedWidth(90)
        self._more_rows = []
        for label, field in ((tr("SSH port"), self.ssh_port), (tr("SSH key file"), self.key_path),
                             (tr("Agent name"), self.name), (tr("Agent port"), self.agent_port)):
            form.addRow(label, field)
            self._more_rows.append(field)
            form.setRowVisible(field, False)
        self._form = form
        self.more_btn.toggled.connect(self._toggle_more)
        root.addLayout(form)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.go = buttons.addButton(tr("Install and connect"),
                                    QDialogButtonBox.ButtonRole.AcceptRole)
        self.go.setDefault(True)
        self.go.clicked.connect(lambda: self._start(""))
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.progress.connect(self._show_progress)
        self.host.setFocus()

    def _toggle_more(self, on: bool) -> None:
        for field in self._more_rows:
            self._form.setRowVisible(field, on)
        self.more_btn.setText(tr("More options") + ("  ▾" if on else "  ▸"))
        self.adjustSize()

    def _show_progress(self, text: str) -> None:
        self.status.setStyleSheet("")
        self.status.setText(tr(text))

    def _error(self, text: str) -> None:
        self.status.setStyleSheet(f"color:{_win.C_ERR.name()}")
        self.status.setText(text)

    def reject(self) -> None:
        if self._busy:                       # the Pi is mid-install: let it finish
            self._error(tr("Still installing: wait for it to finish."))
            return
        super().reject()

    def _start(self, accept_key: str) -> None:
        from core import agent_setup
        host = self.host.text().strip()
        if not host:
            self._error(tr("Enter the Pi's address."))
            return
        name = self.name.text().strip()
        try:
            agent_setup.install_command(int(self.agent_port.value()), name)
        except agent_setup.SetupError as e:
            self._error(str(e))
            return
        params = {"host": host, "ssh_port": int(self.ssh_port.value()),
                  "user": self.user.text().strip(), "password": self.password.text(),
                  "key_path": self.key_path.text().strip(), "name": name,
                  "agent_port": int(self.agent_port.value()), "accept_key": accept_key}
        self._busy = True
        self.go.setEnabled(False)
        self._show_progress("Connecting over SSH…")
        store = self._controller.store
        self._controller.run(lambda: deploy(params, store, self.progress.emit), self._on_done)

    def _on_done(self, entry, err) -> None:
        from core import agent_setup
        self._busy = False
        self.go.setEnabled(True)
        if isinstance(err, agent_setup.UnknownHostKey):
            answer = QMessageBox.question(self, tr("New SSH host"), tr(
                "This is the first SSH connection to {host}. Its {kind} key is:\n\n{fp}\n\n"
                "Continue if you trust this network. To be sure, compare it on the Pi with:\n"
                "ssh-keygen -lf /etc/ssh/ssh_host_{short}_key.pub").format(
                host=err.host, kind=err.key_type, fp=err.fingerprint,
                short=_key_file(err.key_type)))
            if answer == QMessageBox.StandardButton.Yes:
                self._start(err.fingerprint)
            else:
                self._error(tr("Stopped: the SSH host key wasn't accepted."))
            return
        if err is not None:
            self._error(describe_error(err))
            return
        self.entry = entry
        self._controller._mw._log("SYS", tr(
            "Installed isodaq-agent on {host} and paired. Fingerprint {fp}").format(
            host=entry["name"], fp=protocol.show_fingerprint(entry["fingerprint"])),
            _win.C_OK)
        self.accept()


def _key_file(key_type: str) -> str:
    return {"ssh-ed25519": "ed25519", "ssh-rsa": "rsa"}.get(
        key_type, "ecdsa" if key_type.startswith("ecdsa") else "*")


def deploy(params: dict, store: TrustStore, progress) -> dict:
    """Install over SSH, then pair (blocking: run on a worker thread)."""
    import time

    from core import agent_setup
    session = agent_setup.SSHSession(params["host"], params["ssh_port"], params["user"],
                                     params["password"], params["key_path"],
                                     params["accept_key"])
    try:
        code, _ = agent_setup.install(session, params["agent_port"], params["name"],
                                      params["password"], progress)
    finally:
        session.close()
    progress("Pairing…")
    for attempt in range(10):                # the service has only just (re)started
        try:
            return agent_client.pair(params["host"], params["agent_port"], code, store,
                                     socket.gethostname())
        except AgentError as e:
            if e.code not in ("unreachable", "closed", "timeout") or attempt == 9:
                raise
            time.sleep(1)
    raise AgentError("unreachable", "The agent didn't start")
