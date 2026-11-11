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


def model_version(name):
    """Comparable numeric version, independent of marketing suffixes."""
    match = re.search(r'\d+(?:[.-]\d+)*', name)
    return tuple(map(int, re.split(r'[.-]', match[0]))) if match else ()


def cheap_model(name):
    return any(word in name.lower() for word in ('haiku', 'luna', 'mini', 'nano'))


def select_codex_cheap(catalog):
    """Pure selection from the CLI catalog; hidden models are never selected."""
    if isinstance(catalog, dict):   # `codex debug models` prints {"models": [...]}
        catalog = catalog.get('models', [])
    candidates = [m['slug'] for m in catalog if m.get('visibility') == 'list'
                  and ('luna' in m.get('slug', '').lower() or
                       re.search(r'fast|affordable|efficient', m.get('description', ''), re.I))]
    return max(candidates, key=lambda name: (model_version(name), name), default='')


@lru_cache(maxsize=1)
def codex_cheap_model():
    import json
    try:
        result = subprocess.run(['codex', 'debug', 'models'], capture_output=True,
                                text=True, check=True, timeout=30)
        return select_codex_cheap(json.loads(result.stdout))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        return ''


@lru_cache(maxsize=1)
def anthropic_models():
    import anthropic
    try:
        return tuple(m.id for m in anthropic.Anthropic().models.list())
    except Exception:
        return ()


def select_anthropic_model(models, tier):
    family = 'haiku' if tier == 'cheap' else 'opus'
    candidates = [name for name in models if family in name.lower()]
    return max(candidates, key=lambda name: (model_version(name), name),
               default=f'claude-{family}-5-5')


def anthropic_tier_model(tier):
    return select_anthropic_model(anthropic_models(), tier)


def resolve_model(provider, model):
    if model and model not in ('tier:cheap', 'tier:default'):
        return model
    tier = 'cheap' if model == 'tier:cheap' else 'default'
    if provider == 'claude':
        return 'haiku' if tier == 'cheap' else 'opus'
    if provider == 'codex':
        return codex_cheap_model() if tier == 'cheap' else ''
    if provider == 'anthropic':
        return anthropic_tier_model(tier)
    return 'gpt-5' if provider == 'proxy' else ''
