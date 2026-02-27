"""Public transcription service facade.

Implementation is in `mfaren.transcribe_pipeline` to keep this module small
and stable for imports across the app.
"""

from .transcribe_pipeline import get_live_path, list_models, pick_default_model, transcribe_file

__all__ = [
    "get_live_path",
    "list_models",
    "pick_default_model",
    "transcribe_file",
]
