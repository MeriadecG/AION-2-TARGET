"""Moteur réseau partagé : un seul processus pour DPS + observation Target + familiers."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402

from aion_target.config import Config  # noqa: E402
from aion_target.engine_hub import EngineHub, engine_state  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeClient(QObject):
    status = pyqtSignal(dict)
    dps = pyqtSignal(dict)
    players = pyqtSignal(list)
    party = pyqtSignal(list)
    fight = pyqtSignal(dict)
    kill = pyqtSignal(dict)
    stopped = pyqtSignal(str)
    created = 0

    def __init__(self):
        super().__init__()
        FakeClient.created += 1
        self.starts = self.stops = 0
        self.publish = []
        self.last_status, self.ping_ms, self.path, self.running = {}, None, None, False

    def start(self):
        self.starts += 1
        self.running = True

    def stop(self):
        self.stops += 1
        self.running = False

    def set_publish_dps(self, on):
        self.publish.append(on)

    def reset(self):
        pass

    def set_mode(self, m):
        pass


def hub_with_fake():
    cfg = Config()
    cfg.save = lambda *a: None
    FakeClient.created = 0
    return EngineHub(cfg, lambda m: None, client_factory=FakeClient), cfg


def test_single_engine_for_all_modes(app):
    hub, _ = hub_with_fake()
    states = []
    hub.status.connect(lambda st: states.append(st["state"]))
    hub.set_mode("observe", True)
    c = hub.client
    assert c.running and c.publish[-1] is False              # observation seule : pas de DPS
    hub.set_mode("dps", True)
    assert FakeClient.created == 1 and c.starts == 2 and c.publish[-1] is True   # même moteur
    hub.set_mode("pets", True)
    hub.set_mode("dps", False)
    assert c.running and c.publish[-1] is False              # encore utile : pas d'arrêt
    hub.set_mode("observe", False)
    hub.set_mode("pets", False)
    assert not c.running and c.stops == 1 and hub.modes == set()
    assert states[-1] == "off"


def test_signals_are_forwarded_and_state_machine(app):
    hub, _ = hub_with_fake()
    got = {}
    hub.players.connect(lambda p: got.setdefault("players", p))
    hub.kill.connect(lambda k: got.setdefault("kill", k))
    hub.set_mode("observe", True)
    hub.client.players.emit([{"name": "Bob"}])
    hub.client.kill.emit({"verdict": "mine"})
    assert got == {"players": [{"name": "Bob"}], "kill": {"verdict": "mine"}}
    assert engine_state(True, {"capture": "running"}) == "searching"
    assert engine_state(True, {"capture": "running", "game_port": 13328}) == "active"
    assert engine_state(True, {"capture": "no_permission"}) == "no_permission"
    assert engine_state(True, {"capture": "running", "stopped": "code 1"}) == "stopped"
    assert engine_state(False, {"capture": "running"}) == "off"


def test_ui_sections_share_the_hub(app):
    from aion_target.control import ControlWindow  # noqa: F401  (styles)
    from aion_target.dps_ui import DpsTab
    from aion_target.history import History
    from aion_target.observe_ui import ObservationSection, counters_text
    from aion_target.prefetch import Prefetcher
    import tempfile
    from pathlib import Path
    hub, cfg = hub_with_fake()
    cfg.dps_warning_ack = True                               # avertissement déjà accepté

    def card(root, label, **kw):
        g = QtWidgets.QGridLayout()
        root.addLayout(g)
        return g
    hist = History(Path(tempfile.mkdtemp()) / "h.db", 100, 100)
    dps = DpsTab(cfg, hist, card, lambda m: None, hub)
    pf = Prefetcher(lambda: None, "eu", ["eu-siel"])
    obs = ObservationSection(cfg, hub, pf, card, lambda m: None)
    obs.chk.setChecked(True)
    assert cfg.observe_enabled and hub.modes == {"observe"}
    dps.chk.setChecked(True)
    assert hub.modes == {"observe", "dps"} and FakeClient.created == 1
    hub.client.status.emit({"capture": "running", "game_port": 13328})
    assert "port 13328" in obs.state.text() and "DPS meter" in obs.state.text()
    dps.chk.setChecked(False)
    assert hub.client.running                                 # l'observation le garde
    obs.chk.setChecked(False)
    assert not hub.client.running
    assert "PAS" not in counters_text(pf) and "pas la liste complète" in counters_text(pf)
