"""Native Windows desktop editor: preview, transcription, AI picks, multi-lane timeline and export."""
from __future__ import annotations
from pathlib import Path
from dataclasses import replace
from datetime import datetime
import copy
import os
import subprocess
import sys
import wave

from PySide6.QtCore import Qt, QThread, Signal, QObject, QUrl, QRectF, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QKeySequence, QShortcut, QBrush
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QFileDialog, QMessageBox, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QCheckBox, QPlainTextEdit, QProgressBar, QSlider, QGroupBox, QFormLayout, QInputDialog,
    QScrollArea, QMenu, QStatusBar
)
from .project import Project, Clip, Voice, TextOverlay, stamp
from .render import inspect, render, render_batch, PRESETS, export_duration, overlap_duration
from .transcription import transcribe, transcript_srt, WHISPER_MODELS
from .agent import discover
from .runtime import app_root, data_root, gguf_path, download, QWEN_URL, find_executable

STYLESHEET = """
QWidget { background:#101522; color:#e4e9f4; font: 10pt 'Segoe UI'; }
QMainWindow, QTabWidget::pane { background:#101522; }
QLabel#heading { font-size:18pt; font-weight:700; color:#f5f7ff; }
QLabel#subheading { color:#8897b4; }
QPushButton { background:#27344d; border:1px solid #3a4a65; border-radius:7px; padding:8px 12px; font-weight:600; }
QPushButton:hover { background:#364866; border-color:#71a8ff; }
QPushButton:disabled { color:#69768d; background:#182134; }
QPushButton#primary { background:#4675f5; border-color:#4675f5; color:white; }
QPushButton#primary:hover { background:#5a86ff; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox { background:#171f30; border:1px solid #35435e; border-radius:6px; padding:7px; selection-background-color:#456cea; }
QTableWidget { background:#151d2d; alternate-background-color:#1b2639; border:1px solid #2b3b57; gridline-color:#29354b; selection-background-color:#2c5180; }
QHeaderView::section { background:#23314b; color:#abbcdb; padding:7px; border:0; border-right:1px solid #30425e; }
QTabBar::tab { padding:10px 16px; color:#a1b1d0; background:#172139; border-radius:5px; margin-right:3px; }
QTabBar::tab:selected { color:#fff; background:#3054a2; }
QGroupBox { border:1px solid #2b3953; border-radius:8px; margin-top:12px; padding:14px 8px 8px 8px; }
QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 5px; color:#b4c8ee; }
QSlider::groove:horizontal { height:5px; background:#344158; border-radius:2px; }
QSlider::handle:horizontal { background:#6fa8ff; width:13px; margin:-4px 0; border-radius:6px; }
QProgressBar { background:#26344b; border:0; border-radius:5px; height:9px; text-align:center; }
QProgressBar::chunk { background:#477af5; border-radius:5px; }
"""


def button(text, action, primary=False, tip=""):
    b = QPushButton(text)
    b.clicked.connect(action)
    if primary: b.setObjectName("primary")
    if tip: b.setToolTip(tip)
    return b


def table(headers, stretch=-1):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.setAlternatingRowColors(True)
    t.horizontalHeader().setStretchLastSection(True)
    if stretch >= 0:
        t.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    return t


class Task(QObject):
    finished = Signal(object)
    failed = Signal(str)
    status = Signal(str)
    def __init__(self, task):
        super().__init__()
        self.task = task
    def execute(self):
        try:
            value = self.task(self.status.emit)
        except Exception as exc:
            import traceback
            self.failed.emit(f"{exc}\n\n{traceback.format_exc(limit=8)}")
        else:
            self.finished.emit(value)


