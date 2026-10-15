"""quote_answer.py -- the line that answers, quoted from the files an ask already found. No model call.

Opt-in (ask.py --quote, or "quote" in ask --json). After the normal search returns its files, this
picks one line from them and quotes it word for word with its file and line number, or says that no
line clearly answers. It never writes words of its own beyond two fixed templates.

How a line is picked, for one question:
  1. terms     the question's content words (ask.query_terms), each weighted by how rare it is
               across the lines of the files read, so a word every line shares counts for little
  2. score     a line earns the weight of each term it holds; a term found only in the line's
               heading or the file's title earns half (a "## Ports" heading counts for its rows)
  3. shape     the question word asks for a kind of answer (how many -> a number, when -> a date,
               where -> a path or place, who -> a name, phone -> a phone number); a line with
               that kind of value scores more, a line without it scores much less
  4. new words a line that only repeats the question is not an answer, unless it holds the kind of
               value asked for ("web view: 8080"); "which tool ...?" is answered only by a line (or
               its heading) that says "tool"
  5. margin    the best line is quoted only when it holds enough of the question and leads the
               runner-up line by a margin, unless the runner-up states the same value;
               otherwise the honest not-found template, naming the top file

A "line" is a passage as Markdown reads it: a list item, a table row, or a paragraph whose hard-wrapped
lines are joined, cited at its first line. Headings are context, never answers. Only notes (.md, .txt)
the search checked are quoted from: never code, never a word-search guess.

Cleanup is light: list bullets, heading marks, quote marks and bold/italic stars are dropped;
the words are never changed. A line is never quoted when it, or a line within SECRET_MARGIN of it,
looks like a secret (a secret can wrap across lines); only that small window is scanned, so a big
file costs no full secret scan.
"""
import math
import re

MAX_FILES = 5        # files read: the ask's own top five
QUOTE_CHARS = 240    # a longer line is shown as a window around its best match
MIN_COVER = 0.5      # share of the question's term weight the line (with its heading) must hold
MARGIN = 0.15        # lead over the runner-up line, as a share of the best score
TITLE_SHARE = 0.5    # weight of a term found only in the heading path or file title
MISS_SHAPE = 0.4     # score kept by a line without the kind of value the question asks for
HAS_SHAPE = 1.25     # score boost for a line with it
LENGTH_PULL = 0.25   # a paragraph longer than QUOTE_CHARS holds many words by chance: its score is
                     # divided by 1 + LENGTH_PULL * (its length - QUOTE_CHARS) / QUOTE_CHARS
RANK_PULL = 0.1      # a passage from the search's file k (0 = first) has its score divided by 1 + RANK_PULL * k
SECRET_MARGIN = 3    # lines either side of the quote scanned with it (toc_search.MARGIN)
NOTE_SUFFIXES = (".md", ".markdown", ".txt")  # a quote is a sentence from a note, never a line of code
MIN_PRESENT = 0.5    # share of the question's terms the files must hold somewhere, else none answers
# Words that name the kind of answer, not its subject ("how long", "his name"): the answer line holds
# a value instead, so they are not matched.
ASK_WORDS = {"name", "names", "long", "many", "much", "big", "large", "fast", "often", "far", "number"}
QUOTE_TIERS = ("confirmed", "saved")  # files the search checked; word-search guesses are not quoted

NOT_FOUND = "I found related files but no line that answers this."
NOTHING = "No file found, so there is nothing to quote."

_DATE = r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2})\b"
_TIME = r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b|\b\d{1,2}:\d{2}\b"
_NUM = r"\d[\d,._]*"
_PHONE = r"\(?\d{3}\)?[-. ]\d{3}[-.]\d{4}"
_PATH = r"(?:~|\.{0,2})/[\w.-]+/|\b[\w-]+\.(?:md|py|ts|js|json|sh|txt|yaml|yml|toml)\b|https?://|\b\d+ [A-Z][a-z]+ (?:St|Street|Ave|Avenue|Rd|Road|Blvd)\b"
_NAME = r"\b[A-Z][a-z]+ [A-Z][a-z]+\b|[\w.+-]+@[\w-]+\.[\w.]+"
_MONEY = r"[$€£]\s?\d[\d,.]*|\b\d[\d,.]*\s?(?:usd|dollars|eur|euros)\b"
_CODE = r"`[^`]+`|\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9-]{4,}\b"

