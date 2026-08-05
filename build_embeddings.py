import json
import os
import time
import numpy as np
import urllib.request

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')  
CHUNK_SIZE     = 1200
CHUNK_OVERLAP  = 250


def split_into_chunks(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks


def embed_text(text):
    url = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent'
    body = json.dumps({"content": {"parts": [{"text": text}]}}).encode()
    req = urllib.request.Request(
        url, data=body, method='POST',
        headers={'Content-Type': 'application/json', 'x-goog-api-key': GEMINI_API_KEY}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read().decode())
    return result['embedding']['values']


def main():
    if not GEMINI_API_KEY:
        print('❌ Set GEMINI_API_KEY as an environment variable first.')
        return

    with open('knowledge.json', encoding='utf-8') as f:
        pages = json.load(f)

    chunk_records = []
    vectors = []
    total = 0

    for page in pages:
        if len(page.get('text', '')) > 200000:
            print('Skipping huge page:', page.get('url'))
            continue

        for chunk in split_into_chunks(page['text']):
            try:
                vec = embed_text(chunk)
            except Exception as e:
                print(f'⚠️ Embedding failed for a chunk of {page.get("url")}: {e}')
                continue

            chunk_records.append({
                'text':  chunk,
                'title': page.get('title', ''),
                'url':   page.get('url', ''),
            })
            vectors.append(vec)
            total += 1

            if total % 25 == 0:
                print(f'Embedded {total} chunks so far...')
            time.sleep(0.05)  # gentle pacing to stay well under rate limits

    print(f'✅ Finished embedding {total} chunks.')

    with open('chunks.json', 'w', encoding='utf-8') as f:
        json.dump(chunk_records, f, ensure_ascii=False)

    np.save('embeddings.npy', np.array(vectors, dtype=np.float32))
    print('✅ Saved chunks.json and embeddings.npy')


if __name__ == '__main__':
    main()