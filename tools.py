"""Audio tools and speech-model preparation shared by the bundled engine."""
from functools import lru_cache
import math
import os
from pathlib import Path
import re
import subprocess
import sys

WHISPER = 'mlx-community/whisper-large-v3-mlx'
MODEL_MESSAGE = 'Downloading the speech model (about 3 GB, one time)…'


@lru_cache(maxsize=1)
def ffmpeg():
    override = os.environ.get('MEETINGNOTES_FFMPEG')
    if override and Path(override).is_file() and os.access(override, os.X_OK):
        return override
    try:
        import imageio_ffmpeg
    except ImportError:
        return 'ffmpeg'
    return imageio_ffmpeg.get_ffmpeg_exe()


def audio_duration(path):
    """Decode the audio completely; never trust a damaged file's header alone."""
    try:
        result = subprocess.run(
            [ffmpeg(), '-nostdin', '-hide_banner', '-nostats', '-stats', '-xerror',
             '-i', str(path), '-map', '0:a:0', '-f', 'null', '-'],
            capture_output=True, text=True, errors='replace', check=True)
        # Prefer the final decoded timestamp, including for containers without duration metadata.
        times = re.findall(r'time=\s*(\d+):(\d+):(\d+(?:\.\d+)?)', result.stderr)
        if not times:
            times = re.findall(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', result.stderr)
        if times:
            h, m, s = map(float, times[-1])
            value = h * 3600 + m * 60 + s
            if math.isfinite(value) and value > 0:
                return value
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f'Could not read audio file: {path}') from exc
    raise ValueError(f'Could not read audio file or audio is empty: {path}')


def is_readable_audio(path):
    try:
        return audio_duration(path) > 0
    except ValueError:
        return False


def ensure_speech_model(before_download):
    """Notify only on a cache miss, before allowing a network download."""
    from huggingface_hub import snapshot_download
    try:
        snapshot_download(WHISPER, local_files_only=True)
        return
    except Exception:
        pass
    before_download()
    try:
        snapshot_download(WHISPER)
    except Exception as exc:
        raise ValueError('Could not download the speech model. Check your internet connection.') from exc


def python_command():
    """This interpreter for child scripts, with the same isolation and bytecode-cache location.
    Children started with -I ignore PYTHON* env vars, so the app's -X pycache_prefix must be passed on,
    otherwise they write __pycache__ into the (signed, read-only) app bundle."""
    command = [sys.executable, '-I']
    if sys.pycache_prefix:
        command += ['-X', f'pycache_prefix={sys.pycache_prefix}']
    return command
