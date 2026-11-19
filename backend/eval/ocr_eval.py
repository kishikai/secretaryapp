"""OCR accuracy evaluation on synthetic Messenger-like screenshots.

Run from backend/:  PADDLE_PDX_MODEL_SOURCE=bos .venv/bin/python -m eval.ocr_eval
Renders 32 screenshots with known ground truth into a temp dir, runs the real
extract_messages pipeline with PaddleEngine, prints a summary and writes docs/ocr-eval.md.
"""
import io
import random
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.ocr import OcrLine, OcrMessage, extract_messages

WIDTH = 390
MARGIN = 12
MAX_BUBBLE = int(WIDTH * 0.7)
PAD_X, PAD_Y, RADIUS = 10, 8, 16
FONT_FILES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
BLUE = (0, 132, 255)
THEMES = {
    "light": {"page": (255, 255, 255), "left": (233, 233, 235), "left_text": (0, 0, 0)},
    "dark": {"page": (0, 0, 0), "left": (48, 48, 48), "left_text": (228, 230, 235)},
}
GREY = (138, 141, 145)
TIMESTAMP_SIZE = 12
TIMESTAMP_GAP = 12
TIMESTAMPS = ["Today 14:32", "Thu 9:05 AM", "Yesterday 21:47", "Mon 8:12 PM", "Nov 3, 10:20"]
SENTENCES = [
    "Hey, are you free tonight?",
    "Meet at 6:30pm?",
    "Bring 2 extra chairs!",
    "I'll send the address in a bit.",
    "Thanks, that works for me.",
    "Can you call Alex when you get there?",
    "Sam said the venue opens at 5.",
    "Running 10 minutes late, sorry!",
    "Did you get my message about the schedule?",
    "Let's split the bill, it's 1,250 total.",
    "No worries, take your time.",
    "The meeting moved to Room 4B.",
    "Please confirm by Friday.",
    "Got it, see you there.",
    "Don't forget to bring your ID.",
    "It's raining hard right now.",
    "Can we reschedule to next week?",
    "I just finished the report.",
    "Lunch is on me today!",
    "What time does the bus leave?",
    "Sige, see you mamaya.",
    "Nasa'n na kayo?",
    "Okay lang, mauna na ako.",
    "Pwede ba tayong mag-meet bukas?",
    "Salamat po sa help!",
    "Na-send ko na yung files, check mo na lang.",
    "Malapit na ako, 5 minutes na lang.",
    "Ang traffic grabe sa EDSA today.",
    "Kumain ka na ba?",
    "Wait lang, may tinatapos pa ako.",
    "Sino'ng sasama sa dinner?",
    "Ingat ka pauwi, text mo ko pag nandun ka na.",
    "Ayos lang, hindi naman urgent.",
    "Hintayin ko kayo sa lobby.",
    "Nag-order na ako ng 3 pizzas!",
    "Bukas na lang natin pag-usapan, pagod na ako.",
    "Si Sam ba yung nasa labas?",
    "Pasensya na, na-late ako ng gising.",
    "Tara na, 7:15 na!",
    "Alex, pakisabi kay Sam na okay na.",
]
BUCKETS = [("< 0.90", 0.0, 0.90), ("0.90 - 0.97", 0.90, 0.97), (">= 0.97", 0.97, 2.0)]


# ---------------------------------------------------------------- generator


@dataclass
class Case:
    name: str
    mode: str  # light | dark
    fmt: str  # PNG | JPEG
    data: bytes
    expected: list[tuple[str, str]]  # (text, side) per bubble
    timestamps: list[str]


def wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and font.getlength(trial) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    return lines + [current]


def make_bubble(rng: random.Random, font, blank_between: bool) -> tuple[list[str], str]:
    """Returns (rendered lines, ground-truth text). A '' line is a blank line."""
    sentences = rng.sample(SENTENCES, 6)
    lines: list[str] = []
    for p in range(rng.randint(1, 3)):
        if p and blank_between:
            lines.append("")
        take = rng.randint(1, 2)
        paragraph = " ".join(sentences[:take])
        del sentences[:take]
        lines += wrap(paragraph, font, MAX_BUBBLE - 2 * PAD_X)
    # The pipeline cannot tell a soft wrap from a hard newline, so every rendered line break is "\n".
    return lines, "\n".join(lines)


