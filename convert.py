#!/usr/bin/env python3
"""Hämtar ett iCal-schema, döper om händelser enligt rules.json och delar upp
schemat i en .ics-fil per kurs (så att varje kurs kan ha egen färg i Google Kalender).

Användning:
    SCHEDULE_URL="https://.../schema.ics" python convert.py

Inga externa beroenden.
"""
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
RULES_FILE = ROOT / "rules.json"
OUT_DIR = ROOT / "docs"

ALLOWED_FIELDS = {"SUMMARY", "LOCATION", "DESCRIPTION"}
OVERIGT_FILE = "ovrigt.ics"
OVERIGT_NAME = "Övrigt"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "schedule-converter/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def unfold(text: str) -> list[str]:
    """iCal viker långa rader (CRLF + blanksteg). Vik ut dem igen."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in text.split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return [l for l in lines if l != ""]


def fold(line: str, limit: int = 75) -> str:
    """Vik rader till max 75 byte (UTF-8-säkert)."""
    if len(line.encode("utf-8")) <= limit:
        return line
    parts: list[str] = []
    cur = b""
    max_len = limit
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > max_len:
            parts.append(cur.decode("utf-8"))
            cur = b
            max_len = limit - 1  # fortsättningsrader börjar med ett blanksteg
        else:
            cur += b
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def load_config() -> tuple[list[dict], list[dict]]:
    data = json.loads(RULES_FILE.read_text(encoding="utf-8"))

    rules = []
    for r in data.get("rules", []):
        field = r.get("field", "SUMMARY").upper()
        if field not in ALLOWED_FIELDS:
            sys.exit(f"Ogiltigt fält i regel: {field}")
        rules.append(
            {
                "field": field,
                "pattern": re.compile(r["pattern"], re.IGNORECASE),
                "replace": r["replace"],
            }
        )

    calendars = []
    for c in data.get("calendars", []):
        calendars.append(
            {
                "file": c["file"],
                "name": c["name"],
                "match": re.compile(c["match"], re.IGNORECASE),
            }
        )
    return rules, calendars


def split_calendar(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    """Delar upp i (allt utom händelser, lista med händelser)."""
    other: list[str] = []
    events: list[list[str]] = []
    cur: list[str] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            cur = [line]
        elif line == "END:VEVENT" and cur is not None:
            cur.append(line)
            events.append(cur)
            cur = None
        elif cur is not None:
            cur.append(line)
        elif line == "END:VCALENDAR":
            continue
        else:
            other.append(line)
    return other, events


def event_field(ev: list[str], field: str) -> str:
    for line in ev:
        if ":" in line:
            head, value = line.split(":", 1)
            if head.split(";", 1)[0].upper() == field:
                return value
    return ""


def apply_rules(ev: list[str], rules: list[dict]) -> list[str]:
    out: list[str] = []
    for line in ev:
        if ":" in line:
            head, value = line.split(":", 1)
            name = head.split(";", 1)[0].upper()  # t.ex. SUMMARY;LANGUAGE=sv
            for rule in rules:
                if rule["field"] == name:
                    value = rule["pattern"].sub(rule["replace"], value)
            line = f"{head}:{value}"
        out.append(line)
    return out


def build(other: list[str], events: list[list[str]], name: str | None) -> list[str]:
    header = list(other)
    if name:
        header = [l for l in header if not l.upper().startswith("X-WR-CALNAME")]
        header.insert(1, "X-WR-CALNAME:" + name.replace(",", "\\,"))
    lines = header[:]
    for ev in events:
        lines.extend(ev)
    lines.append("END:VCALENDAR")
    return lines


def write(path: Path, lines: list[str]) -> None:
    path.write_text("\r\n".join(fold(l) for l in lines) + "\r\n", encoding="utf-8")


def main() -> None:
    url = os.environ.get("SCHEDULE_URL")
    if not url:
        sys.exit("Sätt miljövariabeln SCHEDULE_URL till schemats iCal-länk.")
    url = re.sub(r"^webcal://", "https://", url)

    text = fetch(url)
    if "BEGIN:VCALENDAR" not in text:
        sys.exit("Svaret ser inte ut som en iCal-fil. Avbryter utan att skriva över.")

    rules, calendars = load_config()
    other, events = split_calendar(unfold(text))
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Dela upp på ORIGINALTITELN (före namnbyte), sedan döp om
    buckets: dict[str, list[list[str]]] = {c["file"]: [] for c in calendars}
    overigt: list[list[str]] = []
    renamed_all: list[list[str]] = []

    for ev in events:
        original_summary = event_field(ev, "SUMMARY")
        renamed = apply_rules(ev, rules)
        renamed_all.append(renamed)
        for c in calendars:
            if c["match"].search(original_summary):
                buckets[c["file"]].append(renamed)
                break
        else:
            overigt.append(renamed)

    # Samlingsfil med allt
    write(OUT_DIR / "schedule.ics", build(other, renamed_all, None))

    # En fil per kurs
    for c in calendars:
        write(OUT_DIR / c["file"], build(other, buckets[c["file"]], c["name"]))
        print(f"{c['file']}: {len(buckets[c['file']])} händelser")

    # Det som inte matchade någon kurs
    overigt_path = OUT_DIR / OVERIGT_FILE
    if overigt:
        write(overigt_path, build(other, overigt, OVERIGT_NAME))
        print(f"{OVERIGT_FILE}: {len(overigt)} händelser (matchade ingen kurs)")
    elif overigt_path.exists():
        overigt_path.unlink()

    print(f"Totalt {len(events)} händelser i källschemat.")


if __name__ == "__main__":
    main()
