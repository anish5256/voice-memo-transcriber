#!/usr/bin/env python3
"""Recoverable live recording transcription. Only .done audio enters the timeline."""
import argparse
import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

import numpy as np
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcribe as tr
from tools import ffmpeg, audio_duration, ensure_speech_model


def atomic(path, write):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def text_write(path, text):
    atomic(path, lambda f: f.write(text.encode('utf-8')))


def discover(session):
    directory = Path(session) / 'segments'
    return sorted((p.with_suffix('.m4a') for p in directory.glob('seg-*.done')
                   if re.fullmatch(r'seg-\d{5}\.done', p.name)),
                  key=lambda p: int(p.stem[4:]))


def duration(path):
    return audio_duration(path)


# Source attribution uses decoded mono samples, with full scale equal to 1.
SYS_ACTIVE_DBFS = -45.
MIC_MARGIN_DB = 10.
SYSTEM_NOTE = "Speaker 1 is the person who recorded (microphone); other speakers came through the call audio."


def system_enabled(session):
    return bool(json.loads((Path(session) / 'session.json').read_text()).get('system_audio', False))


def decode_track(path):
    result = subprocess.run([ffmpeg(), '-v', 'error', '-i', str(path),
                             '-ac', '1', '-ar', str(tr.SR), '-f', 'f32le', '-'],
                            check=True, capture_output=True)
    samples = np.frombuffer(result.stdout, dtype='<f4')
    if not len(samples):
        raise ValueError('Empty audio track')
    return samples


def rms_db(samples, start, end):
    first, last = max(0, int(start * tr.SR)), max(0, int(end * tr.SR))
    # Samples beyond the shorter track are silence in the longest-duration mix.
    clip = samples[first:last].astype(np.float64)
    power = np.sum(clip * clip) / max(1, last - first)
    return 10 * math.log10(power) if power > 0 else -math.inf


def source_for(mic, system, start, end):
    mic_db, sys_db = rms_db(mic, start, end), rms_db(system, start, end)
    if mic_db == sys_db == -math.inf:
        return None  # No signal on either track; finalize uses a labelled neighbour.
    return 'sys' if sys_db >= SYS_ACTIVE_DBFS and mic_db < sys_db + MIC_MARGIN_DB else 'mic'


def prepare_audio(path, enabled, log):
    """Validate system capture before committing a mix; failures retain mic audio."""
    if not enabled:
        return path, None
    system = path.with_suffix('.sys.m4a')
    try:
        samples = decode_track(system)
    except Exception as exc:
        print(f'Mic-only segment {path.name}: missing/unreadable {system.name}: {exc}',
              file=log, flush=True)
        return path, None
    mix = path.with_suffix('.mix.m4a')
    if not mix.exists():
        tmp = path.with_suffix('.mix.tmp.m4a')
        try:
            subprocess.run([ffmpeg(), '-v', 'error', '-y', '-i', str(path), '-i', str(system),
                            '-filter_complex', 'amix=inputs=2:duration=longest:normalize=0',
                            '-ac', '1', '-ar', '48000', '-c:a', 'aac', str(tmp)],
                           check=True, capture_output=True)
            os.replace(tmp, mix)
        finally:
            tmp.unlink(missing_ok=True)
    return mix, samples


HINDI_WORDS = set("""hai hain ho hoon hu ka ke ki mein me toh to nahi nahin kya se ko
par pe aur ye yeh wo woh kar karna karke raha rahi rahe tha thi the bhi hi jo jab
kuch koi mera meri mere aap tum hum main mai kaise kyun kyunki lekin agar matlab""".split())


def looks_english(text):
    words = re.findall(r"[^\W\d_]+", text.lower())
    return len(words) >= 5 and not HINDI_WORDS.intersection(words)


