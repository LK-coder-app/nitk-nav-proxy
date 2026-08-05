import json
import os
import time
from collections import deque
from urllib.parse import urljoin, urlparse
import re
import requests
from bs4 import BeautifulSoup
import zipfile

BASE_URL = "https://www.nitk.ac.in/"
OUTPUT_FILE = "knowledge.json"
MAX_PAGES = 2500

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


import os

print("=" * 60)
print("DEBUG INFORMATION")
print("Current working directory:", os.getcwd())
print("Chroma absolute path:", os.path.abspath("nitk_chroma"))
print("SQLite exists:", os.path.exists("nitk_chroma/chroma.sqlite3"))
print("=" * 60)


print("=" * 60)
print("Collection object:", collection)

if collection is not None:
    print("Count:", collection.count())
    print("Peek:")
    print(collection.peek(limit=3))
else:
    print("Collection not initialized yet.")

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

    if os.path.exists("knowledge.json"):
        print("knowledge.json found.")
        return

    raise FileNotFoundError(
        "knowledge.json is missing. Run the crawler first."
    )


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
        