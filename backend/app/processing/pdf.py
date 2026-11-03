"""Shared access to PDFium, which is not thread-safe: every call must hold PDFIUM_LOCK."""

import threading

PDFIUM_LOCK = threading.Lock()
