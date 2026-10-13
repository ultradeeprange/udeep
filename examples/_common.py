"""What every example does with its figure: save it for a light and for a dark page, as the ordinary figure and as
the UDeep file, check the file against the figure, and print the caption the file's own declaration gives."""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from udeep import mpl as hp

OUTPUT = Path(__file__).resolve().parent / "output"


def save(build, name: str, folder: Path = OUTPUT) -> dict:
    """`build()` makes the figure. Returns the light figure's validation report, its caption and its two files."""
    folder.mkdir(parents=True, exist_ok=True)
    result = {}
    for suffix, settings in (("", {}), ("_dark", hp.theme("dark"))):
        with plt.rc_context(settings):
            fig = build()
            sdr, hdr = folder / f"{name}{suffix}_sdr.png", folder / f"{name}{suffix}_hdr.jpg"
            fig.savefig(sdr, bbox_inches="tight", pad_inches=0.1)        # the ordinary figure: the figure of record
            hp.savefig_hdr(fig, hdr)                                      # the same figure, a gain map, the declaration
            if not suffix:
                result = {"report": hp.validate(fig, hdr, sdr=sdr), "caption": hp.caption(fig), "hdr": hdr, "sdr": sdr}
            plt.close(fig)
    print(result["caption"])
    print(result["report"])
    return result
