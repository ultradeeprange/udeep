"""The device evidence: one record per report in evidence/devices/, and the table made from them.

    python tools/evidence.py                 check every record and rewrite evidence/README.md
    python tools/evidence.py --check         the same, but fail if evidence/README.md is not current
    python tools/evidence.py --import 12     turn issue 12 (a device report) into a record; needs the `gh` tool

A record says what one person saw on one screen, under the conditions the profile's Annex A asks for (A.4). A value
that was not recorded is null: it is never filled in afterwards. Whether a page's result counts is worked out here
from its controls (A.2), not written by hand.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEVICES = ROOT / "evidence" / "devices"
TABLE = ROOT / "evidence" / "README.md"
FORM = ROOT / ".github" / "ISSUE_TEMPLATE" / "device-report.yml"
REPO = "ultradeeprange/udeep"

CONDITIONS = ("device", "os", "browser", "brightness", "auto_brightness", "ambient")
# page -> (its items, the answers an item can have)
PAGES = {
    "pattern": (("F0", "F1", "F2", "F3", "F4", "F5", "F6"), ("hdr", "sdr", "not shown")),
    "svg": (("S0", "S1", "S2", "S3", "S4", "S5", "S6"), ("hdr", "sdr", "not shown")),
    "vector": (("V1", "V2", "V3"), ("black", "same white", "brighter")),
    "video": (("V1", "V2", "V3", "V4", "V5"), ("hdr", "sdr", "does not play")),
    "precision": (tuple(a + b for a in "DWB" for b in "ABCDEF"), ("bands", "faint", "smooth")),
}
# page -> the answers its controls must have for the page to count (Annex A.2). `vector` is read with `svg`: it
# is part of that page. `precision` is blind, and is scored against the unpublished key, not here.
CONTROLS = {
    "pattern": {"F0": "sdr"},
    "svg": {"S0": "sdr", "S1": "hdr"},
    "vector": {},
    "video": {"V1": "hdr", "V4": "hdr"},
    "precision": {},
}
NAMES = {"pattern": "formats", "svg": "SVG", "vector": "vector fills", "video": "12-bit video", "precision": "precision"}
KEYS = ("id", "date", "source", *CONDITIONS, "direct", "results", "notes")


def check(record: dict, name: str = "") -> list[str]:
    """What is wrong with a record, as a list of sentences; empty if nothing is."""
    wrong = []
    if tuple(record) != KEYS:
        return [f"{name}: the keys must be {', '.join(KEYS)}, in that order"]
    if name and record["id"] != name:
        wrong.append(f"{name}: id is {record['id']!r}, and must be the file's name")
    if record["date"] is not None and not re.fullmatch(r"\d{4}-\d\d-\d\d", record["date"]):
        wrong.append(f"{name}: date must be YYYY-MM-DD or null")
    if not isinstance(record["source"], str) or not record["source"]:
        wrong.append(f"{name}: source must say where the report came from")
    for key in CONDITIONS:
        if record[key] is not None and not (isinstance(record[key], str) and record[key].strip()):
            wrong.append(f"{name}: {key} must be text, or null if it was not recorded")
    if record["auto_brightness"] not in (None, "on", "off"):
        wrong.append(f"{name}: auto_brightness must be on, off or null")
    if record["direct"] not in (True, None):
        wrong.append(f"{name}: a report seen through remote desktop is not evidence (Annex A.4)")
    results = record["results"]
    if not isinstance(results, dict) or not results:
        return [*wrong, f"{name}: results must hold at least one page"]
    for page, answers in results.items():
        if page not in PAGES:
            wrong.append(f"{name}: there is no page {page!r}")
            continue
        items, allowed = PAGES[page]
        if not answers:
            wrong.append(f"{name}: {page} is empty: leave the page out")
        for item, answer in answers.items():
            if item not in items:
                wrong.append(f"{name}: {page} has no item {item!r}")
            elif answer not in allowed:
                wrong.append(f"{name}: {page} {item} is {answer!r}, and must be one of {', '.join(allowed)}")
    return wrong


def standing(record: dict, page: str) -> str:
    """Whether a page's result counts: 'valid', 'void' (a control failed) or 'controls not recorded'."""
    answers, controls = record["results"][page], CONTROLS[page]
    if page == "vector":
        return "valid"                       # each square is its own answer: black, the same white, or brighter
    if page == "precision":
        return "blind: scored against the key"
    if any(item not in answers for item in controls):
        return "controls not recorded"
    return "valid" if all(answers[item] == want for item, want in controls.items()) else "void"


