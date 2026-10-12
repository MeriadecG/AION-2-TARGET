"""Faux serveur org.freedesktop.Notifications (tests) : écrit chaque Notify reçu en JSON."""
import json, sys
from PyQt6.QtCore import QCoreApplication, QObject, pyqtClassInfo, pyqtSlot, pyqtProperty
from PyQt6.QtDBus import QDBusConnection

OUT = sys.argv[1]


@pyqtClassInfo("D-Bus Interface", "org.freedesktop.Notifications")
class Server(QObject):
    n = 0

    @pyqtSlot(result="QStringList")
    def GetCapabilities(self):
        return ["body", "persistence"]

    @pyqtSlot(result=str)
    def GetServerInformation(self):
        return "FakeNotify"

    @pyqtSlot(str, "uint", str, str, str, "QStringList", "QVariantMap", int, result="uint")
    def Notify(self, app, rid, icon, summary, body, actions, hints, timeout):
        Server.n += 1
        with open(OUT, "a") as f:
            f.write(json.dumps({"app": app, "rid": rid, "icon": icon, "summary": summary, "body": body,
                                "actions": list(actions), "hints": {k: str(v) for k, v in hints.items()},
                                "timeout": timeout}, ensure_ascii=False) + "\n")
        return Server.n


app = QCoreApplication([])
bus = QDBusConnection.sessionBus()
srv = Server()
assert bus.registerObject("/org/freedesktop/Notifications", srv,
                          QDBusConnection.RegisterOption.ExportAllSlots)
assert bus.registerService("org.freedesktop.Notifications")
print("ready", flush=True)
app.exec()
