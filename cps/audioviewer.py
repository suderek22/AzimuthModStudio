"""Non-blocking bank sample preview and WAV export."""
import os
import shutil
import tempfile

from PySide6.QtCore import QProcess, QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                               QTreeWidget, QTreeWidgetItem, QFileDialog, QMessageBox)

from .audio import read_bank, pcm_wav, xma_wav, ffmpeg_path, CODECS


class BankViewer(QWidget):
    def __init__(self, name, data):
        super().__init__()
        self.samples = read_bank(data)
        self.temp = tempfile.TemporaryDirectory(prefix="cps-audio-")
        self.ready = None
        self.action = None
        self.closed = False
        self.decode_timed_out = False
        self.process = QProcess(self)
        self.process.finished.connect(self._decoded)
        self.process.errorOccurred.connect(self._process_error)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(120000)
        self.timeout.timeout.connect(self._timed_out)
        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.errorOccurred.connect(lambda _error, message: self._error(message))
        layout = QVBoxLayout(self)
        buttons = QHBoxLayout()
        self.play_btn = QPushButton("Play")
        self.play_btn.clicked.connect(lambda: self.request("play"))
        stop = QPushButton("Stop")
        stop.clicked.connect(self.stop)
        self.export_btn = QPushButton("Export WAV...")
        self.export_btn.clicked.connect(lambda: self.request("export"))
        buttons.addWidget(self.play_btn)
        buttons.addWidget(stop)
        buttons.addWidget(self.export_btn)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.info = QLabel("%s - %d samples. PCM16, mono/stereo XMA and MPEG decoding supported." %
                           (name, len(self.samples)))
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Sample", "Codec", "Rate", "Channels", "Seconds"])
        self.tree.setRootIsDecorated(False)
        for sample in self.samples:
            self.tree.addTopLevelItem(QTreeWidgetItem([sample.name, CODECS.get(sample.mode, str(sample.mode)),
                                                       str(sample.rate), str(sample.channels), "%.3f" % sample.seconds]))
        self.tree.setColumnWidth(0, 430)
        layout.addWidget(self.tree)
        self.tree.itemDoubleClicked.connect(lambda _item, _column: self.request("play"))
        if self.samples:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def _error(self, message):
        self.info.setText("Audio error: " + message)
        if not self.closed:
            QMessageBox.warning(self, "Audio bank", message)

    def stop(self):
        self.action = None
        self.player.stop()

    def request(self, action):
        index = self.tree.indexOfTopLevelItem(self.tree.currentItem())
        if index < 0:
            self._error("Select a sample first.")
            return
        if self.process.state() != QProcess.NotRunning:
            self._error("A sample is still decoding. Wait for it to finish.")
            return
        self.action = action
        self.index = index
        if self.ready == index:
            self._perform()
            return
        self.player.stop()
        self.player.setSource(QUrl())
        sample = self.samples[index]
        path = self._wav_path()
        try:
            if sample.mode == 2:
                with open(path, "wb") as f:
                    f.write(pcm_wav(sample))
                self.ready = index
                self._perform()
            else:
                if sample.mode not in (10, 11):
                    raise ValueError("%s playback/export is not supported; no WAV was written." %
                                     CODECS.get(sample.mode, "Codec %d" % sample.mode))
                executable = ffmpeg_path()
                source = os.path.join(self.temp.name, "source.bin")
                with open(source, "wb") as f:
                    f.write(xma_wav(sample) if sample.mode == 10 else sample.data)
                self.info.setText("Decoding %s..." % sample.name)
                self.decode_timed_out = False
                self.timeout.start()
                self.process.start(executable, ["-v", "error", "-y", "-i", source, "-c:a", "pcm_s16le", path])
        except (OSError, ValueError) as ex:
            self.action = None
            self._error(str(ex))

    def _wav_path(self):
        return os.path.join(self.temp.name, "sample_%d.wav" % self.index)

    def _process_error(self, error):
        if error == QProcess.FailedToStart:
            self.timeout.stop()
            self.action = None
            self._error(self.process.errorString())

    def _timed_out(self):
        self.action = None
        self.decode_timed_out = True
        self.process.kill()

    def _decoded(self, code, status):
        self.timeout.stop()
        if self.closed:
            return
        if self.decode_timed_out:
            self._error("Decoding exceeded 120 seconds.")
            return
        if code != 0 or status != QProcess.NormalExit:
            message = bytes(self.process.readAllStandardError()).decode("utf-8", "replace").strip()
            self.action = None
            self._error(message[-2000:] or "Decoder stopped before producing a WAV.")
            return
        self.ready = self.index
        self.info.setText("Decoded %s" % self.samples[self.index].name)
        self._perform()

    def _perform(self):
        action, self.action = self.action, None
        if action == "play":
            self.player.setSource(QUrl.fromLocalFile(self._wav_path()))
            self.player.play()
        elif action == "export":
            name = self.samples[self.index].name
            safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip(". ") or "sample"
            path, _ = QFileDialog.getSaveFileName(self, "Export sample as WAV", safe + ".wav", "WAV (*.wav)")
            if path:
                try:
                    shutil.copyfile(self._wav_path(), path)
                    self.info.setText("Exported " + path)
                except OSError as ex:
                    self._error(str(ex))

    def shutdown(self):
        self.closed = True
        self.timeout.stop()
        self.player.stop()
        self.player.setSource(QUrl())
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
            self.process.waitForFinished(3000)
        self.temp.cleanup()
