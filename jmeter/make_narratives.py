"""Write jmeter/narratives.txt: one team-row narrative per line, for JMeter's CSV Data Set Config.

Reads ict3113_tickets.csv (team rows 3000-3999) without modifying it. Whitespace runs, including
newlines and tabs, collapse to single spaces so each narrative fits on one line.
"""
import csv
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "ict3113_tickets.csv"
OUT = HERE / "narratives.txt"

with open(SOURCE, encoding="utf-8", newline="") as f:
    narratives = [re.sub(r"\s+", " ", row["narrative"]).strip() for row in csv.DictReader(f)]
narratives = [n for n in narratives if n]

OUT.write_text("\n".join(narratives) + "\n", encoding="utf-8")
print(f"{len(narratives)} narratives -> {OUT}")
