"""A simulated field that spans several decades. Extend on a logarithmic stretch, with a statistical knee.

SYNTHETIC data: a log-normal field made by smoothing random numbers. It stands in for a quantity where most of the
volume is quiet and a small part is intense; it is not the output of a solver.

The stretch is logarithmic, because the field's structure is in its ratios. The knee is statistical: ten times the
field's mean. Below it the figure is the ordinary logarithmic plot; the intense events, which an ordinary figure
crowds into its last few shades, go above white. No noise is stated for a simulation, so the noise model is
declared unknown, and the file says that it is a rendering.
"""
import matplotlib.pyplot as plt
import numpy as np
from _common import save

from udeep import mpl as hp


def field(size: int = 384) -> np.ndarray:
    rng = np.random.default_rng(20261015)
    k = np.fft.fftfreq(size)[:, None] ** 2 + np.fft.fftfreq(size)[None, :] ** 2
    spectrum = np.where(k > 0, 1.0 / np.maximum(k, 1e-12) ** 1.7, 0.0)    # far more power at large scales
    smooth = np.fft.ifft2(np.fft.fft2(rng.normal(size=(size, size))) * np.sqrt(spectrum)).real
    smooth = (smooth - smooth.mean()) / smooth.std()
    return np.exp(2.0 * smooth)                                           # log-normal: a long tail of intense values


def build():
    data = field()
    knee = 10.0 * float(data.mean())
    low, high = float(data.min()), float(data.max())
    fig, ax = plt.subplots(figsize=(5.8, 5.0), dpi=150)
    img = hp.imshow(ax, data, mode="extend", knee=knee, stretch={"kind": "log", "lo": low, "hi": high},
                    cmap="ice", headroom=4.0, unit="1", knee_rule="statistical; ten times the mean of the field")
    hp.colorbar(img, ax=ax, label="value (arbitrary unit)")
    share = 100.0 * float((data > knee).mean())
    ax.set(title=f"Synthetic log-normal field: {share:.1f} % of it above ten times the mean",
           xlabel="x (cell)", ylabel="y (cell)")
    return fig


if __name__ == "__main__":
    save(build, "simulation_field")