# Question word -> the kind of value an answer holds. First match wins; no match asks for no shape.
# when / who / where ask only when they lead the question ("what happens when X" asks for no date).
SHAPES = (
    (re.compile(r"\b(?:price|cost|costs|fee|pay|paid|charge)\b"), _MONEY),
    (re.compile(r"\bphone\b|\bnumber to call\b|\bcall\b"), _PHONE),
    (re.compile(r"^\W*when\b|\bwhat (?:date|day|year)\b"), _DATE + "|" + _TIME),
    (re.compile(r"\bwhat time\b"), _TIME),
    (re.compile(r"\bhow (?:many|much|long|big|large|fast|old|often|far)\b|\b(?:what|which) (?:size|price|cost|port|version|percent)\b|"
                r"\b(?:price|cost|size|limit|cap|count|length)\b"), _NUM),
    (re.compile(r"^\W*(?:who|whose)\b|\bname\b"), _NAME),
    (re.compile(r"^\W*where\b"), _PATH),
    (re.compile(r"\b(?:part|model|serial) number\b|\bwhich (?:bulb|part|model|header|flag|file|command)\b|"
                r"\bwhat (?:bulb|part|model|header|flag|file|command)\b"), _CODE + "|" + _NUM),
)

_HEAD = re.compile(r"^\s{0,3}(#{1,6})\s+(.*\S)")
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_NOT_ANSWER = re.compile(r"^(?:see also\b|source:|sources:|<!--|---+$|```|\|?\s*-{3,})", re.I)


# "which tool ...?", "what license ...?": the answer names one, so the line (or its heading) must say the
# word. Not when "what" leads a verb ("what does", "what is"): those name no kind of thing.
_FOCUS = re.compile(r"\b(?:which|what)\s+([a-z][a-z-]+)")
_NOT_FOCUS = {"is", "are", "was", "were", "does", "do", "did", "can", "could", "should", "will", "would",
              "has", "have", "had", "the", "a", "an", "of", "to", "in", "on", "it", "they", "we", "i", "you", "time",
              "happens", "happened", "happen", "changed", "changes", "means", "made", "makes", "goes", "went"}


def focus_of(question: str) -> str:
    m = _FOCUS.search(question.lower())
    return m.group(1) if m and m.group(1) not in _NOT_FOCUS else ""


def shape_for(question: str):
    q = question.lower()
    for ask, shape in SHAPES:
        if ask.search(q):
            return re.compile(shape, 0 if _NAME in shape or _CODE in shape else re.I)  # names and codes are cased
    return None


def tidy(line: str) -> str:
    """Light cleanup only: bullets, heading and quote marks, bold/italic stars. Words are kept as written."""
    t = _HEAD.sub(r"\2", line).strip()
    t = re.sub(r"^>\s?", "", t)
    t = _BULLET.sub("", t)
    t = re.sub(r"\*\*|__", "", t)
    t = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"\1", t)
    return t.strip()


def _matcher(terms: list):
    """held(text): the terms a text holds. A term is held when a word of the text starts with it, or
    with its first five letters for a long word ("restarted" holds "restart"). Unlike ask.term_hits,
    never inside a word: "vin" is not in "having". One regex pass finds the candidate words."""
    keys = {t: t[:5] for t in terms}
    if not keys:
        return lambda text: set()
    words = re.compile(r"(?<![a-z0-9])(?:" + "|".join(sorted({re.escape(k) for k in keys.values()})) + r")[a-z0-9]*")

    def held(text: str) -> set:
        found = set(words.findall(text))
        return {t for t, k in keys.items() if any(w.startswith(k) for w in found)} if found else set()
    return held