class TimelineView(QWidget):
    """A visual multi-lane overview; edit precise boundaries in the table beneath."""
    jump = Signal(float)
    def __init__(self):
        super().__init__()
        self.project = Project()
        self.playhead = 0.
        self.setMinimumHeight(174)
        self.setMouseTracking(True)
        self.zoom = 1.
    def set_project(self, project):
        self.project = project
        self.update()
    def set_playhead(self, seconds):
        self.playhead = seconds
        self.update()
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#141d2e"))
        x0 = 65
        usable = max(1, self.width() - x0 - 10)
        total = max(1, export_duration(self.project), self.playhead)
        visible = max(10., total / self.zoom)
        px = usable / visible
        for n, (name, y, color) in enumerate([("VIDEO", 25, "#477af5"), ("VOICE", 65, "#ae74ed"),
                                               ("MUSIC", 105, "#3cb9aa"), ("TITLES", 145, "#d7a85e")]):
            p.fillRect(0, y-3, self.width(), 35, QColor("#1c293f" if n % 2 == 0 else "#172135"))
            p.setPen(QColor("#94a9d0"))
            p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            p.drawText(6, y+17, name)
        clock = 0.
        overlap = overlap_duration(self.project)
        for i, clip in enumerate(self.project.clips):
            if i: clock -= overlap
            width = max(2, clip.length * px)
            box = QRectF(x0 + clock * px, 29, width, 26)
            p.setPen(QPen(QColor("#7199ff"), 1))
            p.setBrush(QBrush(QColor("#3354a7")))
            p.drawRoundedRect(box, 3, 3)
            p.setPen(Qt.GlobalColor.white)
            p.drawText(box.adjusted(5, 0, -3, 0), Qt.AlignmentFlag.AlignVCenter,
                       f"{i+1} {clip.title[:26]}")
            clock += clip.length
        for voice in self.project.voices:
            p.fillRect(QRectF(x0 + voice.at * px, 70, max(3., 5*px), 25), QColor("#965de0"))
        if self.project.music_path:
            p.fillRect(QRectF(x0, 110, min(usable, total * px), 25), QColor("#2b968c"))
        for overlay in self.project.overlays:
            p.fillRect(QRectF(x0 + overlay.start * px, 150,
                             max(3., (overlay.end-overlay.start)*px), 24), QColor("#b57d35"))
        p.setPen(QPen(QColor("#ffcc6c"), 2))
        xhead = x0 + self.playhead * px
        p.drawLine(round(xhead), 0, round(xhead), self.height())
        p.end()
    def mousePressEvent(self, event):
        if event.position().x() < 65: return
        seconds = max(0, event.position().x()-65) / max(1, self.width()-75) * max(10, export_duration(self.project)/self.zoom)
        self.jump.emit(seconds)
    def wheelEvent(self, event):
        self.zoom = min(10., max(1., self.zoom * (1.2 if event.angleDelta().y() > 0 else 1/1.2)))
        self.update()


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.project = Project()
        self.candidates = []
        self.in_point, self.out_point = 0., 0.
        self.thread = None
        self.worker = None
        self.busy = False
        self.undo_states = []
        self.record_stream = None
        self.record_chunks = []
        self.setWindowTitle("ClipForge Studio  ·  Local AI Video Editor")
        self.resize(1580, 940)
        self.setMinimumSize(1020, 670)
        self.setStyleSheet(STYLESHEET)
        self.setAcceptDrops(True)
        self.setup()
        self.refresh()
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.auto_save)
        self.autosave_timer.start(60_000)
        QTimer.singleShot(350, self.offer_recovery)

    def setup(self):
        shell = QWidget()
        self.setCentralWidget(shell)
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(15, 12, 15, 10)
        head = QHBoxLayout()
        title = QLabel("CLIPFORGE  /  STUDIO")
        title.setObjectName("heading")
        head.addWidget(title)
        head.addWidget(QLabel("Private • On-device • Non-destructive"))
        head.addStretch()
        head.addWidget(button("Import video", self.import_video, True))
        head.addWidget(button("Open project", self.open_project))
        head.addWidget(button("Save", self.save_project))
        head.addWidget(button("Undo", self.undo))
        head.addWidget(button("Export MP4", self.export, True))
        head.addWidget(button("Batch Shorts", self.batch_export, False, "Each accepted excerpt becomes an individual MP4 + SRT + CSV manifest"))
        layout.addLayout(head)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0,0,5,0)
        self.video = QVideoWidget()
        self.video.setMinimumHeight(240)
        self.video.setStyleSheet("background:#04070d; border:1px solid #31405e; border-radius:8px;")
        lv.addWidget(self.video, 3)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(lambda _: self.update_time())
        self.audio.setVolume(0.9)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.sliderMoved.connect(self.scrub)
        lv.addWidget(self.seek)
        nav = QHBoxLayout()
        self.time_label = QLabel("00:00:00.000 / 00:00:00.000")
        nav.addWidget(button("−5s", lambda: self.player.setPosition(max(0,self.player.position()-5000))))
        self.play_button = button("▶ Play", self.toggle_play)
        nav.addWidget(self.play_button)
        nav.addWidget(button("+5s", lambda: self.player.setPosition(self.player.position()+5000)))
        nav.addWidget(self.time_label)
        nav.addStretch()
        lv.addLayout(nav)
        mark = QHBoxLayout()
        mark.addWidget(button("Mark IN  [I]", self.mark_in))
        self.in_label = QLabel("IN —")
        mark.addWidget(self.in_label)
        mark.addWidget(button("Mark OUT  [O]", self.mark_out))
        self.out_label = QLabel("OUT —")
        mark.addWidget(self.out_label)
        mark.addWidget(button("+ Add selected range", self.add_range, True))
        lv.addLayout(mark)
        lv.addWidget(QLabel("PROJECT TIMELINE  ·  click to preview, scroll to zoom"))
        self.timeline = TimelineView()
        self.timeline.jump.connect(self.jump_output)
        lv.addWidget(self.timeline)
        lv.addWidget(QLabel("Precise trim, reorder and split clips in the Timeline tab."))
        splitter.addWidget(left)

        self.tabs = QTabWidget()
        splitter.addWidget(self.tabs)
        splitter.setSizes([780, 740])
        self.build_transcript_tab()
        self.build_ai_tab()
        self.build_timeline_tab()
        self.build_titles_tab()
        self.build_audio_tab()
        self.build_settings_tab()
        self.build_log_tab()
        bottom = QHBoxLayout()
        self.state_label = QLabel("Ready. Import a video to start.")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setRange(0,100)
        self.progress.setValue(0)
        bottom.addWidget(self.state_label, 1)
        bottom.addWidget(self.progress)
        layout.addLayout(bottom)
        QShortcut(QKeySequence("Space"), self, activated=self.toggle_play)
        QShortcut(QKeySequence("I"), self, activated=self.mark_in)
        QShortcut(QKeySequence("O"), self, activated=self.mark_out)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save_project)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)

    def build_transcript_tab(self):
        pane = QWidget(); l = QVBoxLayout(pane)
        h = QHBoxLayout()
        h.addWidget(QLabel("Speech → timestamped editable notes"))
        h.addStretch()
        h.addWidget(button("Transcribe locally", self.start_transcribe, True))
        h.addWidget(button("Export SRT", self.save_srt))
        l.addLayout(h)
        self.transcript_filter = QLineEdit()
        self.transcript_filter.setPlaceholderText("Search transcript…")
        self.transcript_filter.textChanged.connect(self.filter_transcript)
        l.addWidget(self.transcript_filter)
        self.transcript_table = table(["Start", "End", "Transcript"], 2)
        self.transcript_table.cellDoubleClicked.connect(lambda r,_: self.play_at(self.project.segments[r].start))
        self.transcript_table.itemChanged.connect(self.edit_transcript)
        l.addWidget(self.transcript_table)
        l.addWidget(QLabel("Edit the text column to correct speech. Corrected segments retain time ranges but lose individual word timing."))
        self.tabs.addTab(pane, "Transcript")

    def build_ai_tab(self):
        pane = QWidget(); l = QVBoxLayout(pane)
        l.addWidget(QLabel("AI CLIP SCOUT  ·  two-pass proposal + critic, both on-device"))
        self.objective = QPlainTextEdit("Find funny moments, jokes, surprising insights, practical information, memorable debates and strong story payoffs. Prefer natural hooks and complete context; avoid misleading clickbait.")
        self.objective.setMaximumHeight(82)
        l.addWidget(self.objective)
        row = QHBoxLayout()
        row.addWidget(button("Discover highlights", self.start_analysis, True))
        row.addWidget(button("Add pick to timeline", self.accept_candidate))
        row.addWidget(button("Reject / learn preference", self.reject_candidate))
        l.addLayout(row)
        self.candidate_table = table(["Score", "Type", "Start", "End", "Title / Hook"], 4)
        self.candidate_table.cellDoubleClicked.connect(lambda r,_: self.play_at(self.candidates[r].start))
        l.addWidget(self.candidate_table)
        self.reason = QPlainTextEdit()
        self.reason.setReadOnly(True)
        self.reason.setMaximumHeight(106)
        self.candidate_table.itemSelectionChanged.connect(self.explain_candidate)
        l.addWidget(self.reason)
        l.addWidget(QLabel("AI suggestions are estimates from the transcript, not frame analysis. Preview every pick."))
        self.tabs.addTab(pane, "AI Picks")

    def build_timeline_tab(self):
        pane = QWidget(); l = QVBoxLayout(pane)
        row = QHBoxLayout()
        row.addWidget(button("↑", lambda:self.move_clip(-1)))
        row.addWidget(button("↓", lambda:self.move_clip(1)))
        row.addWidget(button("Split at playhead", self.split_clip))
        row.addWidget(button("Remove", self.remove_clip))
        l.addLayout(row)
        self.clip_table = table(["#", "Source IN (sec)", "OUT (sec)", "Length", "Clip title"], 4)
        self.clip_table.itemChanged.connect(self.edit_clip)
        self.clip_table.cellDoubleClicked.connect(self.open_clip)
        l.addWidget(self.clip_table)
        l.addWidget(QLabel("IN/OUT and title cells are editable. Time values always refer to original video."))
        self.timeline_length = QLabel("Timeline: 0 seconds")
        l.addWidget(self.timeline_length)
        self.tabs.addTab(pane, "Timeline")

    def build_titles_tab(self):
        pane = QWidget(); l = QVBoxLayout(pane)
        form = QFormLayout()
        self.text_entry = QLineEdit(); self.text_entry.setPlaceholderText("Hook, CTA, label, joke…")
        form.addRow("Text",self.text_entry)
        self.text_start = QDoubleSpinBox(); self.text_start.setRange(0,100000); self.text_start.setDecimals(2)
        self.text_end = QDoubleSpinBox(); self.text_end.setRange(0,100000); self.text_end.setDecimals(2); self.text_end.setValue(3)
        self.text_size = QSpinBox(); self.text_size.setRange(12,160); self.text_size.setValue(52)
        self.text_y = QComboBox(); self.text_y.addItems(["top","middle","bottom"])
        form.addRow("Timeline start",self.text_start);form.addRow("Timeline end",self.text_end)
        form.addRow("Font size",self.text_size);form.addRow("Position",self.text_y)
        l.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(button("Add overlay", self.add_overlay, True)); row.addWidget(button("Remove selected",self.remove_overlay))
        l.addLayout(row)
        self.overlay_table = table(["Start","End","Position","Text"],3)
        l.addWidget(self.overlay_table)
        self.tabs.addTab(pane,"Titles")

    def build_audio_tab(self):
        pane=QWidget(); l=QVBoxLayout(pane)
        l.addWidget(QLabel("VOICEOVER TRACK  ·  microphone recording or offline Windows speech"))
        self.vo_text=QPlainTextEdit(); self.vo_text.setPlaceholderText("Write voiceover script…")
        self.vo_text.setMaximumHeight(90); l.addWidget(self.vo_text)
        f=QFormLayout()
        self.vo_at=QDoubleSpinBox(); self.vo_at.setRange(0,100000); self.vo_at.setDecimals(2)
        self.vo_gain=QDoubleSpinBox(); self.vo_gain.setRange(0,4); self.vo_gain.setSingleStep(.1); self.vo_gain.setValue(1)
        f.addRow("Start on final timeline (s)",self.vo_at);f.addRow("Volume (0–4)",self.vo_gain)
        l.addLayout(f)
        row=QHBoxLayout()
        row.addWidget(button("Generate offline voice", self.generate_voice))
        self.record_button=button("● Start mic recording",self.toggle_record)
        row.addWidget(self.record_button)
        row.addWidget(button("Import audio", self.import_voice))
        l.addLayout(row)
        self.voice_table=table(["At","Gain","Audio file"],2)
        l.addWidget(self.voice_table)
        l.addWidget(button("Delete selected voiceover",self.remove_voice))
        group=QGroupBox("Background music")
        fg=QFormLayout(group)
        self.music_line=QLineEdit(); self.music_line.setReadOnly(True)
        fg.addRow("Music file",self.music_line)
        fg.addRow("",button("Choose music",self.import_music))
        self.music_level=QDoubleSpinBox(); self.music_level.setRange(0,1); self.music_level.setDecimals(2)
        self.music_level.setSingleStep(.05);self.music_level.setValue(.12)
        self.music_level.valueChanged.connect(self.set_music_gain)
        fg.addRow("Music volume",self.music_level)
        self.source_level=QDoubleSpinBox();self.source_level.setRange(0,4);self.source_level.setValue(1.)
        self.source_level.setSingleStep(.1)
        self.source_level.valueChanged.connect(lambda v:setattr(self.project,"source_volume",v))
        fg.addRow("Original audio volume",self.source_level)
        self.duck=QCheckBox("Lower original audio while voiceovers speak")
        self.duck.setChecked(True)
        self.duck.toggled.connect(lambda v:setattr(self.project,"duck_original",v))
        fg.addRow("Ducking",self.duck)
        l.addWidget(group)
        self.tabs.addTab(pane,"Audio / VO")

    def build_settings_tab(self):
        pane=QWidget(); scrolling=QScrollArea(); scrolling.setWidgetResizable(True)
        outer=QVBoxLayout(pane);outer.addWidget(scrolling)
        content=QWidget();scrolling.setWidget(content); l=QVBoxLayout(content)
        model_group=QGroupBox("Local models and transcribing")
        mf=QFormLayout(model_group)
        self.whisper=QComboBox();self.whisper.addItems(WHISPER_MODELS);self.whisper.setCurrentText("small")
        self.whisper.currentTextChanged.connect(lambda v: setattr(self.project,"transcript_model",v))
        self.language=QComboBox();self.language.addItems(["auto","en","es","fr","de","it","pt","ja","ko","zh"])
        self.backend=QComboBox();self.backend.addItems(["llama.cpp","Ollama"])
        self.backend.currentTextChanged.connect(lambda v: setattr(self.project,"analysis_backend",v))
        self.model_path=QLineEdit(); self.model_path.setPlaceholderText("Leave blank to use local Qwen GGUF")
        self.ollama_model=QLineEdit("qwen2.5:1.5b")
        mf.addRow("Whisper model",self.whisper);mf.addRow("Spoken language",self.language)
        mf.addRow("Clip AI engine",self.backend);mf.addRow("Custom GGUF file",self.model_path)
        mf.addRow("Ollama local model name",self.ollama_model)
        mf.addRow("",button("Browse GGUF",self.browse_gguf))
        mf.addRow("",button("Download local Qwen 1.5B (≈1 GB)",self.download_gguf))
        mf.addRow("",button("Check installed tools",self.check_tools))
        l.addWidget(model_group)
        export_group=QGroupBox("Video export")
        ef=QFormLayout(export_group)
        self.aspect=QComboBox();self.aspect.addItems(PRESETS.keys())
        self.framing=QComboBox();self.framing.addItems(["blur","crop","pad"])
        self.transition=QComboBox();self.transition.addItems(["cut","fade"])
        self.transition_time=QDoubleSpinBox();self.transition_time.setRange(.05,2);self.transition_time.setDecimals(2);self.transition_time.setValue(.3)
        self.captions=QCheckBox("Burn captions into video");self.captions.setChecked(True)
        self.caption_size=QSpinBox();self.caption_size.setRange(10,72);self.caption_size.setValue(20)
        self.crf=QSpinBox();self.crf.setRange(15,32);self.crf.setValue(21)
        ef.addRow("Aspect ratio",self.aspect);ef.addRow("Reframe",self.framing)
        ef.addRow("Transitions",self.transition);ef.addRow("Transition duration",self.transition_time)
        ef.addRow("Captions",self.captions);ef.addRow("Subtitle font size",self.caption_size)
        ef.addRow("H.264 quality (lower = better)",self.crf)
        l.addWidget(export_group)
        l.addWidget(QLabel("Video/audio stay on this PC. Internet is used only if you choose to download models."))
        l.addStretch(1)
        self.tabs.addTab(pane,"Settings")

    def build_log_tab(self):
        pane=QWidget();l=QVBoxLayout(pane)
        self.logs=QPlainTextEdit();self.logs.setReadOnly(True);l.addWidget(self.logs)
        l.addWidget(button("Open portable data folder",lambda: self.show_folder(data_root())))
        self.tabs.addTab(pane,"Jobs / Logs")

    def notify(self, message):
        self.state_label.setText(message[:180])
        self.logs.appendPlainText(f"[{datetime.now():%H:%M:%S}] {message}")
        # Avoid a megabyte of FFmpeg log filling the UI.
        if self.logs.blockCount() > 1200:
            self.logs.setPlainText("\n".join(self.logs.toPlainText().splitlines()[-600:]))

    def task(self, label, work, on_success):
        if self.busy:
            QMessageBox.information(self,"Already working","Finish the current task before starting another.")
            return
        self.busy=True; self.progress.setRange(0,0)
        self.notify(label)
        thread=QThread(self)
        worker=Task(work)
        worker.moveToThread(thread)
        thread.started.connect(worker.execute)
        worker.status.connect(self.notify)
        def done(value):
            try: on_success(value)
            finally: self._end_task()
        def failure(error):
            self.notify("ERROR: " + error.splitlines()[0])
            QMessageBox.critical(self,"Operation failed",error[:6000])
            self._end_task()
        worker.finished.connect(done)
        worker.failed.connect(failure)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda:setattr(self,"thread",None))
        self.worker=worker;self.thread=thread
        thread.start()
    def _end_task(self):
        self.busy=False; self.progress.setRange(0,100);self.progress.setValue(100)

    def checkpoint(self):
        self.undo_states.append(copy.deepcopy(self.project))
        self.undo_states=self.undo_states[-40:]
    def undo(self):
        if not self.undo_states: return
        self.project=self.undo_states.pop()
        self.refresh()
        self.notify("Last edit undone")

    def import_video(self):
        file,_=QFileDialog.getOpenFileName(self,"Open video","","Video (*.mp4 *.mkv *.mov *.webm *.avi *.m4v *.ts);;All files (*)")
        if not file: return
        self.load_video_path(file)

    def load_video_path(self,file):
        def loaded(info):
            self.checkpoint()
            self.project=Project(video_path=file,duration=info["duration"])
            self.player.setSource(QUrl.fromLocalFile(file))
            self.refresh();self.notify(f"Imported: {Path(file).name} · {stamp(info['duration'])}")
        self.task("Inspecting media…",lambda report:inspect(file),loaded)

    def save_project(self):
        self.sync_settings()
        file=self.project.project_file
        if not file:
            file,_=QFileDialog.getSaveFileName(self,"Save ClipForge project",str(data_root()/"Untitled.clipforge.json"),"ClipForge Project (*.clipforge.json)")
        if not file:return
        try:
            self.project.save(file)
            self.recovery_path().unlink(missing_ok=True)
            self.notify("Saved project: " + file)
        except Exception as ex: QMessageBox.critical(self,"Save failed",str(ex))
    def open_project(self):
        file,_=QFileDialog.getOpenFileName(self,"Open project", "", "ClipForge Project (*.clipforge.json);;JSON (*.json)")
        if not file:return
        try:
            project=Project.load(file)
            self.checkpoint();self.project=project;self.candidates=[]
            if Path(project.video_path).exists():
                self.player.setSource(QUrl.fromLocalFile(project.video_path))
            else:
                QMessageBox.warning(self,"Missing source","The original video moved. Choose its new location to relink this project.")
                replacement,_=QFileDialog.getOpenFileName(self,"Locate missing source video","","Video (*.mp4 *.mkv *.mov *.webm *.avi *.m4v *.ts);;All files (*)")
                if replacement:
                    self.project.video_path=replacement
                    self.player.setSource(QUrl.fromLocalFile(replacement))
            self.refresh();self.notify("Opened project: " + Path(file).name)
        except Exception as ex: QMessageBox.critical(self,"Load failed",str(ex))

    def refresh(self):
        self.transcript_table.blockSignals(True)
        self.transcript_table.setRowCount(0)
        for i,s in enumerate(self.project.segments):
            self.transcript_table.insertRow(i)
            for j,value in enumerate([stamp(s.start),stamp(s.end),s.text]):
                item=QTableWidgetItem(value)
                if j!=2:item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.transcript_table.setItem(i,j,item)
        self.transcript_table.blockSignals(False)
        self.filter_transcript(self.transcript_filter.text())
        self.clip_table.blockSignals(True)
        self.clip_table.setRowCount(0)
        for i,c in enumerate(self.project.clips):
            self.clip_table.insertRow(i)
            for j,value in enumerate([str(i+1),f"{c.start:.3f}",f"{c.end:.3f}",f"{c.length:.2f}",c.title]):
                item=QTableWidgetItem(value)
                if j in (0,3):item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.clip_table.setItem(i,j,item)
        self.clip_table.blockSignals(False)
        self.timeline_length.setText(f"Timeline length: {stamp(max(0,export_duration(self.project)))} · {len(self.project.clips)} clips")
        self.timeline.set_project(self.project)
        self.overlay_table.setRowCount(0)
        for i,item in enumerate(self.project.overlays):
            self.overlay_table.insertRow(i)
            for j,v in enumerate([f"{item.start:.2f}",f"{item.end:.2f}",item.y,item.text]):
                self.overlay_table.setItem(i,j,QTableWidgetItem(v))
        self.voice_table.setRowCount(0)
        for i,item in enumerate(self.project.voices):
            self.voice_table.insertRow(i)
            for j,v in enumerate([f"{item.at:.2f}",f"{item.gain:.2f}",Path(item.path).name]):
                self.voice_table.setItem(i,j,QTableWidgetItem(v))
        self.music_line.setText(self.project.music_path)
        self.music_level.blockSignals(True);self.music_level.setValue(self.project.music_volume);self.music_level.blockSignals(False)
        self.source_level.blockSignals(True);self.source_level.setValue(self.project.source_volume);self.source_level.blockSignals(False)
        self.duck.blockSignals(True);self.duck.setChecked(self.project.duck_original);self.duck.blockSignals(False)
        self.whisper.setCurrentText(self.project.transcript_model)
        self.backend.setCurrentText(self.project.analysis_backend)
        self.model_path.setText(self.project.analysis_model)
        self.ollama_model.setText(self.project.ollama_model)
        self.aspect.setCurrentText(self.project.aspect)
        self.framing.setCurrentText(self.project.framing)
        self.transition.setCurrentText(self.project.transition)
        self.transition_time.setValue(self.project.transition_seconds)
        self.captions.setChecked(self.project.captions)
        self.caption_size.setValue(self.project.caption_size)
        self.crf.setValue(self.project.export_crf)
        self.show_candidates()

    def edit_transcript(self,item):
        row=item.row()
        if item.column()!=2 or not 0<=row<len(self.project.segments):return
        corrected=item.text().strip()
        if not corrected or corrected==self.project.segments[row].text:return
        self.checkpoint()
        self.project.segments[row].text=corrected
        # Word alignment is no longer valid; fallback caption uses full segment.
        self.project.segments[row].words=[]
        self.notify(f"Corrected transcript segment {row+1}; captions will use its segment-level timing")

    def filter_transcript(self,text):
        needle=text.casefold()
        for i,s in enumerate(self.project.segments):
            self.transcript_table.setRowHidden(i,needle not in s.text.casefold())

    def update_time(self):
        pos=self.player.position();length=self.player.duration()
        self.time_label.setText(f"{stamp(pos/1000)} / {stamp(length/1000)}")
        self.seek.blockSignals(True);self.seek.setValue(int(pos/max(1,length)*1000));self.seek.blockSignals(False)
    def on_position(self,_):
        self.update_time()
        source_at=self.player.position()/1000
        clock=0.
        overlap=overlap_duration(self.project)
        for i,clip in enumerate(self.project.clips):
            if i:clock-=overlap
            if clip.start<=source_at<clip.end:
                self.timeline.set_playhead(clock+source_at-clip.start)
                return
            clock+=clip.length
    def scrub(self,v):self.player.setPosition(round(self.player.duration()*v/1000))
    def toggle_play(self):
        if self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause();self.play_button.setText("▶ Play")
        else:
            self.player.play();self.play_button.setText("❚❚ Pause")
    def play_at(self,t):
        self.player.setPosition(round(t*1000));self.player.play();self.play_button.setText("❚❚ Pause")
    def mark_in(self):
        self.in_point=self.player.position()/1000;self.in_label.setText(stamp(self.in_point))
    def mark_out(self):
        self.out_point=self.player.position()/1000;self.out_label.setText(stamp(self.out_point))
    def add_range(self):
        if self.out_point-self.in_point<.25:
            QMessageBox.information(self,"Select a range","Seek through the source and press I for IN and O for OUT.")
            return
        self.add_clip(Clip(self.in_point,self.out_point,"Manual selection"))
    def add_clip(self,clip):
        if clip.end>self.project.duration + .1 or clip.start<0 or clip.end<=clip.start:return
        self.checkpoint();self.project.clips.append(copy.deepcopy(clip));self.refresh()
        self.tabs.setCurrentIndex(2);self.notify("Added to editable timeline: " + clip.title)
    def jump_output(self,t):
        transition=overlap_duration(self.project)
        cursor=0.
        for i,c in enumerate(self.project.clips):
            if i:cursor-=transition
            if cursor<=t<cursor+c.length:
                self.play_at(c.start+t-cursor);self.timeline.set_playhead(t);return
            cursor+=c.length
    def edit_clip(self,item):
        row=item.row();col=item.column()
        if not 0<=row<len(self.project.clips):return
        if col not in (1,2,4):return
        clip=self.project.clips[row]
        try:
            value=item.text()
            if col in (1,2):
                number=float(value)
                start=number if col==1 else clip.start
                end=number if col==2 else clip.end
                if not (0<=start<end<=self.project.duration+.1):raise ValueError("Range invalid")
                self.checkpoint();clip.start=start;clip.end=end
            else:
                self.checkpoint();clip.title=value.strip() or "Untitled highlight"
            self.refresh()
        except Exception:
            self.notify("Invalid edit; keeping previous clip values")
            self.refresh()
    def selected_clip(self):return self.clip_table.currentRow()
    def move_clip(self,delta):
        row=self.selected_clip();to=row+delta
        if row<0 or not 0<=to<len(self.project.clips):return
        self.checkpoint();self.project.clips[row],self.project.clips[to]=self.project.clips[to],self.project.clips[row]
        self.refresh();self.clip_table.selectRow(to)
    def remove_clip(self):
        row=self.selected_clip()
        if 0<=row<len(self.project.clips):
            self.checkpoint();self.project.clips.pop(row);self.refresh()
    def split_clip(self):
        row=self.selected_clip()
        if not 0<=row<len(self.project.clips):return
        clip=self.project.clips[row];at=self.player.position()/1000
        if not clip.start+.25<at<clip.end-.25:
            self.notify("Playhead must be inside selected clip to split")
            return
        self.checkpoint();self.project.clips[row:row+1]=[replace(clip,end=at,title=clip.title+" · A"),replace(clip,start=at,title=clip.title+" · B")]
        self.refresh()
    def open_clip(self,row,_col):
        if 0<=row<len(self.project.clips):self.play_at(self.project.clips[row].start)

    def start_transcribe(self):
        if not self.project.video_path:
            QMessageBox.information(self,"Video needed","Import a video first.");return
        model=self.whisper.currentText();language=self.language.currentText()
        def success(value):
            self.checkpoint();self.project.segments=value[0];self.project.transcript_model=model
            self.refresh();self.tabs.setCurrentIndex(0)
            self.notify(f"Transcription ready: {len(value[0])} segments · language {value[1]}")
        self.task("Running offline Whisper transcription…",
                  lambda log: transcribe(self.project.video_path,model,language,log),success)
    def save_srt(self):
        if not self.project.segments:return
        file,_=QFileDialog.getSaveFileName(self,"Save transcript captions", "transcript.srt", "SRT (*.srt)")
        if file:
            Path(file).write_text(transcript_srt(self.project.segments),encoding="utf-8-sig")
            self.notify("Transcript SRT exported")

    def start_analysis(self):
        if not self.project.segments:
            QMessageBox.information(self,"Transcript needed","Transcribe a video first.");return
        self.sync_settings()
        snapshot=copy.deepcopy(self.project)
        goal=self.objective.toPlainText().strip()[:900]
        def success(clips):
            self.candidates=clips;self.show_candidates();self.tabs.setCurrentIndex(1)
            if not clips:QMessageBox.information(self,"No picks","The model returned no usable candidates. Try a better model or more explicit prompt. Detailed reasons in Jobs / Logs.")
        self.task("Local AI is searching and reviewing highlights…",
                  lambda log: discover(snapshot,snapshot.analysis_backend,snapshot.analysis_model,snapshot.ollama_model,goal,log),success)
    def show_candidates(self):
        self.candidate_table.setRowCount(0)
        for i,c in enumerate(self.candidates):
            self.candidate_table.insertRow(i)
            vals=[str(c.score),c.category,stamp(c.start),stamp(c.end),c.title+(" — "+c.hook if c.hook else "")]
            for j,value in enumerate(vals):
                item=QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.candidate_table.setItem(i,j,item)
    def explain_candidate(self):
        row=self.candidate_table.currentRow()
        if 0<=row<len(self.candidates):
            c=self.candidates[row]
            self.reason.setPlainText(f"{c.title} | {c.length:.1f} s | score {c.score}/100\n{c.reason}\nSuggested hook: {c.hook}")
    def accept_candidate(self):
        row=self.candidate_table.currentRow()
        if 0<=row<len(self.candidates):
            c=self.candidates.pop(row);self.add_clip(c)
    def reject_candidate(self):
        row=self.candidate_table.currentRow()
        if 0<=row<len(self.candidates):
            self.checkpoint();c=self.candidates.pop(row)
            self.project.rejected.append(c.title)
            self.show_candidates();self.notify("Rejection preference retained in project for next AI pass")

    def add_overlay(self):
        if not self.text_entry.text().strip() or self.text_end.value()<=self.text_start.value():return
        self.checkpoint();self.project.overlays.append(TextOverlay(self.text_entry.text().strip(),
            self.text_start.value(),self.text_end.value(),self.text_size.value(),y=self.text_y.currentText()))
        self.refresh()
    def remove_overlay(self):
        row=self.overlay_table.currentRow()
        if 0<=row<len(self.project.overlays):
            self.checkpoint();self.project.overlays.pop(row);self.refresh()
    def import_voice(self):
        file,_=QFileDialog.getOpenFileName(self,"Add voiceover","","Audio (*.wav *.mp3 *.m4a *.aac *.flac);;All files (*)")
        if file:self.register_voice(file)
    def register_voice(self,file):
        self.checkpoint();self.project.voices.append(Voice(file,self.vo_at.value(),self.vo_gain.value()))
        self.refresh();self.notify("Added VO: " +Path(file).name)
    def remove_voice(self):
        row=self.voice_table.currentRow()
        if 0<=row<len(self.project.voices):
            self.checkpoint();self.project.voices.pop(row);self.refresh()
    def import_music(self):
        file,_=QFileDialog.getOpenFileName(self,"Add background music","","Audio (*.wav *.mp3 *.m4a *.aac *.flac);;All files (*)")
        if file:self.checkpoint();self.project.music_path=file;self.refresh()
    def set_music_gain(self,value):self.project.music_volume=value

    def generate_voice(self):
        script=self.vo_text.toPlainText().strip()
        if not script:return
        if os.name!="nt":
            QMessageBox.warning(self,"Windows voice only","Offline SAPI speech generation is available when running on Windows.");return
        target=data_root()/"voiceovers"/f"tts-{datetime.now():%Y%m%d-%H%M%S}.wav"
        target.parent.mkdir(parents=True,exist_ok=True)
        # Encoded PowerShell avoids command line quote injection and handles Unicode.
        ps=("Add-Type -AssemblyName System.Speech; "
            "$v=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$v.SetOutputToWaveFile('{str(target).replace(chr(39),chr(39)*2)}'); "
            f"$v.Speak('{script.replace(chr(39),chr(39)*2)}'); $v.Dispose()")
        encoded=__import__("base64").b64encode(ps.encode("utf-16le")).decode("ascii")
        def speak(log):
            from .runtime import run
            log("Generating speech with local Windows SAPI voice…")
            run(["powershell.exe","-NoProfile","-NonInteractive","-EncodedCommand",encoded])
            if not target.exists():raise RuntimeError("Voice file was not created")
            return str(target)
        self.task("Rendering Windows offline voiceover…",speak,self.register_voice)

    def toggle_record(self):
        if self.record_stream is not None:
            self.stop_recording();return
        try:
            import sounddevice as sd
            import numpy as np
            self.record_chunks=[]
            def callback(indata,frames,time_info,status):
                self.record_chunks.append(indata.copy())
            self.record_stream=sd.InputStream(samplerate=44100,channels=1,dtype="int16",callback=callback)
            self.record_stream.start()
            self.record_button.setText("■ Stop and save recording")
            self.notify("Microphone recording started (44.1 kHz, mono)")
        except Exception as ex:
            self.record_stream=None
            QMessageBox.critical(self,"Microphone failed",str(ex))
    def stop_recording(self):
        import numpy as np
        try:self.record_stream.stop();self.record_stream.close()
        finally:self.record_stream=None;self.record_button.setText("● Start mic recording")
        if not self.record_chunks:return
        samples=np.concatenate(self.record_chunks,axis=0)
        self.record_chunks=[]
        target=data_root()/"voiceovers"/f"mic-{datetime.now():%Y%m%d-%H%M%S}.wav"
        target.parent.mkdir(parents=True,exist_ok=True)
        with wave.open(str(target),"wb") as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(44100);w.writeframes(samples.tobytes())
        self.register_voice(str(target))

    def browse_gguf(self):
        file,_=QFileDialog.getOpenFileName(self,"Choose on-device GGUF",str(data_root()/"models"),"GGUF (*.gguf)")
        if file:self.model_path.setText(file)
    def download_gguf(self):
        path=gguf_path()
        if path.exists():
            self.notify("Local model already downloaded: "+str(path));return
        self.task("Downloading selected local LLM model (≈1 GB)…",
                  lambda log:download(QWEN_URL,path,log),lambda _:self.notify("Local GGUF ready: "+str(path)))
    def check_tools(self):
        report=[]
        for name in ["ffmpeg","ffprobe","llama-server"]:
            try:report.append(name+": "+find_executable(name))
            except Exception as ex:report.append(name+": MISSING ("+str(ex)+")")
        report.append("GGUF model: "+("Ready" if gguf_path().exists() else "Not downloaded"))
        QMessageBox.information(self,"Local tools", "\n\n".join(report))
    def sync_settings(self):
        p=self.project
        p.transcript_model=self.whisper.currentText()
        p.analysis_backend=self.backend.currentText()
        p.analysis_model=self.model_path.text().strip()
        p.ollama_model=self.ollama_model.text().strip()
        p.aspect=self.aspect.currentText();p.framing=self.framing.currentText()
        p.transition=self.transition.currentText();p.transition_seconds=self.transition_time.value()
        p.captions=self.captions.isChecked();p.caption_size=self.caption_size.value()
        p.export_crf=self.crf.value();p.music_volume=self.music_level.value()
        p.source_volume=self.source_level.value();p.duck_original=self.duck.isChecked()
    def export(self):
        self.sync_settings()
        if not self.project.clips:
            QMessageBox.information(self,"No clips","Add clips from AI Picks or the video preview first.");return
        file,_=QFileDialog.getSaveFileName(self,"Export edited short-form MP4",str(data_root()/"export.mp4"),"MP4 (*.mp4)")
        if not file:return
        snapshot=copy.deepcopy(self.project)
        def ready(result):
            self.notify("Export complete: "+str(result))
            QMessageBox.information(self,"Export complete",f"Video saved:\n{result}\n\nCaptions sidecar saved as .srt")
        self.task("Preparing offline MP4 export…",lambda log:render(snapshot,file,log),ready)
    def batch_export(self):
        self.sync_settings()
        if not self.project.clips:
            QMessageBox.information(self,"No clips","Accept or create excerpts in the Timeline first.")
            return
        directory=QFileDialog.getExistingDirectory(self,"Choose folder for individual shorts",str(data_root()))
        if not directory:return
        snapshot=copy.deepcopy(self.project)
        def completed(files):
            self.notify(f"Batch finished: {len(files)} individual MP4 shorts")
            QMessageBox.information(self,"Batch export complete",
                f"Exported {len(files)} MP4 shorts, subtitle files and clip-manifest.csv to:\n{directory}")
        self.task("Batch-exporting individual shorts…",lambda log:render_batch(snapshot,directory,log),completed)

    def recovery_path(self):
        return data_root()/"autosave.clipforge.json"
    def auto_save(self):
        if self.busy or not self.project.video_path:return
        try:
            snapshot=copy.deepcopy(self.project)
            snapshot.save(str(self.recovery_path()))
        except Exception as exc:
            self.notify("Auto-save could not write recovery copy: "+str(exc))
    def offer_recovery(self):
        backup=self.recovery_path()
        if not backup.is_file():return
        try:
            recovered=Project.load(str(backup))
            if not recovered.video_path or not Path(recovered.video_path).is_file():return
            reply=QMessageBox.question(self,"Recover unsaved work?",
                "A local recovery snapshot was found. Restore clips and edits from the previous session?",
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if reply==QMessageBox.StandardButton.Yes:
                recovered.project_file=""
                self.project=recovered
                self.player.setSource(QUrl.fromLocalFile(recovered.video_path))
                self.refresh();self.notify("Unsaved session recovered. Save the project to a new file.")
            else:backup.unlink(missing_ok=True)
        except Exception as exc:self.notify("Cannot open recovery copy: "+str(exc))

    def dragEnterEvent(self,event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
    def dropEvent(self,event):
        files=[url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        media_extensions={".mp4",".mkv",".mov",".webm",".avi",".m4v",".ts"}
        media=next((p for p in files if Path(p).suffix.lower() in media_extensions),None)
        if media:
            self.load_video_path(media)
            event.acceptProposedAction()

    def show_folder(self,path):
        path=Path(path);path.mkdir(exist_ok=True,parents=True)
        if os.name=="nt":os.startfile(str(path))
        else:subprocess.Popen(["xdg-open",str(path)])
    def closeEvent(self,event):
        if self.busy:
            QMessageBox.warning(self,"Active job","Finish your active export/transcription before closing.")
            event.ignore();return
        if self.record_stream:self.stop_recording()
        self.player.stop()
        event.accept()


def main():
    app=QApplication(sys.argv)
    app.setApplicationName("ClipForge Studio")
    app.setOrganizationName("ClipForge")
    window=Studio();window.show()
    return app.exec()
