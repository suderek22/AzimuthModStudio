"""Opt-in, asynchronous GitHub release notifications; never installs updates."""
import html
import json
import re
from functools import total_ordering

from PySide6.QtCore import QObject, QUrl, Signal, Qt
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from . import APP_VERSION

REPOSITORY = "https://github.com/suderek22/AzimuthModStudio"
LATEST_RELEASE = "https://api.github.com/repos/suderek22/AzimuthModStudio/releases/latest"


@total_ordering
class Version:
    def __init__(self, value: str):
        match = re.fullmatch(r"[vV]?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?", value.strip())
        if not match:
            raise ValueError("Unsupported release version: " + value)
        self.numbers = tuple(int(match[i]) for i in (1, 2, 3))
        self.prerelease = tuple(match[4].split(".")) if match[4] else ()
        if any(not part for part in self.prerelease):
            raise ValueError("Invalid prerelease version: " + value)

    def __eq__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return (self.numbers, self.prerelease) == (other.numbers, other.prerelease)

    def __lt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        if self.numbers != other.numbers:
            return self.numbers < other.numbers
        if not self.prerelease or not other.prerelease:
            return bool(self.prerelease) and not other.prerelease
        for left, right in zip(self.prerelease, other.prerelease):
            if left == right:
                continue
            if left.isdigit() and right.isdigit():
                return int(left) < int(right)
            if left.isdigit() != right.isdigit():
                return left.isdigit()
            return left < right
        return len(self.prerelease) < len(other.prerelease)


def newer_release(data: object, current: str = APP_VERSION) -> str | None:
    if not isinstance(data, dict) or not isinstance(data.get("tag_name"), str):
        raise ValueError("GitHub did not return a release tag.")
    if data.get("draft"):
        raise ValueError("GitHub returned an unpublished draft.")
    tag = data["tag_name"]
    return tag if Version(tag) > Version(current) else None


class UpdateChecker(QObject):
    busy_changed = Signal(bool)
    message = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.reply = None
        self.manual = False

    def check(self, manual=True):
        if self.reply is not None:
            self.message.emit("An update check is already running.")
            return
        self.manual = manual
        request = QNetworkRequest(QUrl(LATEST_RELEASE))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", ("CarPackStudio/" + APP_VERSION).encode("ascii"))
        request.setTransferTimeout(15000)
        self.reply = self.manager.get(request)
        self.reply.finished.connect(self._finished)
        self.busy_changed.emit(True)

    def _finished(self):
        reply, self.reply = self.reply, None
        manual = self.manual
        self.busy_changed.emit(False)
        try:
            code = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            if code == 404:
                message = "No published release is available on GitHub yet."
                self.message.emit(message)
                if manual:
                    QMessageBox.information(self.parent(), "Check updates", message)
                return
            if reply.error() != QNetworkReply.NoError:
                raise ValueError(reply.errorString())
            payload = bytes(reply.readAll())
            if len(payload) > 1024 * 1024:
                raise ValueError("GitHub release response is unexpectedly large.")
            tag = newer_release(json.loads(payload))
            if tag:
                self.message.emit("A newer version is available: " + tag)
                box = QMessageBox(self.parent())
                box.setWindowTitle("Update available")
                box.setTextFormat(Qt.RichText)
                box.setText("Version <b>%s</b> is available (installed: %s).<br>"
                            '<a href="%s">Open GitHub to download the newest version</a>.' %
                            (html.escape(tag), APP_VERSION, REPOSITORY))
                box.setTextInteractionFlags(Qt.TextBrowserInteraction)
                download = box.addButton("Open GitHub", QMessageBox.ActionRole)
                box.addButton(QMessageBox.Close)
                box.exec()
                if box.clickedButton() is download and not QDesktopServices.openUrl(QUrl(REPOSITORY)):
                    raise ValueError("Windows could not open the GitHub download page.")
            else:
                self.message.emit("No newer release is available.")
                if manual:
                    QMessageBox.information(self.parent(), "Check updates", "You have the latest release (%s)." % APP_VERSION)
        except (ValueError, UnicodeError) as ex:
            message = "Cannot check updates: %s" % ex
            self.message.emit(message)
            if manual:
                QMessageBox.warning(self.parent(), "Check updates", message)
        finally:
            reply.deleteLater()

    def shutdown(self):
        if self.reply is not None:
            self.reply.finished.disconnect(self._finished)
            self.reply.abort()
            self.reply.deleteLater()
            self.reply = None
