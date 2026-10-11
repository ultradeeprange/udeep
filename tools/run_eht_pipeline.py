"""Reconstruct the M87* image of 11 April 2017 with the Event Horizon Telescope's own released pipeline.

    data      EHT Collaboration, "First M87 EHT Results: Calibrated Data" (2019-D01-01), ODC-PDDL
    pipeline  EHT Collaboration, "First M87 EHT Results: Imaging Pipelines" (2019-D01-02), eht-imaging, GPLv3

The pipeline script is downloaded and run unmodified except for ONE setting: ttype 'nfft' -> 'direct' (an exact
Fourier transform; the NFFT library is hard to install on Windows). It is not copied into this repository.

Needs its own environment:  pip install ehtim     Output: data/eht/m87_2017_101.fits (Jy/pixel, 64 x 64, 2 uas)
On Windows, ehtim's dependency `paramsurvey` imports the Unix-only module `resource`; a three-line stand-in
module in that environment is enough (the imaging pipeline does not use paramsurvey).
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import urllib.request
from pathlib import Path

EHT = Path(__file__).resolve().parents[1] / "data" / "eht"
RAW = "https://github.com/eventhorizontelescope"
FILES = {
    "SR1_M87_2017_101_lo_hops_netcal_StokesI.uvfits": (f"{RAW}/2019-D01-01/raw/master/uvfits/SR1_M87_2017_101_lo_hops_netcal_StokesI.uvfits", "697af2bb3bbf7321"),
    "SR1_M87_2017_101_hi_hops_netcal_StokesI.uvfits": (f"{RAW}/2019-D01-01/raw/master/uvfits/SR1_M87_2017_101_hi_hops_netcal_StokesI.uvfits", "618c3019f60e8826"),
    "eht-imaging_pipeline.py": (f"{RAW}/2019-D01-02/raw/master/eht-imaging/eht-imaging_pipeline.py", "5b5ec6a3e33f786c"),
}

if __name__ == "__main__":
    EHT.mkdir(parents=True, exist_ok=True)
    for name, (url, sha) in FILES.items():
        p = EHT / name
        if not p.exists():
            p.write_bytes(urllib.request.urlopen(url, timeout=120).read())
        got = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        if got != sha:
            raise SystemExit(f"{name}: sha256 {got} does not match the recorded {sha}")
    src = (EHT / "eht-imaging_pipeline.py").read_text(encoding="utf-8")
    setting = "ttype     = 'nfft' "
    if src.count(setting) != 1:
        raise SystemExit("the pipeline's ttype setting was not found exactly once")
    (EHT / "pipeline_direct.py").write_text(src.replace(setting, "ttype     = 'direct'"), encoding="utf-8")
    subprocess.run([sys.executable, "pipeline_direct.py", "-i", "SR1_M87_2017_101_lo_hops_netcal_StokesI.uvfits",
                    "-i2", "SR1_M87_2017_101_hi_hops_netcal_StokesI.uvfits", "-o", "m87_2017_101.fits"],
                   cwd=EHT, check=True, env={**__import__("os").environ, "MPLBACKEND": "Agg"})
    print("wrote", EHT / "m87_2017_101.fits")