def render(rng: random.Random, name: str, mode: str, fmt: str) -> Case:
    theme = THEMES[mode]
    size = rng.randint(14, 17)
    font = ImageFont.truetype(rng.choice(FONT_FILES), size)
    stamp_font = ImageFont.truetype(FONT_FILES[0], TIMESTAMP_SIZE)
    line_h = round(size * 1.35)
    n = rng.randint(3, 6)
    stamp_at = rng.randint(1, n - 1) if rng.random() < 0.3 else None
    stamp = rng.choice(TIMESTAMPS) if stamp_at is not None else None

    # layout: items are ("bubble", side, lines, truth) or ("stamp",)
    items = []
    for i in range(n):
        if i == stamp_at:
            items.append(("stamp",))
        side = rng.choice(["left", "right"])
        lines, truth = make_bubble(rng, font, rng.random() < 0.25)
        items.append(("bubble", side, lines, truth))

    def height_of(item) -> int:
        if item[0] == "stamp":
            return 2 * TIMESTAMP_GAP + line_h
        return len(item[2]) * line_h + 2 * PAD_Y

    total = 2 * MARGIN + sum(height_of(it) for it in items)
    prev_side = None
    for it in items:
        if it[0] == "bubble":
            if prev_side is not None:
                total += 2 if it[1] == prev_side else 10
            prev_side = it[1]
        else:
            prev_side = None  # the timestamp block supplies its own spacing
    img = Image.new("RGB", (WIDTH, total), theme["page"])
    draw = ImageDraw.Draw(img)

    y = MARGIN
    prev_side = None
    expected = []
    for it in items:
        if it[0] == "stamp":
            w = stamp_font.getlength(stamp)
            ty = y + TIMESTAMP_GAP + (line_h - TIMESTAMP_SIZE) / 2
            draw.text(((WIDTH - w) / 2, ty), stamp, font=stamp_font, fill=GREY)
            y += height_of(it)
            prev_side = None
            continue
        _, side, lines, truth = it
        if prev_side is not None:
            y += 2 if side == prev_side else 10
        text_w = max(font.getlength(l) for l in lines)
        box_w = min(int(text_w + 0.999) + 2 * PAD_X, MAX_BUBBLE)
        x0 = MARGIN if side == "left" else WIDTH - MARGIN - box_w
        h = height_of(it)
        fill = theme["left"] if side == "left" else BLUE
        ink = theme["left_text"] if side == "left" else (255, 255, 255)
        draw.rounded_rectangle([x0, y, x0 + box_w - 1, y + h - 1], radius=RADIUS, fill=fill)
        for k, text in enumerate(lines):
            if text:
                ty = y + PAD_Y + k * line_h + (line_h - size) / 2
                draw.text((x0 + PAD_X, ty), text, font=font, fill=ink)
        expected.append((truth, side))
        y += h
        prev_side = side

    buf = io.BytesIO()
    if fmt == "PNG":
        img.save(buf, "PNG")
    else:
        img.save(buf, "JPEG", quality=75)
    return Case(name, mode, fmt, buf.getvalue(), expected, [stamp] if stamp else [])


def generate() -> list[Case]:
    rng = random.Random(42)
    cases = []
    for mode in ("light", "dark"):
        for i in range(16):
            fmt = "PNG" if i % 2 == 0 else "JPEG"
            cases.append(render(rng, f"{mode}-{fmt.lower()}-{i // 2 + 1}", mode, fmt))
    return cases


# ------------------------------------------------------------------ metrics


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def norm(s: str) -> str:
    return "".join(s.lower().split())


def near(a: str, b: str) -> bool:
    """Loose match for timestamps: whitespace/case-insensitive, up to 25% edits."""
    a, b = norm(a), norm(b)
    return levenshtein(a, b) <= 0.25 * max(len(b), 1)


@dataclass
class Result:
    case: Case
    messages: list[OcrMessage]
    skipped: list[OcrLine]
    seconds: float

    @property
    def counted(self) -> bool:
        return len(self.messages) == len(self.case.expected)

    @property
    def grouped(self) -> bool:
        return self.counted and all(
            m.side == side for m, (_, side) in zip(self.messages, self.case.expected)
        )

    def pairs(self):
        """(expected text, predicted message) in order; only for count-matched screenshots."""
        return [(t, m) for (t, _), m in zip(self.case.expected, self.messages)] if self.counted else []