def speech_pieces(mask, duration):
    """Split at the longest >=300 ms silence; merge short neighbouring pieces.

    Boundaries are unpadded. An isolated utterance shorter than four seconds is
    retained; padding is applied only when slicing audio for Whisper.
    """
    mask = np.asarray(mask, dtype=bool)[:int(math.ceil(duration * 10))]
    active = np.flatnonzero(mask)
    if not len(active):
        return []

    def split(a, b):
        if b - a <= 15 + 1e-9:
            return [(a, b)]
        silences = []
        i = int(round(a * 10))
        while i < min(len(mask), int(round(b * 10))):
            if mask[i]:
                i += 1
                continue
            start = i
            while i < len(mask) and i / 10 < b and not mask[i]:
                i += 1
            if i - start >= 3 and start / 10 > a and i / 10 < b:
                silences.append((i - start, start / 10, i / 10))
        if silences:
            _, left, right = max(silences, key=lambda gap: gap[0])
            pieces = split(a, left) + split(right, b)
        else:
            pieces = [(a, a + 15)] + split(a + 15, b)
        # Merge a short piece into the smaller neighbour, then cap the merged
        # span without recreating the same silence boundary.
        i = 0
        while len(pieces) > 1 and i < len(pieces):
            if pieces[i][1] - pieces[i][0] >= 4 - 1e-9:
                i += 1
                continue
            j = (i - 1 if i == len(pieces) - 1 or
                 (i > 0 and pieces[i-1][1] - pieces[i-1][0] <=
                  pieces[i+1][1] - pieces[i+1][0]) else i + 1)
            lo, hi = sorted((i, j))
            start, end = pieces[lo][0], pieces[hi][1]
            merged = []
            while end - start > 15 + 1e-9:
                cut = min(start + 15, end - 4)
                merged.append((start, cut))
                start = cut
            pieces[lo:hi+1] = merged + [(start, end)]
            i = hi
        return pieces

    return split(active[0] / 10, min(duration, (active[-1] + 1) / 10))


class Engine:
    def __init__(self, models=None, prompt=None):
        self.models = models or Path(__file__).resolve().parent / 'models'
        self.prompt = prompt
        self.embedders = None
        self.detector = None
        self._mask_cache = None

    @contextlib.contextmanager
    def audio(self, path):
        import soundfile as sf
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / 'audio.wav'
            subprocess.run([ffmpeg(), '-v', 'error', '-y', '-i', str(path),
                            '-ac', '1', '-ar', str(tr.SR), str(wav)],
                           check=True, capture_output=True)
            yield sf.read(wav, dtype='float32')[0]

    def segment_mask(self, path, x):
        # Keep only the current segment, shared by transcription and embeddings.
        key = (Path(path), len(x))
        if self._mask_cache is None or self._mask_cache[0] != key:
            if self.detector is None:
                self.detector = tr.segmentation_model(self.models)
            self._mask_cache = (key, tr.speech_mask(x, self.models, self.detector))
        return self._mask_cache[1]

    def transcribe_segment(self, path):
        with self.audio(path) as x:
            mask = self.segment_mask(path, x)
            rows = []
            for start, end in speech_pieces(mask, len(x) / tr.SR):
                first = max(0, int((start - .2) * tr.SR))
                clip = x[first:int((end + .2) * tr.SR)]
                hing = tr.transcribe_result(clip, 'hing', self.prompt)
                if looks_english(hing['text']):
                    prompt = (tr.DEFAULT_HINGLISH_PROMPT + " " +
                              "Ye transcript Roman Hindi (Hinglish) mein likha jaata hai, English translation nahi.")
                    retry = tr.transcribe_result(clip, 'hing', prompt, temperature=.2)
                    if not looks_english(retry['text']):
                        hing = retry
                eng = tr.transcribe_result(clip, 'en', self.prompt)['segments']
                clean = lambda t: ' '.join(t.split())
                for h in hing['segments']:
                    if h['end'] > h['start']:
                        rows.append(dict(
                            start=float(h['start']) + first / tr.SR,
                            end=float(h['end']) + first / tr.SR,
                            hinglish=clean(h['text']),
                            english=clean(' '.join(e['text'] for e in eng
                                if e['start'] < h['end'] and e['end'] > h['start']))))
            return rows

    def embed_segment(self, path):
        with self.audio(path) as x:
            mask = self.segment_mask(path, x)
            windows = [w for w in tr.windows(0, len(x) / tr.SR)
                       if mask[int(w[0]*10):int(w[1]*10)].mean() > .6]
            if self.embedders is None:
                self.embedders = [tr.Embedder(self.models / name) for name in (
                    '3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx',
                    'nemo_en_titanet_small.onnx')]
            return dict(windows=np.asarray(windows).reshape(-1, 2),
                        **{f'embeddings{i}': np.asarray([e(x, *w) for w in windows])
                           for i, e in enumerate(self.embedders)})


ENGINE = Engine()


