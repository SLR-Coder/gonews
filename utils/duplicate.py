import openai
import numpy as np
import os

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY ortam değişkeni bulunamadı!")
openai.api_key = OPENAI_API_KEY

def get_embedding(text, model="text-embedding-ada-002"):
    response = openai.embeddings.create(input=[text], model=model)
    return np.array(response.data[0].embedding)

def is_duplicate(new_title, existing_titles, threshold=0.85):
    new_emb = get_embedding(new_title)
    existing_embs = [get_embedding(t) for t in existing_titles]
    similarities = [np.dot(new_emb, e) / (np.linalg.norm(new_emb) * np.linalg.norm(e)) for e in existing_embs]
    return max(similarities) > threshold if similarities else False
