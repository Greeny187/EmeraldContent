import numpy as np

def optimize(weights_hint: dict[str,float], sentiment: dict[str,float]|None=None) -> dict:
    # very lightweight: normalize hints and nudge by sentiment (positive -> tilt risk-on asset if present)
    if not weights_hint:
        return {}
    keys = list(weights_hint.keys())
    w = np.array([max(0.0, float(weights_hint[k])) for k in keys], dtype=float)
    if w.sum() == 0:
        w = np.ones_like(w)
    w = w / w.sum()
    if sentiment:
        pos = sentiment.get("positive", 0.33) - sentiment.get("negative", 0.33)
        w = w * (1.0 + 0.2*pos)  # gentle tilt
        w = w / w.sum()
    return {k: float(v) for k, v in zip(keys, w)}