@contextlib.contextmanager
def lock(session, name):
    with (Path(session) / name).open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def event(kind, **fields):
    print(json.dumps(dict(event=kind, **fields), ensure_ascii=False), flush=True)


def scan(session, log, emit_events=False, strict=False):
    """Commit embeddings before JSON (the completion marker); regenerate live text."""
    readable, live, offset = [], [], 0.
    enabled = system_enabled(session)
    for path in discover(session):
        try:
            audio, system = prepare_audio(path, enabled, log)
            length = duration(audio)
        except Exception as exc:
            print(f'Skipping unreadable segment {path.name}: {exc}', file=log, flush=True)
            if emit_events:
                event('segment_error', index=int(path.stem[4:]), message=str(exc))
            continue
        try:
            jp, npz = path.with_suffix('.json'), path.with_suffix('.npz')
            if not jp.exists() or not npz.exists():
                # Reuse either completed half after an interrupted pair of writes.
                if not npz.exists():
                    vectors = ENGINE.embed_segment(path.with_suffix('.sys.m4a') if system is not None else path)
                    atomic(npz, lambda f: np.savez(f, **vectors))
                if not jp.exists():
                    rows = ENGINE.transcribe_segment(audio)
                    if system is not None:
                        mic = decode_track(path)
                        rows = [dict(r, source=source_for(mic, system, r['start'], r['end']))
                                for r in rows]
                    text_write(jp, json.dumps(rows, ensure_ascii=False, allow_nan=False))
                    if emit_events:
                        event('segment', index=int(path.stem[4:]),
                              text=' '.join(r['hinglish'] for r in rows))
            rows = json.loads(jp.read_text(encoding='utf-8'))
            with np.load(npz, allow_pickle=False) as data:
                vectors = {key: data[key] for key in data.files}
            readable.append((audio, offset, length, rows, vectors))
            for row in rows:
                start = offset + row['start']
                live.append(f"[{int(start // 60)}:{int(start % 60):02d}] {row['hinglish']}")
        except Exception as exc:
            print(f'Segment processing failed {path.name}: {exc}', file=log, flush=True)
            if strict:
                raise
            if emit_events:
                event('segment_error', index=int(path.stem[4:]), message=str(exc))
        # A readable recording contributes even if its model inference failed.
        offset += length
    text_write(Path(session) / 'live.txt', '\n'.join(live) + ('\n' if live else ''))
    return readable


def watch(session, poll=0.5, log=None):
    session = Path(session)
    with lock(session, '.watch.lock'):
        ensure_speech_model(lambda: event('downloading_model'))
        while True:
            with lock(session, '.process.lock'):
                scan(session, log or sys.stderr, True)
            if (session / 'STOP').exists():
                with lock(session, '.process.lock'):
                    scan(session, log or sys.stderr, True)
                event('stopped')
                return 0
            time.sleep(poll)