def pct(num: float, den: float) -> str:
    return f"{100 * num / den:.1f}%" if den else "n/a"


def cer_of(results: list[Result]) -> tuple[int, int]:
    edits = sum(levenshtein(t, m.text) for r in results for t, m in r.pairs())
    chars = sum(len(t) for r in results for t, _ in r.pairs())
    return edits, chars


def compute(results: list[Result]) -> dict:
    out: dict = {"n": len(results)}
    out["grouped"] = sum(r.grouped for r in results)
    out["counted"] = sum(r.counted for r in results)
    out["edits"], out["chars"] = cer_of(results)
    pairs = [p for r in results for p in r.pairs()]
    out["msgs"] = len(pairs)
    out["exact"] = sum(t == m.text for t, m in pairs)
    out["breaks_expected"] = sum(t.count("\n\n") for t, _ in pairs)
    out["breaks_found"] = sum(min(t.count("\n\n"), m.text.count("\n\n")) for t, m in pairs)
    out["breaks_spurious"] = sum(max(m.text.count("\n\n") - t.count("\n\n"), 0) for t, m in pairs)
    stamps = [(r, s) for r in results for s in r.case.timestamps]
    out["stamps"] = len(stamps)
    out["stamps_skipped"] = sum(any(near(l.text, s) for l in r.skipped) for r, s in stamps)
    out["stamps_leaked"] = sum(
        any(near(line, s) for m in r.messages for line in m.text.split("\n")) for r, s in stamps
    )
    out["spurious_skips"] = sum(
        1 for r in results for l in r.skipped if not any(near(l.text, s) for s in r.case.timestamps)
    )
    out["seconds"] = sum(r.seconds for r in results) / len(results)
    buckets = []
    for label, lo, hi in BUCKETS:
        sel = [(t, m) for t, m in pairs if lo <= m.confidence < hi]
        edits = sum(levenshtein(t, m.text) for t, m in sel)
        chars = sum(len(t) for t, _ in sel)
        buckets.append((label, len(sel), edits, chars, sum(t == m.text for t, m in sel)))
    out["buckets"] = buckets
    return out


# ------------------------------------------------------------------- report


def show(messages: list[tuple[str, str]]) -> str:
    return "\n".join(f"[{side}] " + text.replace("\n", "\n        ") for text, side in messages)


def failures(results: list[Result], limit: int = 5) -> list[str]:
    def severity(r: Result) -> int:
        if not r.grouped:
            return 0
        return 1 if any(t != m.text for t, m in r.pairs()) else 2

    bad = sorted((r for r in results if severity(r) < 2), key=severity)[:limit]
    blocks = []
    for r in bad:
        got = show([(m.text, m.side) for m in r.messages])
        if r.skipped:
            got += "\nskipped: " + ", ".join(repr(l.text) for l in r.skipped)
        exp = show(r.case.expected)
        if r.case.timestamps:
            exp += "\nskipped: " + ", ".join(repr(t) for t in r.case.timestamps)
        kind = "grouping" if not r.grouped else "text"
        blocks.append(
            f"#### {r.case.name} ({kind} failure)\n\nExpected:\n\n```\n{exp}\n```\n\nGot:\n\n```\n{got}\n```\n"
        )
    return blocks


