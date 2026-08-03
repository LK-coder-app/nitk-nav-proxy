import chromadb

client = chromadb.PersistentClient(path="nitk_chroma")

print("Collections:", client.list_collections())

collection = client.get_collection("nitk")

print("Count:", collection.count())

print("Peek:", collection.peek())