"""Compatibility facade for transcription service.

This module keeps the original import path `mfaren.transcriber` stable while
implementation lives in `mfaren.transcribe_service`.
"""

from .transcribe_service import get_live_path, list_models, pick_default_model, transcribe_file

__all__ = [
    "get_live_path",
    "list_models",
    "pick_default_model",
    "transcribe_file",
]