def _window(text: str, terms: list, shape=None) -> str:
    """At most QUOTE_CHARS of a long line, centred on the value the question asks for, else on where
    its terms cluster, with ellipses."""
    if len(text) <= QUOTE_CHARS:
        return text
    low = text.lower()
    spots = [low.find(t[:5] if len(t) > 5 else t) for t in terms]
    spots = sorted(s for s in spots if s >= 0) or [0]
    m = shape.search(text) if shape else None
    mid = m.start() if m else spots[len(spots) // 2]
    a = max(0, min(mid - QUOTE_CHARS // 2, len(text) - QUOTE_CHARS))
    a = text.rfind(" ", 0, a) + 1 if a else 0
    b = a + QUOTE_CHARS
    b = text.find(" ", b) if text.find(" ", b) != -1 else len(text)
    return ("..." if a else "") + text[a:b].strip() + ("..." if b < len(text) else "")


_NEW_BLOCK = re.compile(r"^\s*(?:[-*+•>|]|\d+[.)]\s|#{1,6}\s|```)")


def lines_of(text: str):
    """(line number, tidy text, heading path) for each passage that could be an answer: a list item, a
    table row, or a paragraph whose hard-wrapped lines are joined (Markdown's own rule: a line that
    starts no new block continues the one above). Headings name topics, so they are context, not
    answers. The line number is the passage's first line."""
    heads, fence, out, prev = {}, False, [], ""
    for n, raw in enumerate(text.splitlines(), 1):
        if raw.strip().startswith("```"):
            fence, prev = not fence, ""
            continue
        m = None if fence else _HEAD.match(raw)
        if m:
            level = len(m.group(1))
            heads = {k: v for k, v in heads.items() if k < level}
            heads[level] = m.group(2)
            prev = ""
            continue
        if not fence and raw.strip() and prev.strip() and not _NEW_BLOCK.match(raw) and out and out[-1][0] == "open":
            out[-1][2] = f"{out[-1][2]} {raw.strip()}"
        else:
            out.append(["open" if raw.strip() and not fence else "shut", n, raw.strip(), " ".join(heads[k] for k in sorted(heads))])
        prev = raw if not fence else ""
    keep = []
    for _, n, t, head in out:
        t = tidy(t)
        if len(t) >= 12 and not _NOT_ANSWER.match(t):
            keep.append((n, t, head))
    return keep


_VALUE = re.compile(r"`[^`]+`")


def _values(text: str, shape, question: str) -> set:
    """The answer-like values a line states, lowercased: the asked-for kind ("600 s") and any `code`
    span. A value the question itself says is not an answer."""
    found = {m.group(0).lower().strip("`") for m in _VALUE.finditer(text)}
    if shape:
        found |= {m.group(0).lower() for m in shape.finditer(text)}
    q = question.lower()
    return {v for v in found if v.strip() and v not in q}


def pick(question: str, files: list, terms: list, read, secret=lambda s: False, trace=None) -> dict:
    """One quote from the ask's files, or why there is none. `files` are the ask's rows
    ({path, tier}), best first; `terms` the question's content words; `read(path)` the file's text
    (None when it cannot be read); `secret(text)` the secret scan. Returns {"found": True, "text",
    "path", "line"} or {"found": False, "why", "top"?}. `trace`, a list, gets the five best scored
    lines (for measuring)."""
    files = files[:MAX_FILES]
    if not files:
        return {"found": False, "why": NOTHING}
    top = files[0]["path"]
    files = [f for f in files if f.get("tier") in QUOTE_TIERS and f["path"].lower().endswith(NOTE_SUFFIXES)]
    if not files:
        return {"found": False, "why": NOT_FOUND, "top": top}
    terms = [t.lower() for t in terms if t.lower() not in ASK_WORDS]
    if not terms:
        return {"found": False, "why": NOT_FOUND, "top": top}
    held = _matcher(terms)
    cands, raw, seen = [], {}, {}  # (path, line no, text, low text, low heading+title, ...); each file's lines
    for f in files:
        text = read(f["path"])
        if not text:
            continue
        raw[f["path"]] = text.splitlines()
        title = next((tidy(ln) for ln in raw[f["path"]] if ln.strip()), "")
        name = re.sub(r"[-_.]", " ", f["path"].rsplit("/", 1)[-1])
        for n, t, head in lines_of(text):
            low, ctx = t.lower(), f"{head} {title} {name}".lower()
            own = held(low)  # the terms the line holds, then those only its heading holds
            there = seen[ctx] if ctx in seen else seen.setdefault(ctx, held(ctx))  # shared by a section's lines
            cands.append((f["path"], n, t, low, ctx, own, there - own))
    if not cands:
        return {"found": False, "why": NOT_FOUND, "top": top}
    df = {t: sum(1 for c in cands if t in c[5] or t in c[6]) for t in terms}
    if sum(1 for t in terms if df[t]) < MIN_PRESENT * len(terms):
        return {"found": False, "why": NOT_FOUND, "top": top}
    weight = {t: math.log(1 + len(cands) / (1 + df[t])) for t in terms if df[t]}
    # A word no file holds still counts against every line (the line does not say it), at the average
    # weight of the words that are held: rarity cannot be measured for a word that never appears.
    total = sum(weight.values()) * (1 + sum(1 for t in terms if not df[t]) / len(weight))
    shape = shape_for(question)
    qwords = set(re.findall(r"[a-z0-9]+", question.lower()))
    focus = focus_of(question)
    named = re.compile(rf"\b{re.escape(focus)}(?:s|es)?\b") if focus else None
    scored = []
    rank = {f["path"]: i for i, f in reversed(list(enumerate(files)))}
    for path, n, t, low, ctx, has, near in cands:
        own = sum(weight[x] for x in has)
        near = sum(weight[x] for x in near)
        cover = (own + TITLE_SHARE * near) / total
        new = [w for w in re.findall(r"[a-z0-9]+", low) if w not in qwords and len(w) > 2]
        if own == 0 or (len(new) < 2 and not (shape and shape.search(t))) or (named and not named.search(f"{low} {ctx}")):
            continue
        score = cover * (HAS_SHAPE if shape and shape.search(t) else MISS_SHAPE if shape else 1.0)
        score /= 1 + LENGTH_PULL * max(0, len(t) - QUOTE_CHARS) / QUOTE_CHARS
        score /= 1 + RANK_PULL * rank[path]  # the search's own order is evidence too
        scored.append((score, cover, path, n, t))
    if not scored:
        return {"found": False, "why": NOT_FOUND, "top": top}
    scored.sort(key=lambda s: (-s[0], files.index(next(f for f in files if f["path"] == s[2])), s[3]))
    if trace is not None:
        trace.extend(scored[:5])
    best = scored[0]
    near = raw[best[2]][max(0, best[3] - 1 - SECRET_MARGIN):best[3] + SECRET_MARGIN]
    if best[1] < MIN_COVER or (shape and not shape.search(best[4])) or secret("\n".join(near)):
        return {"found": False, "why": NOT_FOUND, "top": top}
    if len(scored) > 1:
        second = scored[1]
        # the runner-up agrees when it is the same text (a copy of the note) or states the same value
        same = best[4] == second[4] or bool(_values(best[4], shape, question) & _values(second[4], shape, question))
        if not same and best[0] - second[0] < MARGIN * best[0]:
            return {"found": False, "why": NOT_FOUND, "top": top}
    return {"found": True, "text": _window(best[4], terms, shape), "path": best[2], "line": best[3]}


def render(q: dict) -> str:
    """The fixed templates: the quote with its source, or the honest not-found."""
    if q.get("found"):
        return f'Answer: "{q["text"]}"  -- {q["path"]} line {q["line"]}'
    return f'{q["why"]} Top file: {q["top"]}' if q.get("top") else q["why"]
