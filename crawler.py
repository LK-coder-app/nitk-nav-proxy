import json
import os
import time
from collections import deque
from urllib.parse import urljoin, urlparse
import re
import requests
from bs4 import BeautifulSoup
import zipfile
import chromadb
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

BASE_URL = "https://www.nitk.ac.in/"
OUTPUT_FILE = "knowledge.json"
MAX_PAGES = 2500

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}
# ---------- ChromaDB ----------
client = chromadb.PersistentClient(path="nitk_chroma")

import os

print("=" * 60)
print("DEBUG INFORMATION")
print("Current working directory:", os.getcwd())
print("Chroma absolute path:", os.path.abspath("nitk_chroma"))
print("SQLite exists:", os.path.exists("nitk_chroma/chroma.sqlite3"))
print("=" * 60)

embedding_function = ONNXMiniLM_L6_V2()

collection = client.get_or_create_collection(
    name="nitk",
    embedding_function=embedding_function
)

print("=" * 60)
print("Collection object:", collection)

print("Count:", collection.count())

print("Peek:")
print(collection.peek(limit=3))

print("=" * 60)
# ------------------------------

def clean_text(text):
    return " ".join(text.split())

def split_into_chunks(text, chunk_size=1200, overlap=250):

    chunks = []

    start = 0

    while start < len(text):

        end = start + chunk_size

        chunks.append(text[start:end])

        start += chunk_size - overlap

    return chunks


def build_search_index():
    print("Using ChromaDB search index.")
    print("Collection count:", collection.count())

    if collection.count() == 0:
        raise RuntimeError("ChromaDB is empty!")

def get_collection_count():
    return collection.count()


def extract_text(html):
    soup = BeautifulSoup(html, "html.parser")

    # Remove unwanted tags
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = soup.title.get_text(strip=True) if soup.title else ""

    # Try common content containers
    selectors = [
        ".gdlr-core-page-builder-body",
        ".gdlr-core-pbf-wrapper-content",
        ".gdlr-core-text-box-item-content",
        "main",
        "article",
        '[role="main"]',
        "#content",
        "#main-content",
        ".content",
        ".main-content",
        ".page-content"
    ]

    main = None

    for selector in selectors:
        main = soup.select_one(selector)
        if main:
            break

    if main:
        text = "\n".join(
            line.strip()
            for line in main.get_text("\n").splitlines()
            if line.strip()
        )
    else:
        text = "\n".join(
            line.strip()
            for line in soup.get_text("\n").splitlines()
            if line.strip()
        )
    
    # ==========================
    # Extract all HTML tables
    # ==========================

    tables = []

    for table in soup.find_all("table"):

        rows = []

        for tr in table.find_all("tr"):

            cols = [
                td.get_text(" ", strip=True)
                for td in tr.find_all(["td", "th"])
            ]

            if cols:
                rows.append(" | ".join(cols))

        if rows:
            tables.append("\n".join(rows))

    if tables:
        text += "\n\n" + "\n\n".join(tables)

    # ==========================
    # Continue with duplicate removal
    # ==========================

    lines = []
    seen = set()

    for line in text.splitlines():
        line = line.strip()

        if len(line) < 2:
            continue

        normalized = line.lower()

        if normalized in seen:
            continue

        seen.add(normalized)

        seen.add(line)
        lines.append(line)

    text = "\n".join(lines)
    
    return title, text


def crawl():

    visited = set()
    SEED_URLS = [
        "https://www.nitk.ac.in/",
        "https://cse.nitk.ac.in/",
        "https://ece.nitk.ac.in/",
        "https://eee.nitk.ac.in/",
        "https://civil.nitk.ac.in/",
        "https://mech.nitk.ac.in/",
        "https://chemical.nitk.ac.in/",
        "https://mining.nitk.ac.in/",
        "https://placement.nitk.ac.in/",
    ]

    queue = deque(SEED_URLS)

    pages = []

    seen_pages = set()

    while queue and len(visited) < MAX_PAGES:

        url = queue.popleft()

        if url in visited:
            continue

        visited.add(url)

        print(f"Queue size: {len(queue)}")

        print("Crawling:", url)

        try:

            r = requests.get(url, headers=HEADERS, timeout=30)

            content_type = r.headers.get("Content-Type", "").lower()

            if "text/html" not in content_type:
                continue

            if r.status_code != 200:
                continue

            title, text = extract_text(r.text)

            if len(text) > 300:

                signature = (
                    title.strip().lower(),
                    text[:1000].strip().lower()
                )

                if signature not in seen_pages:

                    seen_pages.add(signature)

                    page_parsed = urlparse(url)

                    pages.append({
                        "title": title,
                        "url": url,
                        "domain": page_parsed.netloc,
                        "path": page_parsed.path,
                        "text": text
                    })

            soup = BeautifulSoup(r.text, "html.parser")

            for a in soup.find_all("a", href=True):

                link = urljoin(url, a["href"])

                parsed = urlparse(link)

                # Allow every NITK subdomain
                if not parsed.netloc.endswith("nitk.ac.in"):
                    continue

                # Remove query parameters and fragments
                link = parsed.scheme + "://" + parsed.netloc + parsed.path

                if parsed.query:
                    link += "?" + parsed.query

                # Skip PDFs for now
                if link.lower().endswith(".pdf"):
                    continue

                # Skip unwanted pages
                SKIP_PATTERNS = [
                    "/search",
                    "/login",
                    "/feed",
                    "/user",
                    "/comment",
                    "/print",
                ]

                if any(pattern in link.lower() for pattern in SKIP_PATTERNS):
                    continue

                if link not in visited:
                    queue.append(link)

        except Exception as e:

            print(e)

        time.sleep(0.3)


    print("=" * 60)
    print("Visited:", len(visited))
    print("Saved:", len(pages))
    print("Remaining queue:", len(queue))
    print("=" * 60)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:

        json.dump(
            pages,
            f,
            indent=2,
            ensure_ascii=False
        )

    print()

    print("Saved", len(pages), "pages")

