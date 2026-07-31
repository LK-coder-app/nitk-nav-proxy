import json
import os
import chromadb

from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

with open("knowledge.json", encoding="utf-8") as f:
    pages = json.load(f)

client = chromadb.PersistentClient(path="nitk_chroma")

embedding_function = SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)

collection = client.get_or_create_collection(
    name="nitk",
    embedding_function=embedding_function
)

def split_into_chunks(text,
                      chunk_size=1200,
                      overlap=250):

    chunks=[]

    start=0

    while start<len(text):

        end=start+chunk_size

        chunks.append(text[start:end])

        start+=chunk_size-overlap

    return chunks

count=0

ids = []
documents = []
metadatas = []

for page in pages:

    if len(page["text"]) > 200000:
        print("Skipping huge page:", page["url"])
        continue

    chunks = split_into_chunks(page["text"])

    for chunk in chunks:

        ids.append(str(count))
        documents.append(chunk)
        metadatas.append({
            "title": page["title"],
            "url": page["url"]
        })

        count += 1

        if len(ids) == 100:

            collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas
            )

            print("Inserted", count)

            ids = []
            documents = []
            metadatas = []

# Insert remaining documents
if ids:

    collection.add(
        ids=ids,
        documents=documents,
        metadatas=metadatas
    )

print("Finished:", count)