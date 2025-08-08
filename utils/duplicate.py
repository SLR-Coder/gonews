import openai
import numpy as np
import os

openai.api_key = os.environ.get("OPENAI_API_KEY", "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") # Veya doğrudan keyini buraya yaz

def get_embedding(text, model="text-embedding-ada-002"):
    response = openai.embeddings.create(input=[text], model=model)
    return np.array(response.data[0].embedding)

def is_duplicate(new_title, existing_titles, threshold=0.85):
    new_emb = get_embedding(new_title)
    existing_embs = [get_embedding(t) for t in existing_titles]
    similarities = [np.dot(new_emb, e) / (np.linalg.norm(new_emb) * np.linalg.norm(e)) for e in existing_embs]
    return max(similarities) > threshold if similarities else False
