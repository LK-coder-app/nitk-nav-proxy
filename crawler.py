import json
import os
import time
from collections import deque
from urllib.parse import urljoin, urlparse
from rank_bm25 import BM25Okapi
import re
import requests
from bs4 import BeautifulSoup
import zipfile

BASE_URL = "https://www.nitk.ac.in/"
OUTPUT_FILE = "knowledge.json"
_bm25 = None
_pages = None
MAX_PAGES = 2500

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


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
    global _bm25, _pages

    _pages = []

    knowledge = load_knowledge()

    if not knowledge:
        print("No knowledge available.")

        return

    corpus = []

    seen_chunks = set()

    for page in knowledge:

        chunks = split_into_chunks(page["text"])

        for i, chunk in enumerate(chunks):

            chunk_key = clean_text(chunk).lower()

            if chunk_key in seen_chunks:
                continue

            seen_chunks.add(chunk_key)

            record = {
                "title": page["title"],
                "url": page["url"],
                "chunk": i,
                "text": chunk
            }

            _pages.append(record)

            corpus.append(
                re.findall(
                    r'\w+',
                    (page["title"] + " " + chunk).lower()
                )
            )

    if not corpus:
        print("Search index is empty.")

        return

    _bm25 = BM25Okapi(corpus)

    print(f"Loaded {_bm25.corpus_size} pages into search index.")


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
                if link.endswith(".pdf"):
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

    r = requests.get(url, stream=True, timeout=300)

    r.raise_for_status()

    with open("knowledge.zip", "wb") as f:
        for chunk in r.iter_content(1024 * 1024):
            if chunk:
                f.write(chunk)

    print("Extracting knowledge...")

    with zipfile.ZipFile("knowledge.zip", "r") as zip_ref:
        zip_ref.extractall(".")

    os.remove("knowledge.zip")

    if not os.path.exists(OUTPUT_FILE):
        raise RuntimeError("knowledge.json was not extracted correctly!")

    print("Knowledge downloaded successfully.")



def search_knowledge(query, top_k=5):
    global _bm25, _pages

    if _bm25 is None:
        build_search_index()

    STOP_WORDS = {
        "who","what","where","when","why","how",
        "tell","give","show","about","please",
        "me","the","a","an","of","for","to","in",
        "on","at","is","are","was","were","does",
        "do","can","could","would"
    }

    query = query.lower()

    # -------- Query Expansion --------

    REPLACEMENTS = {

        "placements": "placement",
        "placement statistics": "placement report",
        "placement record": "placement report",
        "placement data": "placement report",

        "cdc": "career development center",
        "career centre": "career development center",

        "hod": "head of department",
        "head": "head of department",

        "prof": "professor",
        "faculty": "staff professor",

        "staff": "faculty",
        "teachers": "faculty",

        "hostels": "hostel",
        "messes": "mess",

        "admission": "admissions",

        "btech": "undergraduate",
        "mtech": "postgraduate",

        "phd": "doctor of philosophy",

        "cse": "computer science",
        "ece": "electronics",
        "eee": "electrical",
        "mech": "mechanical engineering",
        "civil": "civil engineering",
        "chem": "chemical engineering",

        "students": "student",
        "labs": "laboratory",

        "director mail": "director office",
        "director email": "director office",

    }

    for old, new in REPLACEMENTS.items():
        query = query.replace(old, new)

    # -------------------------------

    query_tokens = [
        w for w in re.findall(r'\w+', query)
        if w not in STOP_WORDS
    ]

    print("=" * 50)
    print("Query:", query)
    print("Tokens:", query_tokens)

    scores = _bm25.get_scores(query_tokens)

    boosted = []

    for score, page in zip(scores, _pages):

        title = page["title"].lower()

        boost = 0

        # Boost if the whole query appears in the title
        if query in title:
            boost += 10

        # Boost for individual keywords appearing in the title
        for token in query_tokens:
            if token in title:
                boost += 2

        text = page["text"].lower()

        for token in query_tokens:

            if token in text:
                boost += 0.3

        boosted.append((score + boost, page))

    ranked = sorted(
        boosted,
        key=lambda x: x[0],
        reverse=True
    )

    unique_results = []

    seen_urls = set()

    for score, page in ranked:

        if score <= 0:
            continue

        if page["url"] in seen_urls:
            continue

        seen_urls.add(page["url"])

        unique_results.append((score, page))

    print("Top Results:")

    for score, page in ranked[:5]:
        print(score, page["title"])

    results = [
        page
        for score, page in unique_results[:top_k]
    ]

    print("Returned:", len(results))

    return results

def build_context(query):
    

    pages = search_knowledge(query, top_k=10)

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
        