def load_knowledge():
    if not os.path.exists(OUTPUT_FILE):
        return []

    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def download_knowledge():

    if os.path.exists(OUTPUT_FILE):
        print("Knowledge file already exists.")
        return

    print("Downloading knowledge from GitHub Release...")

    url = "https://github.com/LK-coder-app/nitk-nav-proxy/releases/download/v1.0/knowledge.zip"

    print("URL:", url)
    print("Starting requests.get()...")

    try:
        r = requests.get(
            url,
            stream=True,
            timeout=60,
            allow_redirects=True
        )
        print("requests.get() completed.")
    except Exception as e:
        print("requests.get() failed:", e)
        raise

    print("HTTP Status:", r.status_code)
    print("Content-Length:", r.headers.get("Content-Length"))

    r.raise_for_status()

    total = 0

    print("Starting file write...")

    with open("knowledge.zip", "wb") as f:


        for chunk in r.iter_content(1024 * 1024):

            if chunk:

                f.write(chunk)

                total += len(chunk)

                print(f"Downloaded {total//1024//1024} MB")

    print("File downloaded.")

    print("Extracting...")

    with zipfile.ZipFile("knowledge.zip") as z:

        z.extractall(".")

    os.remove("knowledge.zip")

    print("Checking extracted knowledge.json...")

    print("Exists:", os.path.exists("knowledge.json"))

    if os.path.exists("knowledge.json"):
        print("Size:", os.path.getsize("knowledge.json"))

    print("Knowledge download complete.")



def download_chroma():

    if get_collection_count() > 0:
        print("ChromaDB already populated.")
        return

    # Remove empty folder if it exists
    if os.path.exists("nitk_chroma"):
        import shutil
        shutil.rmtree("nitk_chroma")

    print("Downloading ChromaDB from GitHub Release...")

    url = "https://github.com/LK-coder-app/nitk-nav-proxy/releases/download/v1.0/nitk_chroma.zip"

    r = requests.get(
        url,
        stream=True,
        timeout=600,
        allow_redirects=True
    )

    print("HTTP Status:", r.status_code)

    r.raise_for_status()

    with open("nitk_chroma.zip", "wb") as f:
        for chunk in r.iter_content(1024 * 1024):
            if chunk:
                f.write(chunk)

    print("Extracting ChromaDB...")

    with zipfile.ZipFile("nitk_chroma.zip") as z:
        z.extractall(".")

    print("Listing extracted files...")

    for root, dirs, files in os.walk("nitk_chroma"):
        print(root)
        for file in files:
            print("   ", file)

    os.remove("nitk_chroma.zip")

    print("Checking extracted ChromaDB...")

    print("SQLite exists:", os.path.exists("nitk_chroma/chroma.sqlite3"))

    if os.path.exists("nitk_chroma/chroma.sqlite3"):
        print("SQLite size:", os.path.getsize("nitk_chroma/chroma.sqlite3"))

    print("Collection count after extraction:", get_collection_count())

    print("ChromaDB download complete.")


def search_knowledge(query, top_k=10):

    print("Collection count:", collection.count())

    results = collection.query(
        query_texts=[query],
        n_results=top_k
    )

    pages = []

    documents = results["documents"][0]
    metadatas = results["metadatas"][0]

    for doc, meta in zip(documents, metadatas):

        pages.append({

            "title": meta["title"],

            "url": meta["url"],

            "text": doc

        })

    print("=" * 50)
    print("Query:", query)
    print("Results:", len(pages))

    for page in pages:
        print(page["title"])

    return pages

def build_context(query):
    

    pages = search_knowledge(query, top_k=20)

    if not pages:
        return ""

    context = ""

    for page in pages:

        text = page["text"][:3000]

        context += f"""
Title: {page['title']}

URL: {page['url']}

{text}

--------------------------------------------------------

"""

    print("=" * 50)
    print("Context length:", len(context))
    print(context[:1000])
    
    return context

def refresh_knowledge():

    print("Refreshing knowledge...")

    crawl()

    build_search_index()

    print("Knowledge updated.")

if __name__ == "__main__":

    refresh_knowledge()

    pages = search_knowledge("director")

    print(len(pages))

    for p in pages:
        print(p["title"])
        