"""Koyu tema: renkler ve Qt stil sayfası."""
from __future__ import annotations

C = {
    "bg": "#1d1f24",
    "panel": "#25272d",
    "panel2": "#2c2f36",
    "border": "#3a3d46",
    "text": "#e4e6ea",
    "muted": "#9aa0a8",
    "accent": "#4a8cff",
    "accent_hover": "#5f9bff",
    "ok": "#3fb950",
    "err": "#f0605a",
    "warn": "#d6a43a",
    "user": "#2d3a55",
}

STYLE = f"""
QWidget {{ background: {C['bg']}; color: {C['text']}; font-family: 'Segoe UI'; font-size: 10pt; }}
QLabel, QCheckBox {{ background: transparent; }}
QToolTip {{ background: {C['panel2']}; color: {C['text']}; border: 1px solid {C['border']}; padding: 4px; }}
QFrame#panel, QFrame#inputBox {{ background: {C['panel']}; border: 1px solid {C['border']}; border-radius: 8px; }}
QFrame#bubbleUser {{ background: {C['user']}; border-radius: 8px; }}
QFrame#bubbleAssistant {{ background: transparent; }}
QFrame#step {{ background: {C['panel']}; border: 1px solid {C['border']}; border-radius: 6px; }}
QFrame#plan {{ background: {C['panel']}; border: 1px solid {C['accent']}; border-radius: 8px; }}
QFrame#note {{ background: transparent; border-left: 3px solid {C['muted']}; }}
QLabel#title {{ font-size: 12pt; font-weight: 600; }}
QLabel#muted {{ color: {C['muted']}; }}
QLabel#badge {{ border: 1px solid {C['border']}; border-radius: 9px; padding: 1px 8px; color: {C['muted']}; font-size: 9pt; }}
QLabel#badgeOk {{ border: 1px solid {C['ok']}; border-radius: 9px; padding: 1px 8px; color: {C['ok']}; font-size: 9pt; }}
QLabel#badgeBusy {{ border: 1px solid {C['accent']}; border-radius: 9px; padding: 1px 8px; color: {C['accent']}; font-size: 9pt; }}
QLabel#badgeErr {{ border: 1px solid {C['err']}; border-radius: 9px; padding: 1px 8px; color: {C['err']}; font-size: 9pt; }}
QLabel#progress {{ color: {C['muted']}; padding: 2px 4px; }}
QPushButton {{ background: {C['panel2']}; border: 1px solid {C['border']}; border-radius: 6px; padding: 5px 10px; }}
QPushButton:hover {{ border-color: {C['accent']}; }}
QPushButton:disabled {{ color: {C['muted']}; border-color: {C['panel2']}; }}
QPushButton#primary {{ background: {C['accent']}; border: none; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: {C['accent_hover']}; }}
QPushButton#send {{ background: {C['accent']}; border: none; border-radius: 16px; color: white; font-size: 13pt; font-weight: 700; }}
QPushButton#send:hover {{ background: {C['accent_hover']}; }}
QPushButton#stop {{ background: {C['err']}; border: none; border-radius: 16px; color: white; font-size: 11pt; font-weight: 700; }}
QPushButton#icon {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 3px 6px; font-size: 11pt; }}
QPushButton#icon:hover {{ border-color: {C['border']}; }}
QPushButton#icon:checked {{ border-color: {C['accent']}; color: {C['accent']}; }}
QPushButton#link {{ background: transparent; border: none; color: {C['muted']}; padding: 2px; text-align: left; }}
QPushButton#link:hover {{ color: {C['text']}; }}
QComboBox, QLineEdit {{ background: {C['panel2']}; border: 1px solid {C['border']}; border-radius: 6px; padding: 4px 8px; }}
QComboBox:focus, QLineEdit:focus {{ border-color: {C['accent']}; }}
QComboBox QAbstractItemView {{ background: {C['panel2']}; border: 1px solid {C['border']}; selection-background-color: {C['accent']}; }}
QPlainTextEdit {{ background: transparent; border: none; padding: 4px; }}
QCheckBox {{ spacing: 5px; color: {C['text']}; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {C['border']}; border-radius: 3px; background: {C['panel2']}; }}
QCheckBox::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']}; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 9px; }}
QScrollBar::handle:vertical {{ background: {C['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""
