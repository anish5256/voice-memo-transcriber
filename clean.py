#!/usr/bin/env python3
"""Clean a `.for-llm.md` bundle in two stages (local LLM via Ollama).

  1. Exact glossary replacement, in code: every `(often heard as "X")` entry in the bundle's
     glossary is applied as a whole-word, case-insensitive replacement. Small local models ignore
     glossaries, so this is not left to the model.
  2. LLM spelling/grammar repair, in chunks (the transcript is too long for one prompt). A segment
     is only accepted if its length stays within a safe range of the original: a model that
     summarises, drops or invents text is rejected and the original is kept.

  python clean.py out/memo.for-llm.md [--model gemma3:12b]

Writes next to the bundle: <name>.Hinglish.glossary-fixed.txt (stage 1) and
<name>.Hinglish.cleaned.txt (stage 1 + 2). The raw transcripts are never modified.
"""
import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

SEG = re.compile(r"^\[(\d+:\d+\.\d)\] Speaker (\d+)\n  Hinglish: (.*)\n  English:  (.*)$", re.M)
ONE_LINE = re.compile(r"^\[(\d+:\d+\.\d)\] Speaker (\d+):\s*(\S.*)$", re.M)
TWO_LINE = re.compile(r"^\[(\d+:\d+\.\d)\] Speaker (\d+)[ \t]*\n[ \t]+(\S.*)$", re.M)
NONE = "[nothing recognised]"

HEAD = """You are an editor who repairs machine-transcribed Hindi/Hinglish. Each segment has two noisy transcripts of the same speech.

YOU MUST EDIT. Returning the text unchanged is a failure. Rules:
1. Rewrite the HINGLISH text with real mistakes fixed: misspelled Hindi words (use the English version to see what was meant), wrong verb forms, garbled words.
2. Names and terms already match the glossary. Keep them exactly as written.
3. Keep the speaker's own words and colloquial forms (for example "merko", "mere ko", "isko", "usko" stay as they are). Do not formalise, summarise, shorten or add facts.
4. If a word is truly unrecoverable, keep it and put [?] after it. Use [?] sparingly.
5. Output ONLY one line per segment, in the form `[time] Speaker N: corrected Hinglish`. No translation, no notes, no lists.

Worked example (unrelated memo).
Glossary: Rohan
Input:
[0:05.0] Speaker 1
  Hinglish: Kal client ke sath meeting hai, mai Rohan ko presentation bhejna bhool gaya, kya aap forward garke de sakte ho? Merko lagta hai wo dekh lega.
  English: Tomorrow there is a meeting with the client, I forgot to send the presentation to Rohan, can you forward it? I think he will look at it.
Output:
[0:05.0] Speaker 1: Kal client ke saath meeting hai, main Rohan ko presentation bhejna bhool gaya, kya aap forward karke de sakte ho? Merko lagta hai wo dekh lega.

"""


def glossary_rules(text):
    """(regex, canonical) pairs from lines like: - Acme Corp   (often heard as "Akme"; a customer)"""
    rules = []
    for line in text.splitlines():
        m = re.search(r"\(often heard as ([^)]*)\)", line)
        if not m:
            continue
        prefix = line[:m.start()].strip().lstrip("-").strip()
        canon = re.split(r",| {2,}", prefix)[-1].strip()
        for v in re.findall(r'"([^"]+)"', m.group(1)):
            if canon and v.lower() != canon.lower():
                rules.append((re.compile(r"(?<!\w)" + re.escape(v) + r"(?!\w)", re.I), canon))
    return rules


def apply_rules(text, rules):
    for rx, canon in rules:
        text = rx.sub(canon, text)
    return text


def ask(model, prompt, url):
    req = urllib.request.Request(f"{url}/api/chat", data=json.dumps({
        "model": model, "stream": False, "think": False, "keep_alive": "30m",
        "options": {"temperature": 0.2, "num_ctx": 8192},
        "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=1800))["message"]["content"]


def parse_output(text):
    found = {m[1]: m[3].strip() for m in ONE_LINE.finditer(text)}
    for m in TWO_LINE.finditer(text):
        found.setdefault(m[1], m[3].strip())
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--model", default="gemma3:12b")
    ap.add_argument("--ollama-url", default="http://localhost:11434")
    ap.add_argument("--chunk-chars", type=int, default=3000)
    ap.add_argument("--min-ratio", type=float, default=0.75, help="reject output shorter than this x original")
    ap.add_argument("--max-ratio", type=float, default=1.35, help="reject output longer than this x original")
    a = ap.parse_args()

    text = a.bundle.read_text(encoding="utf-8")
    glossary = text.split("## Glossary", 1)[1].split("## Segments", 1)[0].strip()
    segs = [dict(t=m[1], spk=m[2], hing=m[3], eng=m[4]) for m in SEG.finditer(text)]
    rules = glossary_rules(glossary)
    print(f"{len(segs)} segments, {len(rules)} glossary rules", flush=True)

    # stage 1: exact glossary replacement
    for s in segs:
        s["g"] = s["hing"] if s["hing"] == NONE else apply_rules(s["hing"], rules)
        s["e"] = apply_rules(s["eng"], rules)
    changed = sum(s["g"] != s["hing"] for s in segs)
    stem = a.bundle.name.removesuffix(".for-llm.md")
    out1 = a.bundle.with_name(f"{stem}.Hinglish.glossary-fixed.txt")
    out1.write_text("\n".join(f"[{s['t']}] Speaker {s['spk']}: {s['g']}" for s in segs), encoding="utf-8")
    print(f"stage 1: {changed} segments changed by the glossary -> {out1.name}", flush=True)

    # stage 2: LLM repair in chunks
    chunks, cur, size = [], [], 0
    for s in segs:
        if cur and (size + len(s["g"]) + len(s["e"]) > a.chunk_chars or len(cur) >= 14):
            chunks.append(cur)
            cur, size = [], 0
        cur.append(s)
        size += len(s["g"]) + len(s["e"])
    if cur:
        chunks.append(cur)

    accepted = rejected = missing = 0
    t0 = time.time()
    for i, ch in enumerate(chunks, 1):
        body = "\n\n".join(f"[{s['t']}] Speaker {s['spk']}\n  Hinglish: {s['g']}\n  English: {s['e']}" for s in ch)
        prompt = HEAD + f"Glossary (already applied):\n{glossary}\n\nNow the real task.\n\n" + body + "\n"
        try:
            got = parse_output(ask(a.model, prompt, a.ollama_url))
        except Exception as e:   # keep going; those segments keep their stage-1 text
            print(f"  chunk {i}: model call failed ({e}); keeping originals", flush=True)
            got = {}
        for s in ch:
            new = got.get(s["t"])
            s["c"] = s["g"]
            if s["g"] == NONE:
                continue
            if not new:
                missing += 1
            elif a.min_ratio <= len(new) / max(1, len(s["g"])) <= a.max_ratio:
                s["c"] = new
                accepted += 1
            else:
                rejected += 1
        eta = (time.time() - t0) / i * (len(chunks) - i)
        print(f"  chunk {i}/{len(chunks)} done | accepted {accepted} rejected {rejected} missing {missing} | ~{eta / 60:.0f} min left", flush=True)

    out2 = a.bundle.with_name(f"{stem}.Hinglish.cleaned.txt")
    out2.write_text("\n".join(f"[{s['t']}] Speaker {s['spk']}: {s['c']}" for s in segs), encoding="utf-8")
    print(f"stage 2: accepted {accepted}, rejected (kept original) {rejected}, missing from model output {missing} -> {out2.name}", flush=True)


if __name__ == "__main__":
    main()