def load() -> list[dict]:
    records, wrong = [], []
    for path in sorted(DEVICES.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        wrong += check(record, path.stem)
        records.append(record)
    if wrong:
        raise SystemExit("\n".join(wrong))
    return records


def shown(value) -> str:
    return "not recorded" if value is None else str(value).replace("|", "\\|")


def summary(record: dict, page: str) -> str:
    answers = record["results"].get(page)
    if answers is None:
        return "not run"
    state = standing(record, page)
    if state == "void":
        return "void (a control failed)"
    by_answer: dict[str, list[str]] = {}
    for item in PAGES[page][0]:
        if item in answers and item not in CONTROLS[page]:
            by_answer.setdefault(answers[item], []).append(item)
    text = "; ".join(f"{answer}: {' '.join(items)}" for answer, items in by_answer.items())
    return text if state == "valid" else f"{text} ({state})"


def table(records: list[dict]) -> str:
    lines = ["# Device evidence", "",
             "Made by `tools/evidence.py` from the records in [`devices/`](devices/). Do not edit it by hand.", "",
             ("Each record is what one person saw on one screen with the [device checks]"
             "(https://ultradeeprange.github.io/udeep/check/), under the conditions the profile asks for (Annex A)."),
             "A page counts only if its controls behaved: the SDR control stayed plain and the positive control",
             "showed HDR. \"Not recorded\" means the report did not say; nothing is filled in afterwards.", "",
             (f"Records: {len(records)}. To add yours, [open a device report]"
             f"(https://github.com/{REPO}/issues/new?template=device-report.yml)."), ""]
    for record in records:
        source = record["source"]
        link = f"[{source.rsplit('/', 1)[-1]}]({source})" if source.startswith("https://") else source
        title = record["device"] or f"Device not recorded ({shown(record['os'])}; {shown(record['browser'])})"
        lines += [f"## {title}", "",
                  f"- **Record:** [`{record['id']}`](devices/{record['id']}.json), from {link}, {shown(record['date'])}",
                  f"- **System:** {shown(record['os'])}; {shown(record['browser'])}",
                  (f"- **Conditions:** brightness {shown(record['brightness'])}; automatic brightness "
                  f"{shown(record['auto_brightness'])}; room light {shown(record['ambient'])}"), "",
                  "| Check | Result |", "|---|---|"]
        lines += [f"| {NAMES[page]} | {summary(record, page)} |" for page in PAGES]
        if record["notes"]:
            lines += ["", record["notes"]]
        lines.append("")
    return "\n".join(lines)


# ---- a report from an issue

def form_labels() -> dict[str, str]:
    """label -> id, as the issue form has them."""
    text = FORM.read_text(encoding="utf-8")
    found = re.findall(r"- type: \w+\n\s+id: (\w+)\n\s+attributes:\n\s+label: (.+)", text)
    return {label.strip(): ident for ident, label in found}


def sections(body: str) -> dict[str, str]:
    """An issue form's body is '### label' then the answer. -> id: answer, without the form's own wrapping."""
    labels, out = form_labels(), {}
    for part in re.split(r"^### ", body.replace("\r\n", "\n"), flags=re.MULTILINE)[1:]:
        label, _, answer = part.partition("\n")
        answer = re.sub(r"^```\w*\n|\n```$", "", answer.strip()).strip()
        if label.strip() in labels:
            out[labels[label.strip()]] = "" if answer == "_No response_" else answer
    return out


def answers(page: str, text: str) -> tuple[dict[str, str], list[str]]:
    """The lines 'F1: hdr' of one page -> (item: answer, the lines that could not be read)."""
    items, allowed = PAGES[page]
    out, unread = {}, []
    for line in filter(None, (ln.strip() for ln in text.splitlines())):
        m = re.fullmatch(r"(\w+)\s*[:=]\s*(.*)", line)
        if not m or m.group(1).upper() not in items:
            unread.append(line)
        elif m.group(2).strip():
            answer = re.sub(r"\s+", " ", m.group(2).strip().lower())
            if answer in allowed:
                out[m.group(1).upper()] = answer
            else:
                unread.append(line)
    return out, unread


def record_of(body: str, url: str, date: str, ident: str) -> tuple[dict, list[str]]:
    got, unread = sections(body), []
    results = {}
    for page in PAGES:
        found, missed = answers(page, got.get(page, ""))
        unread += [f"{page}: {line}" for line in missed]
        if found:
            results[page] = found
    auto = got.get("auto_brightness", "")
    record = {"id": ident, "date": date, "source": url,
              **{key: (got.get(key, "").strip() or None) for key in CONDITIONS},
              "direct": True if re.search(r"- \[[xX]\]", got.get("direct", "")) else None,
              "results": results, "notes": got.get("notes", "")}
    record["auto_brightness"] = auto if auto in ("on", "off") else None
    return record, unread


def import_issue(number: int) -> None:
    out = subprocess.run(["gh", "issue", "view", str(number), "-R", REPO, "--json", "body,url,createdAt"],
                         check=True, capture_output=True, text=True, encoding="utf-8").stdout
    issue = json.loads(out)
    date = issue["createdAt"][:10]
    device = re.sub(r"[^a-z0-9]+", "-", (sections(issue["body"]).get("device") or "device").lower()).strip("-")
    ident = f"{date}-{device[:40]}-{number}"
    record, unread = record_of(issue["body"], issue["url"], date, ident)
    wrong = check(record, ident)
    path = DEVICES / f"{ident}.json"
    path.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", path.relative_to(ROOT))
    for line in unread:
        print("  could not read:", line)
    for line in wrong:
        print("  to put right by hand:", line)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if evidence/README.md is not current")
    parser.add_argument("--import", dest="issue", type=int, help="the number of a device-report issue")
    args = parser.parse_args()
    if args.issue is not None:
        import_issue(args.issue)
    text = table(load())
    if args.check:
        if not TABLE.exists() or TABLE.read_text(encoding="utf-8") != text:
            sys.exit("evidence/README.md is not current: run tools/evidence.py")
        print("evidence/README.md is current")
    else:
        TABLE.write_text(text, encoding="utf-8", newline="\n")
        print("wrote", TABLE.relative_to(ROOT))


if __name__ == "__main__":
    main()
