# Device evidence

Made by `tools/evidence.py` from the records in [`devices/`](devices/). Do not edit it by hand.

Each record is what one person saw on one screen with the [device checks](https://ultradeeprange.github.io/udeep/check/), under the conditions the profile asks for (Annex A).
A page counts only if its controls behaved: the SDR control stayed plain and the positive control
showed HDR. "Not recorded" means the report did not say; nothing is filled in afterwards.

Records: 2. To add yours, [open a device report](https://github.com/ultradeeprange/udeep/issues/new?template=device-report.yml).

## iPhone 16 Pro

- **Record:** [`2026-09-27-iphone-16-pro`](devices/2026-09-27-iphone-16-pro.json), from profile, Annex B (the editor's observation), 2026-09-27
- **System:** not recorded; Safari, version not recorded
- **Conditions:** brightness not recorded; automatic brightness not recorded; room light not recorded

| Check | Result |
|---|---|
| formats | hdr: F1 F2 F3 F4 F5 F6 |
| SVG | hdr: S2 S3 S5 S6; sdr: S4 (controls not recorded) |
| vector fills | same white: V1; black: V2 V3 |
| 12-bit video | not run |
| precision | not run |

Observed by eye on 2026-09-26 and 27, before Annex A.4 was in force, so the OS and browser versions and the viewing conditions were not written down. Annex B does not say what the two controls of the SVG page showed. An earlier precision run is described in Annex B (B6); it had no 8- and 9-bit controls and is not recorded here.

## Device not recorded (Windows 11; Edge 153)

- **Record:** [`undated-desktop-edge-153`](devices/undated-desktop-edge-153.json), from profile, Annex B (the editor's observation), not recorded
- **System:** Windows 11; Edge 153
- **Conditions:** brightness not recorded; automatic brightness not recorded; room light not recorded

| Check | Result |
|---|---|
| formats | not run |
| SVG | not run |
| vector fills | same white: V1; black: V2 V3 |
| 12-bit video | not run |
| precision | not run |

A desktop HDR monitor; Annex B (B5) records the browser and system only.
