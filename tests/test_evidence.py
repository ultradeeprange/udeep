"""The device evidence (evidence/devices/): every record is well formed, the table is the one the records give,
and a report made with the issue form can be read back into a record."""
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evidence", ROOT / "tools" / "evidence.py")
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def test_every_record_is_well_formed_and_the_table_is_current():
    records = evidence.load()                                  # exits with the faults, if there are any
    assert len(records) >= 2 and len({r["id"] for r in records}) == len(records)
    assert evidence.TABLE.read_text(encoding="utf-8") == evidence.table(records), "run tools/evidence.py"


def test_the_form_asks_for_what_a_record_holds_and_for_every_item_of_every_page():
    labels = evidence.form_labels()
    assert set(labels.values()) == {*evidence.CONDITIONS, "direct", *evidence.PAGES, "notes"}
    form = evidence.FORM.read_text(encoding="utf-8")
    for page, (items, _allowed) in evidence.PAGES.items():
        block = re.search(rf"id: {page}\n.*?value: \|\n((?:\s+\w+:\n)+)", form, re.DOTALL).group(1)
        assert tuple(re.findall(r"(\w+):", block)) == items, page


def test_the_items_are_the_ones_the_check_pages_show():
    def shown(page):
        text = (ROOT / "docs" / "check" / page / "index.html").read_text(encoding="utf-8")
        return tuple(re.findall(r'<span class="id">(\w+)</span>', text))
    assert shown("pattern") == evidence.PAGES["pattern"][0]
    assert shown("svg") == evidence.PAGES["svg"][0] + evidence.PAGES["vector"][0]
    assert shown("video") == evidence.PAGES["video"][0]
    assert shown("precision") == evidence.PAGES["precision"][0]


BODY = """### Device and model

Pixel 9 Pro

### Operating system and version

Android 16

### Browser or application, and version

Chrome 141

### Brightness setting

_No response_

### Automatic brightness

off

### Room light

dark room

### Viewing path

- [X] I looked at the screen itself, not through remote desktop or screen sharing (those deliver SDR).

### Format check

```text
F0: sdr
F1: HDR
F2: hdr
F3:
F4: glows a bit
F5: not shown
F6: hdr
```

### SVG check

```text
S0:
S1:
S2:
S3:
S4:
S5:
S6:
```

### Vector fills above white

```text
V1: Same  white
V2: black
V3: brighter
```

### Video check

```text
V1: sdr
V2: hdr
V3: does not play
V4: hdr
V5: hdr
```

### Precision check

_No response_

### Anything else

_No response_
"""


def test_a_report_from_the_form_becomes_a_record():
    record, unread = evidence.record_of(BODY, "https://github.com/ultradeeprange/udeep/issues/7", "2026-10-12", "x")
    assert evidence.check(record, "x") == []
    assert (record["device"], record["os"], record["browser"]) == ("Pixel 9 Pro", "Android 16", "Chrome 141")
    assert record["brightness"] is None and record["auto_brightness"] == "off" and record["direct"] is True
    assert record["results"]["pattern"] == {"F0": "sdr", "F1": "hdr", "F2": "hdr", "F5": "not shown", "F6": "hdr"}
    assert "svg" not in record["results"] and "precision" not in record["results"]      # pages left as they were
    assert record["results"]["vector"] == {"V1": "same white", "V2": "black", "V3": "brighter"}
    assert unread == ["pattern: F4: glows a bit"]             # kept for a person to read; never guessed
    assert record["notes"] == ""


def test_a_page_counts_only_if_its_controls_behaved():
    record, _ = evidence.record_of(BODY, "https://example.org/1", "2026-10-12", "x")
    assert evidence.standing(record, "pattern") == "valid"
    assert evidence.standing(record, "video") == "void"       # V1, a control, did not show HDR
    assert evidence.summary(record, "video") == "void (a control failed)"
    assert evidence.summary(record, "svg") == "not run"
    del record["results"]["pattern"]["F0"]
    assert evidence.standing(record, "pattern") == "controls not recorded"


@pytest.mark.parametrize(("change", "fault"), [
    ({"direct": False}, "remote desktop"),
    ({"date": "12 Oct 2026"}, "YYYY-MM-DD"),
    ({"auto_brightness": "sometimes"}, "auto_brightness"),
    ({"results": {}}, "at least one page"),
    ({"results": {"pattern": {"F9": "hdr"}}}, "no item"),
    ({"results": {"pattern": {"F1": "glows"}}}, "must be one of"),
    ({"device": ""}, "null if it was not recorded"),
])
def test_a_faulty_record_is_refused(change, fault):
    record, _ = evidence.record_of(BODY, "https://example.org/1", "2026-10-12", "x")
    assert any(fault in line for line in evidence.check({**record, **change}, "x"))
