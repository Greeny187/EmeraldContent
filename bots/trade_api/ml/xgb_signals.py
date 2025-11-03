import numpy as np

try:
    import xgboost as xgb  # optional
except Exception:
    xgb = None

def compute_features(ohlcv: np.ndarray) -> np.ndarray:
    # ohlcv: shape (N, 5) -> O H L C V
    # simple toy features: returns, volatility, volume zscore
    close = ohlcv[:,3]
    ret = np.diff(close, prepend=close[0]) / np.maximum(close, 1e-9)
    vol = np.std(ret[-30:]) if len(ret) >= 30 else np.std(ret)
    v = ohlcv[:,4]
    vz = (v - v.mean()) / (v.std() + 1e-9)
    feats = np.stack([ret, np.full_like(ret, vol), vz], axis=1)
    return feats.astype("float32")

def score_signal(ohlcv: np.ndarray) -> dict:
    feats = compute_features(ohlcv)
    # Fallback model: simple rule (no xgboost on Heroku build)
    score = float(np.tanh(feats[-1].sum()))
    signal = "buy" if score > 0.15 else ("sell" if score < -0.15 else "hold")
    return {"score": score, "signal": signal}
