"""
Shared SenseVoice transcription module.

Uses Alibaba's SenseVoiceSmall model for speech recognition.
Supports Chinese, English, Japanese, Korean.
Runs fully offline after initial model download (~1GB).
"""

import os
import re
import tempfile
import numpy as np

_model = None


# Common misrecognitions → corrections
# SenseVoice doesn't know proper nouns like "Claude", add more as needed
CORRECTIONS = {
    "cloud code": "Claude Code",
    "Cloud Code": "Claude Code",
    "cloud cord": "Claude Code",
    "clad code": "Claude Code",
    "claud code": "Claude Code",
    "klod code": "Claude Code",
    "cloud": "Claude",
    "Cloud": "Claude",
}


def clean_text(text):
    """Remove SenseVoice metadata tags and fix common misrecognitions."""
    text = re.sub(r"<\|[^|]*\|>", "", text).strip()
    for wrong, right in CORRECTIONS.items():
        text = text.replace(wrong, right)
    return text


def load_model():
    """Load SenseVoiceSmall model (downloads ~1GB on first run)."""
    global _model
    if _model is not None:
        return _model

    # soundfile patch for environments without ffmpeg/torchcodec
    try:
        import torch
        import torchaudio
        import soundfile as sf

        def _sf_load(filepath, **kwargs):
            data, sr = sf.read(filepath, dtype="float32")
            if data.ndim == 1:
                data = data[np.newaxis, :]
            else:
                data = data.T
            return torch.from_numpy(data), sr

        torchaudio.load = _sf_load
    except Exception:
        pass

    print("Loading SenseVoiceSmall model...", flush=True)
    print("(First run downloads ~1GB from ModelScope)", flush=True)
    from funasr import AutoModel

    _model = AutoModel(
        model="iic/SenseVoiceSmall",
        trust_remote_code=True,
        disable_update=True,
    )
    print("Model loaded!", flush=True)
    return _model


def transcribe_file(file_path, language="zh", use_itn=True):
    """Transcribe an audio file. Returns cleaned text or empty string."""
    model = load_model()
    result = model.generate(input=file_path, language=language, use_itn=use_itn)
    if result and len(result) > 0:
        text = result[0].get("text", "")
        return clean_text(text)
    return ""


def transcribe_audio(audio_data, sample_rate=16000, language="zh", use_itn=True):
    """Transcribe numpy audio array. Returns cleaned text or empty string."""
    import soundfile as sf

    model = load_model()
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        audio_float = audio_data.flatten().astype(np.float32)
        sf.write(tmp_path, audio_float, sample_rate)
        result = model.generate(input=tmp_path, language=language, use_itn=use_itn)
        if result and result[0].get("text"):
            return clean_text(result[0]["text"])
        return ""
    finally:
        os.unlink(tmp_path)