def watcher_running(session):
    with (Path(session) / '.watch.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


def assign_speakers(windows, labels, rows):
    """Count overlapping windows; ties use the earliest voice, silence the nearest window."""
    order = {}
    for label in labels:
        order.setdefault(int(label), len(order) + 1)
    result = []
    for row in rows:
        a, b = row['start'], row['end']
        votes = {}
        for (start, end), label in zip(windows, labels):
            if start < b and end > a:
                speaker = order[int(label)]
                votes[speaker] = votes.get(speaker, 0) + 1
        if votes:
            speaker = min(votes, key=lambda k: (-votes[k], k))
        elif len(windows):
            nearest = min(range(len(windows)),
                          key=lambda i: max(windows[i][0]-b, a-windows[i][1], 0))
            speaker = order[int(labels[nearest])]
        else:
            speaker = 1
        result.append((a, b, speaker, row['hinglish'], row['english']))
    # Whisper's first voice may precede the first embedding window.
    first = {}
    return [(a, b, first.setdefault(s, len(first)+1), h, e) for a, b, s, h, e in result]


def assign_call_speakers(windows, labels, rows):
    remote = iter(assign_speakers(windows, labels,
                                 [r for r in rows if r.get('source', 'mic') == 'sys']))
    pieces = []
    for row in rows:
        source = row.get('source', 'mic')
        if source == 'sys':
            a, b, speaker, hing, eng = next(remote)
            pieces.append((a, b, speaker + 1, hing, eng))
        else:
            pieces.append((row['start'], row['end'], 1 if source == 'mic' else None,
                           row['hinglish'], row['english']))
    labelled = [p for p in pieces if p[2] is not None]
    for i, (a, b, speaker, hing, eng) in enumerate(pieces):
        if speaker is None:
            nearest = min(labelled, key=lambda p: max(p[0]-b, a-p[1], 0)) if labelled else None
            pieces[i] = (a, b, nearest[2] if nearest else 1, hing, eng)
    return pieces


def finalize(session, work, log, wait_seconds=600):
    session, work = Path(session), Path(work)
    deadline = time.monotonic() + wait_seconds
    while watcher_running(session) and time.monotonic() < deadline:
        if (session / 'STOP').exists() and all(
                p.with_suffix('.json').exists() and p.with_suffix('.npz').exists()
                for p in discover(session)):
            break
        time.sleep(min(.5, max(0, deadline-time.monotonic())))
    with lock(session, '.process.lock'):
        segments = scan(session, log, strict=True)
    if not segments:
        raise ValueError('No readable, successfully processed segments in live session')
    dual = any(p.name.endswith('.mix.m4a') for p, *_ in segments)
    all_windows, matrices, rows = [], [[], []], []
    for path, offset, length, transcript, data in segments:
        w = np.asarray(data['windows']).reshape(-1, 2)
        if dual and not path.name.endswith('.mix.m4a'):
            w = w[:0]
        all_windows.extend(w + offset)
        for i in range(2):
            if len(w):
                matrices[i].append(data[f'embeddings{i}'])
        rows.extend(dict(r, start=r['start']+offset, end=r['end']+offset) for r in transcript)
    windows = np.asarray(all_windows).reshape(-1, 2)
    labels = np.zeros(len(windows), dtype=int)
    if len(windows) >= 8:
        emb = [np.concatenate(m) for m in matrices]
        sim = np.mean([m @ m.T for m in emb], axis=0)
        labels, _ = tr.spectral_cluster(sim)
        if len(set(labels)) == 1 or tr.separation(sim, labels) < tr.SINGLE_SPEAKER_SEPARATION:
            labels[:] = 0
    if dual:
        pieces = assign_call_speakers(windows, labels, rows)
    else:
        pieces = assign_speakers(windows, labels, rows)
    audio = session / 'recording.m4a'
    # ffconcat quoting follows ffmpeg's token syntax, including apostrophes in paths.
    listing = session / 'concat.txt'
    text_write(listing, ''.join("file '" + str(p.resolve()).replace("'", "'\\''") + "'\n"
                               for p, *_ in segments))
    tmp = session / 'recording.tmp.m4a'
    result = subprocess.run([ffmpeg(), '-v', 'error', '-y', '-f', 'concat', '-safe', '0',
                    '-i', str(listing), '-c', 'copy', str(tmp)],
                   check=True, capture_output=True, text=True)
    log.write(result.stderr)
    log.flush()
    os.replace(tmp, audio)
    work.mkdir(parents=True, exist_ok=True)
    for name, column in [('Hinglish', 3), ('English', 4)]:
        text_write(work / f'recording.{name}.txt', '\n'.join(
            f'[{int(a // 60)}:{a % 60:04.1f}] Speaker {s}: {piece[column]}'
            for piece in pieces for a, b, s, h, e in [piece] if piece[column]))
    glossary = tr.DEFAULT_GLOSSARY.read_text(encoding='utf-8').strip() if tr.DEFAULT_GLOSSARY.exists() else '(none provided)'
    bundle = tr.bundle_text([(a, s, h, e) for a, b, s, h, e in pieces], glossary)
    if dual:
        bundle = bundle.replace('## Segments\n\n', '## Speakers\n\n' + SYSTEM_NOTE + '\n\n## Segments\n\n', 1)
    text_write(work / 'recording.for-llm.md', bundle)
    text_write(work / 'pieces.json', json.dumps([(a, b, s) for a, b, s, h, e in pieces]))
    return audio, len({p[2] for p in pieces})


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=['watch'])
    ap.add_argument('session', type=Path)
    ap.add_argument('--models-dir', type=Path, default=Path(__file__).resolve().parent / 'models')
    ap.add_argument('--prompt-file', type=Path)
    args = ap.parse_args()
    global ENGINE
    ENGINE = Engine(args.models_dir, args.prompt_file.read_text().strip() if args.prompt_file else None)
    return watch(args.session)


if __name__ == '__main__':
    sys.exit(main())
