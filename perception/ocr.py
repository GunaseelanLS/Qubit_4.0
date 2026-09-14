"""OCR Engine for Qubit 4.0 using native Linux Tesseract and Leptonica libraries.

Binds to libtesseract.so and libleptonica.so via standard ctypes to provide
fast, zero-dependency local text recognition with bounding boxes and confidence scores.
"""

import ctypes
from ctypes import util
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# Standard Tessdata search locations on Linux
DEFAULT_TESSDATA_DIRS = [
    "/usr/share/tesseract/tessdata",
    "/usr/share/tessdata",
    "/usr/local/share/tessdata",
]

# Tesseract Page Iterator Levels
RIL_BLOCK = 0
RIL_PARA = 1
RIL_TEXTLINE = 2
RIL_WORD = 3
RIL_SYMBOL = 4


class OCREngine:
    """Zero-dependency local OCR engine using C library bindings."""

    def __init__(
        self,
        tessdata_dir: Optional[str] = None,
        language: str = "eng",
    ):
        self.language = language
        self.tessdata_dir = self._find_tessdata_dir(tessdata_dir)
        self._tess_lib = None
        self._lept_lib = None
        self._load_libraries()
        self._setup_function_signatures()

    def _find_tessdata_dir(self, custom_path: Optional[str]) -> str:
        if custom_path and os.path.isdir(custom_path):
            return custom_path

        env_path = os.environ.get("TESSDATA_PREFIX")
        if env_path and os.path.isdir(env_path):
            return env_path

        for candidate in DEFAULT_TESSDATA_DIRS:
            if os.path.isdir(candidate):
                return candidate

        raise FileNotFoundError("Could not locate a valid Tesseract tessdata directory on the system.")

    def _load_libraries(self):
        # Find and load libtesseract
        tess_name = util.find_library("tesseract") or "libtesseract.so.5.5"
        try:
            self._tess_lib = ctypes.CDLL(tess_name)
        except OSError:
            # Fallback common names
            for fallback in ["libtesseract.so.5", "libtesseract.so.4", "libtesseract.so"]:
                try:
                    self._tess_lib = ctypes.CDLL(fallback)
                    break
                except OSError:
                    continue

        if self._tess_lib is None:
            raise OSError("Failed to load libtesseract shared library.")

        # Find and load libleptonica
        lept_name = util.find_library("leptonica") or "libleptonica.so.6"
        try:
            self._lept_lib = ctypes.CDLL(lept_name)
        except OSError:
            for fallback in ["libleptonica.so.5", "liblept.so", "libleptonica.so"]:
                try:
                    self._lept_lib = ctypes.CDLL(fallback)
                    break
                except OSError:
                    continue

        if self._lept_lib is None:
            raise OSError("Failed to load libleptonica shared library.")

    def _setup_function_signatures(self):
        tess = self._tess_lib
        lept = self._lept_lib

        # Tesseract API
        tess.TessBaseAPICreate.restype = ctypes.c_void_p

        tess.TessBaseAPIInit3.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        tess.TessBaseAPIInit3.restype = ctypes.c_int

        tess.TessBaseAPISetImage2.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        tess.TessBaseAPIRecognize.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        tess.TessBaseAPIRecognize.restype = ctypes.c_int

        tess.TessBaseAPIGetIterator.argtypes = [ctypes.c_void_p]
        tess.TessBaseAPIGetIterator.restype = ctypes.c_void_p

        tess.TessPageIteratorBoundingBox.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        ]
        tess.TessPageIteratorBoundingBox.restype = ctypes.c_int

        tess.TessResultIteratorConfidence.argtypes = [ctypes.c_void_p, ctypes.c_int]
        tess.TessResultIteratorConfidence.restype = ctypes.c_float

        tess.TessResultIteratorGetUTF8Text.argtypes = [ctypes.c_void_p, ctypes.c_int]
        tess.TessResultIteratorGetUTF8Text.restype = ctypes.c_void_p

        tess.TessDeleteText.argtypes = [ctypes.c_void_p]

        tess.TessPageIteratorNext.argtypes = [ctypes.c_void_p, ctypes.c_int]
        tess.TessPageIteratorNext.restype = ctypes.c_int

        tess.TessPageIteratorDelete.argtypes = [ctypes.c_void_p]

        tess.TessBaseAPIEnd.argtypes = [ctypes.c_void_p]
        tess.TessBaseAPIDelete.argtypes = [ctypes.c_void_p]

        # Leptonica API
        lept.pixRead.argtypes = [ctypes.c_char_p]
        lept.pixRead.restype = ctypes.c_void_p

        lept.pixReadMem.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        lept.pixReadMem.restype = ctypes.c_void_p

        lept.pixDestroy.argtypes = [ctypes.POINTER(ctypes.c_void_p)]

    def extract_from_file(self, image_path: Union[str, Path]) -> Dict[str, Any]:
        """Extract structured OCR text, lines, words, bounding boxes from an image file."""
        path_str = str(image_path)
        if not os.path.exists(path_str):
            raise FileNotFoundError(f"Image not found for OCR: {path_str}")

        pix = self._lept_lib.pixRead(path_str.encode("utf-8"))
        if not pix:
            raise RuntimeError(f"Leptonica failed to read image file: {path_str}")

        return self._process_pix(pix)

    def extract_from_bytes(self, image_bytes: bytes) -> Dict[str, Any]:
        """Extract structured OCR text from raw image bytes in memory."""
        pix = self._lept_lib.pixReadMem(image_bytes, len(image_bytes))
        if not pix:
            raise RuntimeError("Leptonica failed to decode in-memory image bytes.")

        return self._process_pix(pix)

    def _process_pix(self, pix: int) -> Dict[str, Any]:
        tess = self._tess_lib
        lept = self._lept_lib

        api = tess.TessBaseAPICreate()
        if not api:
            p_pix = ctypes.c_void_p(pix)
            lept.pixDestroy(ctypes.byref(p_pix))
            raise RuntimeError("Failed to create Tesseract Base API instance.")

        try:
            rc = tess.TessBaseAPIInit3(
                api,
                self.tessdata_dir.encode("utf-8"),
                self.language.encode("utf-8"),
            )
            if rc != 0:
                raise RuntimeError(
                    f"TessBaseAPIInit3 failed with code {rc} for tessdata='{self.tessdata_dir}'."
                )

            tess.TessBaseAPISetImage2(api, pix)
            tess.TessBaseAPIRecognize(api, None)

            # Extract lines and words
            lines = self._extract_level_items(api, RIL_TEXTLINE)
            words = self._extract_level_items(api, RIL_WORD)

            full_text = "\n".join(item["text"] for item in lines if item["text"])

            return {
                "full_text": full_text,
                "lines": lines,
                "words": words,
                "word_count": len(words),
                "line_count": len(lines),
            }
        finally:
            p_pix = ctypes.c_void_p(pix)
            lept.pixDestroy(ctypes.byref(p_pix))
            tess.TessBaseAPIEnd(api)
            tess.TessBaseAPIDelete(api)

    def _extract_level_items(self, api: int, level: int) -> List[Dict[str, Any]]:
        tess = self._tess_lib
        items = []

        it = tess.TessBaseAPIGetIterator(api)
        if not it:
            return items

        try:
            left = ctypes.c_int()
            top = ctypes.c_int()
            right = ctypes.c_int()
            bottom = ctypes.c_int()

            while True:
                text_ptr = tess.TessResultIteratorGetUTF8Text(it, level)
                if text_ptr:
                    raw_str = ctypes.string_at(text_ptr).decode("utf-8", errors="replace").strip()
                    tess.TessDeleteText(text_ptr)

                    if raw_str:
                        tess.TessPageIteratorBoundingBox(
                            it,
                            level,
                            ctypes.byref(left),
                            ctypes.byref(top),
                            ctypes.byref(right),
                            ctypes.byref(bottom),
                        )
                        conf = tess.TessResultIteratorConfidence(it, level)

                        items.append(
                            {
                                "text": raw_str,
                                "bbox": [
                                    left.value,
                                    top.value,
                                    max(0, right.value - left.value),
                                    max(0, bottom.value - top.value),
                                ],
                                "confidence": round(max(0.0, min(1.0, conf / 100.0)), 2),
                            }
                        )

                if not tess.TessPageIteratorNext(it, level):
                    break
        finally:
            tess.TessPageIteratorDelete(it)

        return items


# Global singleton instance
ocr_engine = OCREngine()
