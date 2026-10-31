#!/usr/bin/env python3
"""Hämtar ett iCal-schema, döper om händelser enligt rules.json och skriver docs/schedule.ics.

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
OUT_FILE = ROOT / "docs" / "schedule.ics"

# Fält som reglerna får verka på
ALLOWED_FIELDS = {"SUMMARY", "LOCATION", "DESCRIPTION"}


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
    raw = line.encode("utf-8")
    if len(raw) <= limit:
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


def load_rules() -> list[dict]:
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
    return rules


def apply_rules(lines: list[str], rules: list[dict]) -> list[str]:
    out: list[str] = []
    in_event = False
    for line in lines:
        if line == "BEGIN:VEVENT":
            in_event = True
        elif line == "END:VEVENT":
            in_event = False

        if in_event and ":" in line:
            head, value = line.split(":", 1)
            name = head.split(";", 1)[0].upper()  # t.ex. SUMMARY;LANGUAGE=sv
            for rule in rules:
                if rule["field"] == name:
                    value = rule["pattern"].sub(rule["replace"], value)
            line = f"{head}:{value}"
        out.append(line)
    return out


def main() -> None:
    url = os.environ.get("SCHEDULE_URL")
    if not url:
        sys.exit("Sätt miljövariabeln SCHEDULE_URL till schemats iCal-länk.")
    # webcal:// är bara https:// med annat namn
    url = re.sub(r"^webcal://", "https://", url)

    text = fetch(url)
    if "BEGIN:VCALENDAR" not in text:
        sys.exit("Svaret ser inte ut som en iCal-fil. Avbryter utan att skriva över.")

    lines = apply_rules(unfold(text), load_rules())
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text("\r\n".join(fold(l) for l in lines) + "\r\n", encoding="utf-8")
    print(f"Skrev {OUT_FILE} ({sum(1 for l in lines if l == 'BEGIN:VEVENT')} händelser)")


if __name__ == "__main__":
    main()
