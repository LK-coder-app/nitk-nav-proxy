import sqlite3

conn = sqlite3.connect("nitk_chroma/chroma.sqlite3")
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM embeddings;")
print("Embeddings:", cur.fetchone()[0])

cur.execute("SELECT COUNT(*) FROM embedding_metadata;")
print("Embedding metadata:", cur.fetchone()[0])

conn.close()