"""主窗口：拖拽 / 批量队列 / 进度 / 日志 / 报告导出。

视觉约定：全局浅色主题；菜单栏与下拉菜单强制白底黑字，不跟随系统深色模式。
"""
from __future__ import annotations

import html
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction, QColor, QDesktopServices, QIcon, QKeySequence, QPalette,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget,
)

from unpacker import __version__, detector, registry, report
from unpacker.engine import EngineOptions
from unpacker.models import ConflictPolicy, ExtractResult, ExtractStatus
from unpacker.utils import human_size

from .worker import UnpackWorker

_COL_NAME, _COL_KIND, _COL_SIZE, _COL_STATUS, _COL_COUNT, _COL_NOTE = range(6)

#: 成员名编码下拉项：(显示文本, 传给引擎的值)
_ENCODING_CHOICES: tuple[tuple[str, str], ...] = (
    ("自动检测（推荐）", "auto"),
    ("UTF-8", "utf-8"),
    ("GBK / GB2312", "gbk"),
    ("Big5（繁体）", "big5"),
    ("Shift-JIS（日文）", "shift_jis"),
    ("CP437（DOS 西文）", "cp437"),
)

# 浅色主题：菜单栏 / 下拉菜单显式指定白底黑字，避免继承系统深色模式。
# 设计语言：圆角卡片 + 浅渐变 + 柔和分区；主色 #2f6fed，强调蓝紫渐变。
_STYLE = """
* { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 13px; }

QMainWindow, #root {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #f7f9fc, stop:1 #eef1f7);
}
QLabel { color: #1f2328; background: transparent; }
QLabel#title { font-size: 19px; font-weight: 700; color: #0f172a; letter-spacing: .2px; }
QLabel#subtitle { color: #64748b; }
QLabel#hint  { color: #64748b; }
QLabel#empty { color: #94a3b8; }

/* ---------- 卡片（面板 / 折叠组） ---------- */
QFrame#panel, QFrame#collapse {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ffffff, stop:1 #fafbfe);
    border: 1px solid #e3e8f0;
    border-radius: 12px;
}
QFrame#collapseHeader {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #f5f8ff, stop:1 #eef3fc);
    border-top-left-radius: 11px;
    border-top-right-radius: 11px;
    border-bottom: 1px solid #e9eef6;
}
QLabel#collapseTitle, QLabel#panelTitle {
    font-weight: 600; color: #334155; font-size: 13px; background: transparent;
}
QLabel#collapseSub, QLabel#panelSub {
    color: #8494ab; font-size: 12px; background: transparent;
}
QToolButton#collapseToggle {
    background: #ffffff; border: 1px solid #d6dfeb; border-radius: 6px;
    color: #47597a; font-size: 11px; font-weight: 700; padding: 0px;
}
QToolButton#collapseToggle:hover { background: #e9f1ff; border-color: #a9c0f5; }
QToolButton#collapseToggle:checked { background: #e9f1ff; border-color: #a9c0f5; }

/* ---------- 菜单栏：白底黑字 ---------- */
QMenuBar { background: #ffffff; color: #1f2328; border-bottom: 1px solid #e4e7ec;
           padding: 2px 6px; }
QMenuBar::item { background: transparent; color: #1f2328; padding: 6px 12px;
                 border-radius: 6px; }
QMenuBar::item:selected { background: #eef2fb; color: #1f2328; }
QMenuBar::item:pressed { background: #e3ebff; color: #1f2328; }

/* ---------- 下拉菜单：白底黑字 ---------- */
QMenu { background: #ffffff; color: #1f2328; border: 1px solid #d6dae1;
        border-radius: 9px; padding: 5px; }
QMenu::item { background: transparent; color: #1f2328; padding: 7px 26px 7px 14px;
              border-radius: 6px; }
QMenu::item:selected { background: #e8efff; color: #14508c; }
QMenu::item:disabled { color: #a8adb7; }
QMenu::separator { height: 1px; background: #eceef2; margin: 5px 10px; }

/* ---------- 按钮 ---------- */
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ffffff, stop:1 #f0f3f9);
    border: 1px solid #d6dfeb; border-radius: 8px;
    padding: 7px 16px; color: #1f2328;
}
QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #f5f9ff, stop:1 #e6efff);
    border-color: #a9c0f5;
}
QPushButton:pressed { background: #e3ebff; }
QPushButton:disabled { color: #a0a6b1; background: #f4f5f7; border-color: #e6e8ec; }
QPushButton#primary {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #3d7bf2, stop:1 #2f6fed);
    border: 1px solid #2f6fed; color: #ffffff; font-weight: 600;
}
QPushButton#primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #4a85f5, stop:1 #3574f0);
}
QPushButton#primary:pressed { background: #2760d4; }
QPushButton#primary:disabled { background: #b7c8f2; border-color: #b7c8f2; color: #ffffff; }
QPushButton#danger { color: #c0392b; border-color: #f0b8b3; }
QPushButton#danger:hover { background: #fff5f4; border-color: #e08d87; }

/* ---------- 输入框 / 下拉框 ---------- */
QLineEdit, QComboBox {
    background: #ffffff; border: 1px solid #d6dfeb; border-radius: 8px;
    padding: 7px 11px; color: #1f2328;
    selection-background-color: #cfe0ff; selection-color: #1f2328;
}
QLineEdit:hover, QComboBox:hover { border-color: #b8c9ef; }
QLineEdit:focus, QComboBox:focus { border: 2px solid #2f6fed; padding: 6px 10px; }
QLineEdit:disabled, QComboBox:disabled { background: #f4f5f7; color: #9aa0aa; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: none; border-left: 4px solid transparent;
                        border-right: 4px solid transparent;
                        border-top: 5px solid #6b7280; width: 0; height: 0;
                        margin-right: 8px; }
QComboBox QAbstractItemView { background: #ffffff; color: #1f2328;
                              border: 1px solid #d6dae1; border-radius: 8px;
                              padding: 4px; outline: none;
                              selection-background-color: #e8efff;
                              selection-color: #14508c; }

/* ---------- 复选框 ---------- */
QCheckBox { color: #1f2328; spacing: 7px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #c8ced8;
                       border-radius: 5px; background: #ffffff; }
QCheckBox::indicator:hover { border-color: #2f6fed; }
QCheckBox::indicator:checked { background: #2f6fed; border-color: #2f6fed; }

/* ---------- 旧 QGroupBox 兼容 ---------- */
QGroupBox { background: #ffffff; border: 1px solid #e4e7ec; border-radius: 10px;
            margin-top: 12px; padding: 16px 14px 12px 14px;
            font-weight: 600; color: #374151; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px;
                   background: #ffffff; color: #374151; }

/* ---------- 表格 ---------- */
QTableWidget { background: #ffffff; alternate-background-color: #f8fafc;
               border: 1px solid #e9eef5; border-radius: 8px; }
QTableWidget::item { padding: 6px; color: #1f2328; border: none; }
QTableWidget::item:selected { background: #e8efff; color: #1f2328; }
QHeaderView::section {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #f7f9fc, stop:1 #eef2f8);
    color: #4b5563; border: none; border-bottom: 1px solid #e4e7ec;
    padding: 8px; font-weight: 600;
}
QHeaderView::section:horizontal:last { border-right: none; }

/* ---------- 日志 ---------- */
QPlainTextEdit {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #fbfcfe, stop:1 #f6f8fb);
    border: 1px solid #e9eef5; border-radius: 8px;
    font-family: Consolas, "Cascadia Mono", monospace; font-size: 12px;
    padding: 6px;
}

/* ---------- 进度条 ---------- */
QProgressBar { background: #e7ebf2; border: none; border-radius: 8px;
               min-height: 14px; max-height: 14px; text-align: center;
               color: #4b5563; font-size: 11px; }
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #3d7bf2, stop:1 #2f6fed);
    border-radius: 8px;
}
QProgressBar#slim { min-height: 5px; max-height: 5px; background: #eef1f6; }
QProgressBar#slim::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #8fb3f8, stop:1 #6a9bf5);
    border-radius: 3px;
}

/* ---------- 状态栏 / 提示 / 滚动条 ---------- */
QStatusBar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ffffff, stop:1 #f4f6fb);
    color: #6b7280; border-top: 1px solid #e4e7ec;
}
QStatusBar QLabel { color: #6b7280; }
QToolTip { background: #ffffff; color: #1f2328; border: 1px solid #d6dae1; padding: 4px 8px; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #cfd4dc; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #b6bcc6; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #cfd4dc; border-radius: 5px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""

_LOG_COLORS = {"info": "#475569", "warn": "#b45309", "error": "#b91c1c"}

#: 状态图标前景色（浅色主题下保证对比度）
_STATUS_COLORS = {s.value: s.color for s in ExtractStatus}


def _fmt_duration(seconds: float) -> str:
    """把秒数格式化为紧凑中文时长。"""
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec:02d} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes:02d} 分"


class CollapsibleGroup(QFrame):
    """带标题栏的卡片式功能区。

    ``collapsible=True`` 时标题栏（箭头或整行）可点击，切换展开/收起；
    ``collapsible=False`` 时为固定的标题面板，用于把「任务队列 / 运行日志」
    之类的区域包装成一致的卡片分区。
    """

    def __init__(self, title: str, subtitle: str = "", *, expanded: bool = True,
                 collapsible: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="collapse")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._expanded = bool(expanded)
        self._collapsible = collapsible
        # 可折叠设置区：竖向贴合内容高度，收起时能收缩、把空间还给上层伸缩区；
        # 非折叠分区（任务/日志）：竖向伸缩，跟随布局填充。
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum if collapsible else QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame(objectName="collapseHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        head = QHBoxLayout(header)
        head.setContentsMargins(12, 8, 12, 8)
        head.setSpacing(8)

        self.btn_toggle = QToolButton(objectName="collapseToggle")
        self.btn_toggle.setAutoRaise(False)
        self.btn_toggle.setFixedSize(20, 20)
        self.btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle.setVisible(collapsible)
        self.btn_toggle.toggled.connect(self.set_expanded)
        head.addWidget(self.btn_toggle)

        self.lbl_title = QLabel(title, objectName="collapseTitle")
        head.addWidget(self.lbl_title)
        self.lbl_sub = QLabel(subtitle, objectName="collapseSub")
        self.lbl_sub.setVisible(bool(subtitle))
        head.addSpacing(4)
        head.addWidget(self.lbl_sub)
        head.addStretch(1)

        self._header_layout = head          # 供调用方在标题栏右侧追加控件
        if collapsible:
            header.setCursor(Qt.CursorShape.PointingHandCursor)
            header.mousePressEvent = lambda _e: self.toggle()
        root.addWidget(header)

        self._body = QWidget(objectName="collapseBody")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(14, 10, 14, 12)
        self._body_layout.setSpacing(10)
        root.addWidget(self._body, 1)

        self.set_expanded(self._expanded)

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def header_layout(self) -> QHBoxLayout:
        return self._header_layout

    def set_expanded(self, on: bool) -> None:
        self._expanded = bool(on)
        if self._collapsible:
            self.btn_toggle.blockSignals(True)
            self.btn_toggle.setChecked(self._expanded)
            self.btn_toggle.blockSignals(False)
            self.btn_toggle.setText("▾" if self._expanded else "▸")
            self._body.setVisible(self._expanded)
            self.updateGeometry()          # 收起后高度收缩，腾出空间给伸缩区

    def is_expanded(self) -> bool:
        return self._expanded

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        registry.load_all()
        self.setWindowTitle(f"WIN 解包工具 v{__version__}")
        self.resize(1120, 780)
        self.setMinimumSize(920, 640)
        self.setAcceptDrops(True)

        self._rows: dict[str, dict] = {}   # path -> {row, info, result}
        self._worker: UnpackWorker | None = None
        self._results: list[ExtractResult] = []
        self._settings = QSettings("WinUnpack", "WinUnpackGui")

        # ---- 进度/耗时统计
        self._durations: list[float] = []
        self._bytes_total = 0
        self._file_started_at = 0.0
        self._stage_prefix = ""
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick_elapsed)

        self._build_menu()
        self._build_ui()
        self._restore_settings()
        self._log("info", "就绪。拖入文件/文件夹，或点击「添加文件」开始。")
        self._log("info", f"已加载解包器 {len(registry.extractors())} 个。")
        plugins = registry.plugin_modules()
        if plugins:
            self._log("info", f"已加载自定义插件 {len(plugins)} 个。")
        for err in registry.load_errors():
            self._log("error", f"插件加载失败：{err.splitlines()[0] if err else err}")

    # =============================================================== 菜单
    def _build_menu(self) -> None:
        bar = self.menuBar()

        m_file = bar.addMenu("文件")
        m_file.addAction(QAction("添加文件…", self, shortcut=QKeySequence("Ctrl+O"),
                                 triggered=self.add_files_dialog))
        m_file.addAction(QAction("添加文件夹…", self, shortcut=QKeySequence("Ctrl+Shift+O"),
                                 triggered=self.add_folder_dialog))
        m_file.addSeparator()
        m_file.addAction(QAction("清空列表", self, triggered=self.clear_all))
        m_file.addSeparator()
        m_file.addAction(QAction("退出", self, shortcut=QKeySequence("Ctrl+Q"),
                                 triggered=self.close))

        m_tool = bar.addMenu("工具")
        m_tool.addAction(QAction("开始解包", self, shortcut=QKeySequence("F5"),
                                 triggered=self.start_unpack))
        m_tool.addAction(QAction("试运行（只探测不写文件）", self,
                                 shortcut=QKeySequence("Ctrl+F5"),
                                 triggered=self.start_dry_run))
        m_tool.addAction(QAction("取消任务", self, shortcut=QKeySequence("Esc"),
                                 triggered=self.cancel_unpack))
        m_tool.addSeparator()
        m_tool.addAction(QAction("导出报告…", self, shortcut=QKeySequence("Ctrl+S"),
                                 triggered=self.export_report))

        bar.addMenu("帮助").addAction(QAction("关于", self, triggered=self.show_about))

    # =============================================================== 界面构建
    def _build_ui(self) -> None:
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)

        # ---- 顶部：标题 + 快捷操作（一张卡片）
        head_card = QFrame(objectName="panel")
        head_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        head = QHBoxLayout(head_card)
        head.setContentsMargins(16, 12, 16, 12)
        head.setSpacing(12)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_box.addWidget(QLabel("WIN 解包工具", objectName="title"))
        title_box.addWidget(QLabel("自动识别格式 · 压缩包 / 安装包 / 游戏资源包",
                                   objectName="subtitle"))
        head.addLayout(title_box)
        head.addStretch(1)
        self.btn_add = QPushButton("添加文件")
        self.btn_add_dir = QPushButton("添加文件夹")
        self.btn_clear = QPushButton("清空")
        for b in (self.btn_add, self.btn_add_dir, self.btn_clear):
            b.setMinimumHeight(34)
            head.addWidget(b)
        lay.addWidget(head_card)

        # ---- 中部：任务队列 + 运行日志（纵向分割，各一张卡片）
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)

        task_card = CollapsibleGroup("任务队列", "拖拽到列表，或点右上角「添加文件」",
                                     collapsible=False)
        task_lay = task_card.body_layout()
        self.lbl_empty = QLabel(
            "把文件或文件夹拖到这里，或点击右上角「添加文件」\n"
            "支持 zip / 7z / rar / tar / gz / xz / cab / iso / msi / exe / 资源包",
            objectName="empty")
        self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty.setMinimumHeight(78)
        task_lay.addWidget(self.lbl_empty)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["文件", "识别类型", "大小", "状态", "解出", "说明 / 输出目录"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.itemDoubleClicked.connect(lambda _i: self._open_output())
        hh = self.table.horizontalHeader()
        for col, mode in ((_COL_NAME, QHeaderView.ResizeMode.Interactive),
                          (_COL_NOTE, QHeaderView.ResizeMode.Stretch)):
            hh.setSectionResizeMode(col, mode)
        for col, width in ((_COL_NAME, 300), (_COL_KIND, 118), (_COL_SIZE, 92),
                           (_COL_STATUS, 104), (_COL_COUNT, 66)):
            self.table.setColumnWidth(col, width)
        # 表头对齐与列内容保持一致（文字列左对齐，数值列右对齐，状态列居中）
        header_align = {
            _COL_NAME: Qt.AlignmentFlag.AlignLeft,
            _COL_KIND: Qt.AlignmentFlag.AlignLeft,
            _COL_SIZE: Qt.AlignmentFlag.AlignRight,
            _COL_STATUS: Qt.AlignmentFlag.AlignCenter,
            _COL_COUNT: Qt.AlignmentFlag.AlignCenter,
            _COL_NOTE: Qt.AlignmentFlag.AlignLeft,
        }
        for col, align in header_align.items():
            head_item = self.table.horizontalHeaderItem(col)
            if head_item is not None:
                head_item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        hh.setHighlightSections(False)
        task_lay.addWidget(self.table, 1)
        splitter.addWidget(task_card)

        log_card = CollapsibleGroup("运行日志", "", collapsible=False)
        self.chk_log = QCheckBox("显示日志")
        self.chk_log.setChecked(True)
        self.chk_log.toggled.connect(self._toggle_log)
        log_card.header_layout().addWidget(self.chk_log)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        log_card.body_layout().addWidget(self.log, 1)
        splitter.addWidget(log_card)
        splitter.setSizes([460, 200])
        lay.addWidget(splitter, 1)

        # ---- 设置：两个可折叠功能区
        self.grp_out = CollapsibleGroup(
            "输出与重名", "输出位置 · 重名策略 · 文件名编码", expanded=True)
        g1 = QGridLayout()
        g1.setHorizontalSpacing(10)
        g1.setVerticalSpacing(10)
        g1.setColumnStretch(1, 1)

        g1.addWidget(QLabel("输出目录"), 0, 0)
        self.ed_output = QLineEdit()
        self.ed_output.setPlaceholderText("留空 = 与源文件同级的「<文件名>_unpacked」目录")
        g1.addWidget(self.ed_output, 0, 1)
        btn_pick = QPushButton("选择…")
        btn_pick.clicked.connect(self.pick_output_dir)
        g1.addWidget(btn_pick, 0, 2)
        self.chk_merge = QCheckBox("全部解到同一目录")
        g1.addWidget(self.chk_merge, 0, 3)

        g1.addWidget(QLabel("重名处理"), 1, 0)
        conflict_row = QHBoxLayout()
        conflict_row.setSpacing(10)
        self.cmb_conflict = QComboBox()
        self.cmb_conflict.setMinimumWidth(210)
        for policy in ConflictPolicy:
            self.cmb_conflict.addItem(policy.label, policy.value)
        conflict_row.addWidget(self.cmb_conflict)
        conflict_row.addSpacing(14)
        conflict_row.addWidget(QLabel("文件名编码"))
        self.cmb_encoding = QComboBox()
        self.cmb_encoding.setMinimumWidth(160)
        for text, value in _ENCODING_CHOICES:
            self.cmb_encoding.addItem(text, value)
        self.cmb_encoding.setToolTip(
            "部分国产压缩包用 GBK 存放中文名且未标记 UTF-8，"
            "「自动检测」会尝试多种编码并回退，出现乱码时可手动指定")
        conflict_row.addWidget(self.cmb_encoding)
        conflict_row.addStretch(1)
        g1.addLayout(conflict_row, 1, 1, 1, 3)
        self.grp_out.body_layout().addLayout(g1)
        lay.addWidget(self.grp_out)

        self.grp_adv = CollapsibleGroup(
            "密码与解包行为", "密码表 · 递归 · 试运行", expanded=False)
        g2 = QGridLayout()
        g2.setHorizontalSpacing(10)
        g2.setVerticalSpacing(10)
        g2.setColumnStretch(1, 1)

        g2.addWidget(QLabel("密码表"), 0, 0)
        self.ed_pwd = QLineEdit()
        self.ed_pwd.setPlaceholderText("多个密码用逗号分隔，如 123456,admin,password")
        g2.addWidget(self.ed_pwd, 0, 1)
        btn_pwd = QPushButton("从文件加载…")
        btn_pwd.clicked.connect(self.load_passwords)
        g2.addWidget(btn_pwd, 0, 2)

        g2.addWidget(QLabel("行为"), 1, 0)
        behavior_row = QHBoxLayout()
        behavior_row.setSpacing(18)
        self.chk_recursive = QCheckBox("递归解包嵌套压缩包")
        self.chk_subdir = QCheckBox("添加文件夹时含子目录")
        self.chk_subdir.setChecked(True)
        self.chk_dry = QCheckBox("试运行（不写文件）")
        self.chk_dry.setToolTip("只探测类型并给出输出计划，不做任何写入")
        for c in (self.chk_recursive, self.chk_subdir, self.chk_dry):
            behavior_row.addWidget(c)
        behavior_row.addStretch(1)
        g2.addLayout(behavior_row, 1, 1, 1, 3)
        self.grp_adv.body_layout().addLayout(g2)
        lay.addWidget(self.grp_adv)

        # ---- 底部：进度 + 主按钮
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        prog_box = QVBoxLayout()
        prog_box.setSpacing(5)
        self.lbl_stage = QLabel("等待任务", objectName="hint")
        self.bar_total = QProgressBar()
        self.bar_total.setRange(0, 100)
        self.bar_total.setValue(0)
        self.bar_total.setTextVisible(False)   # 数值由上方标签与状态栏呈现
        self.bar_cur = QProgressBar(objectName="slim")
        self.bar_cur.setRange(0, 100)
        self.bar_cur.setValue(0)
        self.bar_cur.setTextVisible(False)
        prog_box.addWidget(self.lbl_stage)
        prog_box.addWidget(self.bar_total)
        prog_box.addWidget(self.bar_cur)
        bottom.addLayout(prog_box, 1)

        self.btn_export = QPushButton("导出报告")
        self.btn_cancel = QPushButton("取消", objectName="danger")
        self.btn_start = QPushButton("开始解包", objectName="primary")
        for b in (self.btn_export, self.btn_cancel, self.btn_start):
            b.setMinimumHeight(36)
            b.setMinimumWidth(96)
        bottom.addWidget(self.btn_export)
        bottom.addWidget(self.btn_cancel)
        bottom.addWidget(self.btn_start)
        lay.addLayout(bottom)

        # ---- 状态栏
        self.lbl_status = QLabel("就绪")
        self.statusBar().addPermanentWidget(self.lbl_status)

        # ---- 信号
        self.btn_add.clicked.connect(self.add_files_dialog)
        self.btn_add_dir.clicked.connect(self.add_folder_dialog)
        self.btn_clear.clicked.connect(self.clear_all)
        self.btn_start.clicked.connect(self.start_unpack)
        self.btn_cancel.clicked.connect(self.cancel_unpack)
        self.btn_export.clicked.connect(self.export_report)
        self.btn_cancel.setEnabled(False)
        self.setStyleSheet(_STYLE)
        self._refresh_empty_hint()

    def _toggle_log(self, visible: bool) -> None:
        self.log.setVisible(visible)

    def _refresh_empty_hint(self) -> None:
        """空列表时显示拖拽提示；并按队列状态同步按钮可用性。"""
        running = bool(self._worker and self._worker.isRunning())
        self.lbl_empty.setVisible(self.table.rowCount() == 0)
        has_rows = bool(self._rows)
        self.btn_start.setEnabled(has_rows and not running)
        self.btn_clear.setEnabled(has_rows and not running)
        self.btn_add.setEnabled(not running)
        self.btn_add_dir.setEnabled(not running)

    # =============================================================== 拖拽支持
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        self.add_paths(paths)
        event.acceptProposedAction()

    # =============================================================== 添加任务
    def add_files_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "选择要解包的文件", "",
                                                "所有文件 (*.*)")
        if files:
            self.add_paths([Path(f) for f in files])

    def add_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            self.add_paths([Path(folder)])

    def add_paths(self, paths: list[Path]) -> None:
        files: list[Path] = []
        for p in paths:
            if p.is_dir():
                it = p.rglob("*") if self.chk_subdir.isChecked() else p.glob("*")
                files.extend(sorted(f for f in it if f.is_file()))
            elif p.is_file():
                files.append(p)
        if not files:
            return

        added = 0
        for f in files:
            key = str(f)
            if key in self._rows:
                continue
            self._add_row(f)
            added += 1
        if added:
            self._log("info", f"已加入 {added} 个文件（去重后）。")
        self._refresh_empty_hint()
        self._update_stage()

    def _add_row(self, path: Path) -> None:
        info = detector.detect(path)
        row = self.table.rowCount()
        self.table.insertRow(row)

        name_item = QTableWidgetItem(path.name)
        name_item.setToolTip(str(path))
        name_item.setData(Qt.ItemDataRole.UserRole, str(path))

        kind_item = QTableWidgetItem(info.kind.label)
        if info.note:
            kind_item.setToolTip(info.note)
        if info.encrypted:
            kind_item.setText(f"{info.kind.label} 🔒")
            kind_item.setToolTip("检测到加密")

        size_item = QTableWidgetItem(human_size(info.size))
        size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight |
                                   Qt.AlignmentFlag.AlignVCenter)
        status_item = QTableWidgetItem("")
        status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        count_item = QTableWidgetItem("-")
        count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        note_item = QTableWidgetItem("待处理")

        for col, item in enumerate((name_item, kind_item, size_item, status_item,
                                    count_item, note_item)):
            self.table.setItem(row, col, item)

        self._rows[str(path)] = {"row": row, "info": info, "result": None}
        self._set_status(row, ExtractStatus.PENDING)

    def _set_status(self, row: int, status: ExtractStatus) -> None:
        """状态列：图标 + 文字 + 颜色编码，便于批量任务快速扫视。"""
        item = self.table.item(row, _COL_STATUS)
        if item is None:
            return
        item.setText(f"{status.icon} {status.label}")
        item.setForeground(QColor(status.color))
        item.setToolTip(status.label)

    # =============================================================== 执行任务
    def _collect_passwords(self) -> list[str]:
        raw = self.ed_pwd.text()
        parts: list[str] = []
        for chunk in raw.replace(";", ",").replace("\n", ",").split(","):
            chunk = chunk.strip()
            if chunk and chunk not in parts:
                parts.append(chunk)
        return parts

    def _build_options(self) -> EngineOptions:
        out_text = self.ed_output.text().strip()
        return EngineOptions(
            output_dir=Path(out_text) if out_text else None,
            merge_output=self.chk_merge.isChecked(),
            conflict=ConflictPolicy(self.cmb_conflict.currentData()
                                    or ConflictPolicy.RENAME.value),
            recursive=self.chk_recursive.isChecked(),
            passwords=self._collect_passwords(),
            encoding=self.cmb_encoding.currentData() or "auto",
            dry_run=self.chk_dry.isChecked(),
            carve=True,
        )

    def start_unpack(self) -> None:
        self._launch(dry_run=False)

    def start_dry_run(self) -> None:
        self._launch(dry_run=True)

    def _launch(self, *, dry_run: bool) -> None:
        if self._worker and self._worker.isRunning():
            return
        paths = [Path(k) for k in self._rows.keys()]
        if not paths:
            QMessageBox.information(self, "提示", "请先添加要解包的文件。")
            return
        if self.chk_dry.isChecked() != dry_run:
            self.chk_dry.setChecked(dry_run)

        self._results = []
        self._durations = []
        self._bytes_total = 0
        self._reset_status()
        self.ed_output.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.cmb_conflict.setEnabled(False)
        self.cmb_encoding.setEnabled(False)
        self._refresh_empty_hint()
        self.bar_total.setRange(0, len(paths))
        self.bar_total.setValue(0)
        self.bar_cur.setValue(0)
        self.lbl_status.setText(f"处理中 0 / {len(paths)}")
        self._log("info", "=" * 46)
        verb = "试运行" if dry_run else "开始解包"
        self._log("info", f"{verb} {len(paths)} 个文件 @ {datetime.now():%H:%M:%S}"
                          f"（重名策略：{self.cmb_conflict.currentText()}）")

        self._worker = UnpackWorker(paths, self._build_options(), self)
        self._worker.sig_log.connect(self._log)
        self._worker.sig_progress.connect(self._on_progress)
        self._worker.sig_file_started.connect(self._on_file_started)
        self._worker.sig_file_done.connect(self._on_file_done)
        self._worker.sig_finished.connect(self._on_finished)
        self._worker.sig_fatal.connect(self._on_fatal)
        self._worker.start()
        self._timer.start()

    def _tick_elapsed(self) -> None:
        """每秒刷新当前文件的已用时间。"""
        if not (self._worker and self._worker.isRunning()) or not self._stage_prefix:
            return
        elapsed = time.perf_counter() - self._file_started_at
        self.lbl_stage.setText(f"{self._stage_prefix}  ·  已用 {_fmt_duration(elapsed)}")

    def cancel_unpack(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._log("warn", "已请求取消，等待当前文件结束…")
            self.btn_cancel.setEnabled(False)

    def _reset_status(self) -> None:
        for _key, meta in self._rows.items():
            self._set_status(meta["row"], ExtractStatus.PENDING)
            self._set_cell(meta["row"], _COL_COUNT, "-")
            self._set_cell(meta["row"], _COL_NOTE, "待处理")
            meta["result"] = None

    # --------------------------------------------------------------- 信号处理
    def _log(self, level: str, message: str) -> None:
        color = _LOG_COLORS.get(level, "#475569")
        prefix = {"info": "·", "warn": "!", "error": "x"}.get(level, "·")
        stamp = datetime.now().strftime("%H:%M:%S")
        body = html.escape(message)
        self.log.appendHtml(
            f'<span style="color:#9aa1ac">{stamp}</span> '
            f'<span style="color:{color}">{prefix} {body}</span>')

    def _on_file_started(self, index: int, total: int, path: str) -> None:
        self._file_started_at = time.perf_counter()
        self._stage_prefix = f"({index}/{total}) 正在处理：{Path(path).name}"
        self.lbl_stage.setText(self._stage_prefix)
        self.bar_cur.setValue(0)
        meta = self._rows.get(path)
        if meta:
            self._set_status(meta["row"], ExtractStatus.RUNNING)
            self.table.scrollToItem(self.table.item(meta["row"], _COL_NAME))

    def _on_progress(self, done: int, total: int, _name: str) -> None:
        pct = int(done * 100 / total) if total else 0
        self.bar_cur.setValue(min(99, max(0, pct)))

    def _on_file_done(self, result: ExtractResult) -> None:
        key = str(result.source)
        meta = self._rows.get(key)
        if meta:
            meta["result"] = result
            self._set_status(meta["row"], result.status)
            self._set_cell(meta["row"], _COL_COUNT,
                           str(result.file_count) if result.file_count else "-")
            note = result.message
            if result.bytes_written:
                note += f"  [{human_size(result.bytes_written)} / {result.duration:.1f}s]"
            if result.output_dir and result.ok:
                note += f"  →  {result.output_dir}"
            self._set_cell(meta["row"], _COL_NOTE, note)
        self._results.append(result)
        self._durations.append(result.duration or 0.0)
        self._bytes_total += result.bytes_written
        self.bar_total.setValue(self.bar_total.value() + 1)
        self.bar_cur.setValue(100)

        done = len(self._results)
        total = self.bar_total.maximum()
        avg = sum(self._durations) / len(self._durations)
        eta = avg * max(0, total - done)
        text = f"处理中 {done} / {total} · 已解出 {human_size(self._bytes_total)}"
        if 1 < eta < 86400:
            text += f" · 预计剩余 {_fmt_duration(eta)}"
        self.lbl_status.setText(text)

    def _on_finished(self, results: list) -> None:
        self._timer.stop()
        self._results = list(results)
        ok = sum(1 for r in results if r.status is ExtractStatus.SUCCESS)
        pwd = sum(1 for r in results if r.status is ExtractStatus.NEED_PASSWORD)
        bad = sum(1 for r in results if r.status in
                  (ExtractStatus.FAILED, ExtractStatus.UNSUPPORTED))
        dry = sum(1 for r in results if r.status is ExtractStatus.DRY_RUN)
        skipped = sum(r.skipped for r in results)
        total_bytes = sum(r.bytes_written for r in results)
        state = "已取消" if (self._worker and self._worker.cancelled) else "完成"
        summary = f"{state}：共 {len(results)} 个，成功 {ok}，需密码 {pwd}，失败/不支持 {bad}"
        if dry:
            summary += f"，试运行 {dry}"
        self.lbl_stage.setText(summary)
        tail = f" · 已解出 {human_size(total_bytes)} / {len(results)} 个文件"
        if skipped:
            tail += f" · 跳过 {skipped}"
        self.lbl_status.setText(f"{state}{tail}")
        self._log("info", summary)
        if total_bytes:
            self._log("info", f"本次共写出 {human_size(total_bytes)}"
                              + (f"，按冲突策略跳过 {skipped} 个已存在文件" if skipped else ""))
        self._finish_ui()

    def _on_fatal(self, message: str) -> None:
        self._timer.stop()
        self._log("error", f"任务异常终止：{message}")
        QMessageBox.critical(self, "错误", f"任务异常终止：\n{message}")
        self._finish_ui()

    def _finish_ui(self) -> None:
        self.ed_output.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.cmb_conflict.setEnabled(True)
        self.cmb_encoding.setEnabled(True)
        self._refresh_empty_hint()

    # =============================================================== 结果操作
    def _selected_row(self) -> int | None:
        items = self.table.selectedItems()
        return items[0].row() if items else None

    def _row_key(self, row: int) -> str | None:
        item = self.table.item(row, _COL_NAME)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _table_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        menu = QMenu(self)
        menu.addAction("打开输出目录", self._open_output)
        menu.addAction("打开源文件位置", self._open_source_dir)
        menu.addSeparator()
        menu.addAction("重新探测类型", lambda: self._redetect(row))
        menu.addAction("从列表移除", lambda: self._remove_row(row))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _open_output(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        key = self._row_key(row)
        meta = self._rows.get(key) if key else None
        result = meta.get("result") if meta else None
        target = None
        if result and result.output_dir and Path(result.output_dir).exists():
            target = Path(result.output_dir)
        elif key:
            guess = Path(key).parent / f"{Path(key).name}_unpacked"
            if guess.exists():
                target = guess
        if target:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        else:
            QMessageBox.information(self, "提示", "尚未生成输出目录。")

    def _open_source_dir(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        key = self._row_key(row)
        if key and Path(key).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(key).parent)))

    def _redetect(self, row: int) -> None:
        key = self._row_key(row)
        if not key:
            return
        info = detector.detect(Path(key))
        self._rows[key]["info"] = info
        self._set_cell(row, _COL_KIND, info.kind.label + (" 🔒" if info.encrypted else ""))
        self._set_cell(row, _COL_SIZE, human_size(info.size))
        self._log("info", f"重新探测 {Path(key).name} -> {info.kind.label}")

    def _remove_row(self, row: int) -> None:
        key = self._row_key(row)
        if key:
            self._rows.pop(key, None)
        self.table.removeRow(row)
        self._reindex()
        self._refresh_empty_hint()
        self._update_stage()

    def clear_all(self) -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.warning(self, "提示", "任务进行中，请先取消。")
            return
        self._timer.stop()
        self.table.setRowCount(0)
        self._rows.clear()
        self._results = []
        self._durations = []
        self._bytes_total = 0
        self._stage_prefix = ""
        self.bar_total.setValue(0)
        self.bar_cur.setValue(0)
        self.lbl_stage.setText("等待任务")
        self.lbl_status.setText("就绪")
        self._refresh_empty_hint()
        self._log("info", "列表已清空。")

    def _reindex(self) -> None:
        for row in range(self.table.rowCount()):
            key = self._row_key(row)
            if key in self._rows:
                self._rows[key]["row"] = row

    def _update_stage(self) -> None:
        if not (self._worker and self._worker.isRunning()):
            self.lbl_stage.setText(f"待解包 {len(self._rows)} 个文件")
            self.lbl_status.setText(f"队列 {len(self._rows)} 个文件")

    def _set_cell(self, row: int, col: int, text: str) -> None:
        item = self.table.item(row, col)
        if item is not None:
            item.setText(text)

    # =============================================================== 其他功能
    def pick_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if folder:
            self.ed_output.setText(folder)

    def load_passwords(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择密码字典文件", "",
                                              "文本文件 (*.txt *.lst *.dic);;所有文件 (*.*)")
        if not path:
            return
        try:
            words = [ln.strip() for ln in Path(path).read_text(
                encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        except OSError as exc:
            QMessageBox.critical(self, "错误", f"读取失败：{exc}")
            return
        self.ed_pwd.setText(",".join(words[:200]))
        self._log("info", f"已载入 {min(len(words), 200)} 个密码候选。")

    def export_report(self) -> None:
        if not self._results:
            QMessageBox.information(self, "提示", "暂无结果可导出。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出报告", f"unpack_report_{datetime.now():%Y%m%d_%H%M%S}.csv",
            "CSV 表格 (*.csv);;JSON (*.json);;文本 (*.txt)")
        if not path:
            return
        try:
            out = report.export(self._results, Path(path))
        except OSError as exc:
            QMessageBox.critical(self, "错误", f"导出失败：{exc}")
            return
        self._log("info", f"报告已导出：{out}")
        QMessageBox.information(self, "完成", f"报告已导出：\n{out}")

    def show_about(self) -> None:
        QMessageBox.about(
            self, "关于",
            f"<b>WIN 解包工具 v{__version__}</b><br><br>"
            "支持：ZIP / 7z / RAR / TAR / GZip / BZip2 / XZ / Zstd / CAB / ISO / "
            "MSI / 自解压安装包 / 游戏资源包雕刻<br>"
            "加密包支持密码表批量尝试；GBK 等非 UTF-8 成员名自动回退；"
            "重名可选保留两者 / 覆盖 / 跳过 / 较新者胜；<br>"
            "<code>plugins/</code> 目录可扩展私有格式。<br><br>"
            "技术栈：Python + PySide6")

    # =============================================================== 配置持久化
    def _restore_settings(self) -> None:
        self.chk_merge.setChecked(self._settings.value("merge", False, bool))
        self.chk_recursive.setChecked(self._settings.value("recursive", False, bool))
        self.chk_subdir.setChecked(self._settings.value("subdir", True, bool))
        self.chk_dry.setChecked(self._settings.value("dry_run", False, bool))

        # 兼容 v1.0.0 的布尔 overwrite 配置
        stored = self._settings.value("conflict", "", str)
        if not stored:
            stored = (ConflictPolicy.OVERWRITE.value
                      if self._settings.value("overwrite", False, bool)
                      else ConflictPolicy.RENAME.value)
        idx = self.cmb_conflict.findData(stored)
        self.cmb_conflict.setCurrentIndex(idx if idx >= 0 else 0)

        enc = self._settings.value("encoding", "auto", str)
        enc_idx = self.cmb_encoding.findData(enc)
        self.cmb_encoding.setCurrentIndex(enc_idx if enc_idx >= 0 else 0)

        show_log = self._settings.value("show_log", True, bool)
        self.chk_log.setChecked(show_log)
        self.log.setVisible(show_log)
        self.ed_output.setText(self._settings.value("output", "", str))

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        self._timer.stop()
        self._settings.setValue("merge", self.chk_merge.isChecked())
        self._settings.setValue("recursive", self.chk_recursive.isChecked())
        self._settings.setValue("subdir", self.chk_subdir.isChecked())
        self._settings.setValue("dry_run", self.chk_dry.isChecked())
        self._settings.setValue("conflict", self.cmb_conflict.currentData())
        self._settings.setValue("encoding", self.cmb_encoding.currentData())
        self._settings.setValue("show_log", self.chk_log.isChecked())
        self._settings.setValue("output", self.ed_output.text())
        super().closeEvent(event)


def _apply_light_palette(app: QApplication) -> None:
    """强制浅色调色板。

    系统处于深色模式时，Qt 默认样式会把菜单栏、下拉菜单与对话框渲染成深色；
    显式设置浅色 + Fusion 风格可确保菜单始终白底黑字。
    """
    app.setStyle("Fusion")
    pal = QPalette()
    pairs = (
        (QPalette.ColorRole.Window, "#f4f6f9"),
        (QPalette.ColorRole.WindowText, "#1f2328"),
        (QPalette.ColorRole.Base, "#ffffff"),
        (QPalette.ColorRole.AlternateBase, "#fafbfc"),
        (QPalette.ColorRole.Text, "#1f2328"),
        (QPalette.ColorRole.Button, "#ffffff"),
        (QPalette.ColorRole.ButtonText, "#1f2328"),
        (QPalette.ColorRole.ToolTipBase, "#ffffff"),
        (QPalette.ColorRole.ToolTipText, "#1f2328"),
        (QPalette.ColorRole.Highlight, "#2f6fed"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QPalette.ColorRole.PlaceholderText, "#9aa1ac"),
        (QPalette.ColorRole.Mid, "#d6dae1"),
        (QPalette.ColorRole.Light, "#ffffff"),
    )
    for role, color in pairs:
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#a0a6b1"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
                 QColor("#a0a6b1"))
    app.setPalette(pal)


def resource_path(name: str) -> Path:
    """定位随包资源：打包后取 PyInstaller 解包目录，源码运行取 tools/icon。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2] / "tools" / "icon"))
    return base / name


def launch() -> int:
    """启动图形界面，返回退出码。"""
    app = QApplication.instance() or QApplication([])
    app.setWindowIcon(QIcon(str(resource_path("app-icon-256.png"))))
    app.setApplicationName("WIN 解包工具")
    _apply_light_palette(app)
    window = MainWindow()
    window.show()
    return app.exec()
