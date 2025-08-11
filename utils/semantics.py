# utils/semantics.py
from utils.gemini import get_embedding
import numpy as np

def cosine_sim(a, b):
    if not a.any() or not b.any():
        return 0.0
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

def is_duplicate(title, category, all_by_cat, current_idx, threshold=0.92):
    """Aynı kategori içinde benzer başlık varsa True döner."""
    try:
        cur_emb = get_embedding(title)
        for idx, other_title in all_by_cat.get(category, []):
            if idx == current_idx:
                continue
            score = cosine_sim(cur_emb, get_embedding(other_title))
            if score >= threshold:
                return True
    except Exception:
        return False
    return False
