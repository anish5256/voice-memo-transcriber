"""Roman-Hinglish spelling normalizer, for *scoring* (and optionally for display).

Only merges spellings of the SAME word. It deliberately never merges words that are different
in Hindi even if they look alike (mein "in" vs main "I", to/toh, me "me"), because that would
hide real errors. The groups are common function words, not tied to any recording.
"""
import re

GROUPS = {
    "hai": ["hai", "hei", "haai"],
    "hoon": ["hoon", "hun", "hu", "hoo", "huun"],
    "mein": ["mein", "mei", "meinn", "maein"],
    "nahi": ["nahi", "nahin", "nhi", "nahee", "nai"],
    "kya": ["kya", "kyaa", "kyah"],
    "toh": ["toh", "tho", "tooh"],
    "wo": ["wo", "woh", "vo", "voh"],
    "ye": ["ye", "yeh", "yah"],
    "aur": ["aur", "aor"],
    "bhi": ["bhi", "bhee"],
    "kuch": ["kuch", "kuchh"],
    "kyunki": ["kyunki", "kyonki", "kyuki", "kuki", "kyunke"],
    "theek": ["theek", "thik", "thek", "teek", "teeke", "theeke"],
    "haan": ["haan", "han", "haa", "haanji"],
    "abhi": ["abhi", "abi"],
    "achha": ["achha", "accha", "acha", "achchha", "achcha"],
    "samajh": ["samajh", "samaj", "samjh", "samagh"],
    "kaise": ["kaise", "kese", "kaisey"],
    "lekin": ["lekin", "lekan", "lakin"],
    "bahut": ["bahut", "bohot", "bahot", "bohut"],
    "thoda": ["thoda", "thora", "thodaa"],
    "jaana": ["jaana", "jana", "jaanaa"],
}
CANON = {v: k for k, vs in GROUPS.items() for v in vs}
_SPEAKER_TAG = re.compile(r"^\[[^\]]*\]\s*Speaker\s*\d+:\s*", re.M)


def strip_tags(text):
    return _SPEAKER_TAG.sub("", text)


def tokens(text, normalize=True):
    toks = re.findall(r"[a-z0-9']+", strip_tags(text).lower())
    return [CANON.get(t, t) for t in toks] if normalize else toks


def changed(text):
    """Tokens the normalizer rewrites, for auditing what it actually does."""
    return [(t, CANON[t]) for t in tokens(text, normalize=False) if t in CANON and CANON[t] != t]
