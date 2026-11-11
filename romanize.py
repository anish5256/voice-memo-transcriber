"""Devanagari -> readable Roman Hinglish (no dependencies).

Whisper transcribes Hindi far more accurately as Devanagari than when it is steered into Roman
script with a prompt (the prompt gets echoed into unclear audio). We decode as Hindi and convert here,
so everything downstream (glossary, cleaning, summaries) still sees Roman Hinglish. Latin text
(English words Whisper keeps in Latin) passes through untouched.
"""
import re

CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h",
    "\u0958": "q", "\u0959": "kh", "\u095a": "g", "\u095b": "z", "\u095c": "d", "\u095d": "dh", "\u095e": "f",
    "\u095f": "y",
}
NUKTA_FORMS = {"क": "\u0958", "ख": "\u0959", "ग": "\u095a", "ज": "\u095b", "ड": "\u095c", "ढ": "\u095d",
               "फ": "\u095e", "य": "\u095f"}
INDEP = dict(zip("अआइईउऊऋएऐओऔ", ["a", "a", "i", "i", "u", "u", "ri", "e", "ai", "o", "au"]))
MATRA = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au",
         "ॅ": "e", "ॉ": "o"}
NASAL = {"ं": "n", "ँ": "n", "ः": "h"}
VIRAMA, NUKTA = "\u094d", "\u093c"
DIGITS = dict(zip("०१२३४५६७८९", "0123456789"))
DEV = re.compile("[ऀ-ॿ]+")


def _word(w):
    out, i = [], 0
    while i < len(w):
        c = w[i]
        if c in CONS:
            if i + 1 < len(w) and w[i + 1] == NUKTA:      # decomposed nukta: fold into the letter
                c = NUKTA_FORMS.get(c, c)
                i += 1
            out.append(CONS[c])
            nxt = w[i + 1] if i + 1 < len(w) else ""
            if nxt in MATRA:
                out.append(MATRA[nxt]); i += 1
            elif nxt == VIRAMA:
                i += 1
            elif i + 1 < len(w) and nxt not in NASAL:
                out.append("a")                         # inherent vowel inside a word
            elif i + 1 < len(w):                        # inherent vowel before anusvara
                out.append("a")
            # word-final inherent vowel is silent in Hindi: add nothing
        elif c in INDEP:
            out.append(INDEP[c])
        elif c in NASAL:
            out.append(NASAL[c])
        elif c in DIGITS:
            out.append(DIGITS[c])
        elif c in (NUKTA, VIRAMA):
            pass
        elif c in MATRA:
            out.append(MATRA[c])
        elif c == "।":
            out.append(".")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def romanize(text):
    return DEV.sub(lambda m: _word(m.group(0)), text)
