"""Android Studio (Darcula / New UI dark) look."""
from PySide6.QtGui import QColor, QPalette, QFont
from PySide6.QtWidgets import QApplication

BG = "#2B2D30"        # tool windows
EDITOR_BG = "#1E1F22"
PANEL = "#313338"
BORDER = "#393B40"
FG = "#DFE1E5"
FG_DIM = "#868A91"
ACCENT = "#3574F0"
SELECT = "#2E436E"
GREEN = "#5FAD65"
RED = "#F75464"
ORANGE = "#E0A030"

STYLE = f"""
* {{ font-family: "Segoe UI"; font-size: 9pt; color: {FG}; }}
QMainWindow, QDialog, QWidget {{ background: {BG}; }}
QMenuBar {{ background: {BG}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 4px 9px; background: transparent; }}
QMenuBar::item:selected {{ background: {SELECT}; border-radius: 4px; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 5px 24px; border-radius: 4px; }}
QMenu::item:selected {{ background: {SELECT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QToolBar {{ background: {BG}; border: none; border-bottom: 1px solid {BORDER}; spacing: 4px; padding: 2px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 5px; padding: 4px 8px; }}
QToolButton:hover {{ background: {PANEL}; border-color: {BORDER}; }}
QToolButton:checked {{ background: {SELECT}; }}
QStatusBar {{ background: {BG}; border-top: 1px solid {BORDER}; color: {FG_DIM}; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {BG}; padding: 5px 8px; border-bottom: 1px solid {BORDER}; font-weight: 600; }}
QTabWidget::pane {{ border: none; border-top: 1px solid {BORDER}; background: {EDITOR_BG}; }}
QTabBar {{ background: {BG}; }}
QTabBar::tab {{ background: {BG}; padding: 6px 14px; border-bottom: 3px solid transparent; color: {FG_DIM}; }}
QTabBar::tab:selected {{ color: {FG}; border-bottom: 3px solid {ACCENT}; }}
QTabBar::tab:hover {{ background: {PANEL}; }}
QTabBar::close-button {{ subcontrol-position: right; }}
QTreeView, QListView, QTableView, QTreeWidget, QListWidget, QTableWidget {{
    background: {BG}; border: none; alternate-background-color: {BG}; outline: 0; }}
QTreeView::item, QListView::item {{ padding: 2px 0; border-radius: 3px; }}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{ background: {SELECT}; color: {FG}; }}
QTreeView::item:hover, QListView::item:hover {{ background: {PANEL}; }}
QHeaderView::section {{ background: {BG}; border: none; border-bottom: 1px solid {BORDER}; padding: 4px; color: {FG_DIM}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {EDITOR_BG}; border: 1px solid {BORDER}; border-radius: 4px; padding: 3px 6px; selection-background-color: {SELECT}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; selection-background-color: {SELECT}; border: 1px solid {BORDER}; }}
QPlainTextEdit, QTextEdit {{ background: {EDITOR_BG}; border: none; selection-background-color: {SELECT}; font-family: "JetBrains Mono", Consolas; font-size: 10pt; }}
QPushButton {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 5px; padding: 5px 14px; }}
QPushButton:hover {{ background: #3C3F45; }}
QPushButton:default, QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: white; }}
QPushButton:disabled {{ color: {FG_DIM}; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {BORDER}; border-radius: 3px; background: {EDITOR_BG}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #4A4D54; min-height: 24px; border-radius: 5px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: #4A4D54; min-width: 24px; border-radius: 5px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 5px; margin-top: 10px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {FG_DIM}; }}
QToolTip {{ background: {PANEL}; color: {FG}; border: 1px solid {BORDER}; }}
QProgressBar {{ background: {EDITOR_BG}; border: 1px solid {BORDER}; border-radius: 4px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
"""


def apply(app: QApplication):
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.Window, QColor(BG))
    p.setColor(QPalette.WindowText, QColor(FG))
    p.setColor(QPalette.Base, QColor(EDITOR_BG))
    p.setColor(QPalette.AlternateBase, QColor(BG))
    p.setColor(QPalette.Text, QColor(FG))
    p.setColor(QPalette.Button, QColor(PANEL))
    p.setColor(QPalette.ButtonText, QColor(FG))
    p.setColor(QPalette.Highlight, QColor(SELECT))
    p.setColor(QPalette.HighlightedText, QColor(FG))
    p.setColor(QPalette.ToolTipBase, QColor(PANEL))
    p.setColor(QPalette.ToolTipText, QColor(FG))
    app.setPalette(p)
    app.setStyleSheet(STYLE)
    app.setFont(QFont("Segoe UI", 9))