def build_report(results: list[Result]) -> str:
    m = compute(results)
    n_stamp_shots = sum(1 for r in results if r.case.timestamps)
    rows = [
        ("Grouping accuracy (count and sides)", f"{m['grouped']}/{m['n']}", pct(m["grouped"], m["n"])),
        ("Character error rate (count-matched screenshots)", f"{m['edits']}/{m['chars']} chars", pct(m["edits"], m["chars"])),
        ("Exact-match message texts", f"{m['exact']}/{m['msgs']}", pct(m["exact"], m["msgs"])),
        ('Line-break fidelity (expected "\\n\\n" found)', f"{m['breaks_found']}/{m['breaks_expected']}", pct(m["breaks_found"], m["breaks_expected"])),
        ('Spurious "\\n\\n" in predictions', str(m["breaks_spurious"]), ""),
        ("Timestamps in `skipped`", f"{m['stamps_skipped']}/{m['stamps']}", pct(m["stamps_skipped"], m["stamps"])),
        ("Timestamps leaked into message text", str(m["stamps_leaked"]), ""),
        ("Non-timestamp lines in `skipped`", str(m["spurious_skips"]), ""),
        ("Mean seconds per screenshot", f"{m['seconds']:.2f}", ""),
    ]
    lines = [
        "# OCR accuracy evaluation",
        "",
        "_Generated by `backend/eval/ocr_eval.py`; overwritten on every run._",
        "",
        "## Method",
        "",
        f"The script renders {m['n']} synthetic Messenger-like screenshots (390 px wide, seeded `random.Random(42)`, "
        "so the set is identical on every run): 16 light and 16 dark, and within each half 8 PNG and 8 JPEG at quality 75. "
        "Each has 3-6 rounded bubbles on random sides with English and Taglish sentences, 25% of bubbles have a blank line "
        f"between paragraphs, and {n_stamp_shots} screenshots have a centred grey timestamp. Every screenshot goes through the real "
        "`extract_messages` with one shared `PaddleEngine`. Ground truth is the text as rendered, so a soft word wrap counts as "
        'a `"\\n"` (the pipeline cannot tell it from a hard newline). **This is synthetic data: clean fonts, uniform '
        "layouts and no avatars, reactions, link previews or emoji, so these numbers are an optimistic upper bound. "
        "Real Messenger screenshots are needed before trusting any threshold below.**",
        "",
        "## Metrics",
        "",
        "| Metric | Count | Rate |",
        "|---|---|---|",
        *[f"| {a} | {b} | {c} |" for a, b, c in rows],
        "",
        "## Confidence vs error",
        "",
        "Per message (count-matched screenshots only); message confidence is the minimum over its lines.",
        "",
        "| Confidence | Messages | Exact match | CER |",
        "|---|---|---|---|",
        *[f"| {l} | {c} | {pct(x, c)} | {pct(e, ch)} |" for l, c, e, ch, x in m["buckets"]],
        "",
        "## Breakdown",
        "",
        "| Mode | Format | Screenshots | Grouping accuracy | CER |",
        "|---|---|---|---|---|",
    ]
    for mode in ("light", "dark"):
        for fmt in ("PNG", "JPEG"):
            sub = [r for r in results if r.case.mode == mode and r.case.fmt == fmt]
            s = compute(sub)
            lines.append(
                f"| {mode} | {fmt} | {s['n']} | {s['grouped']}/{s['n']} ({pct(s['grouped'], s['n'])}) "
                f"| {pct(s['edits'], s['chars'])} |"
            )
    bad = failures(results)
    lines += ["", "## Failures (up to 5)", ""]
    lines += bad if bad else ["None: every screenshot was grouped and read exactly."]
    return "\n".join(lines) + "\n", rows, m


def main() -> None:
    from app.ocr_paddle import PaddleEngine

    cases = generate()
    engine = PaddleEngine()
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for case in cases:
            path = Path(tmp) / f"{case.name}.{case.fmt.lower()}"
            path.write_bytes(case.data)  # rendered images live only in the temp dir
            start = time.perf_counter()
            messages, skipped = extract_messages(path.read_bytes(), engine)
            results.append(Result(case, messages, skipped, time.perf_counter() - start))
    report, rows, m = build_report(results)

    print("OCR evaluation (synthetic data, %d screenshots)" % m["n"])
    for a, b, c in rows:
        print(f"  {a}: {b} {c}".rstrip())
    for label, count, edits, chars, exact in m["buckets"]:
        print(f"  confidence {label}: {count} msgs, exact {pct(exact, count)}, CER {pct(edits, chars)}")
    for mode in ("light", "dark"):
        for fmt in ("PNG", "JPEG"):
            s = compute([r for r in results if r.case.mode == mode and r.case.fmt == fmt])
            print(f"  {mode} {fmt}: grouping {s['grouped']}/{s['n']}, CER {pct(s['edits'], s['chars'])}")

    out = Path(__file__).resolve().parents[2] / "docs" / "ocr-eval.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print("Report written to docs/ocr-eval.md")


if __name__ == "__main__":
    main()
