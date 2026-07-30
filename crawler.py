import json
import os
import time
from collections import deque
from urllib.parse import urljoin, urlparse
from rank_bm25 import BM25Okapi
import re
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.nitk.ac.in/"
OUTPUT_FILE = "knowledge.json"
_bm25 = None
_pages = None
MAX_PAGES = 200

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def clean_text(text):
    return " ".join(text.split())


def build_search_index():
    global _bm25, _pages

    _pages = load_knowledge()

    corpus = []

    for page in _pages:
        text = (
            page["title"] + " " +
            page["text"]
        ).lower()

        corpus.append(
            re.findall(r'\w+', text)
        )

    _bm25 = BM25Okapi(corpus)

    print(f"Loaded {_bm25.corpus_size} pages into search index.")


def extract_text(html):
    soup = BeautifulSoup(html, "html.parser")

    # Remove unwanted tags
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
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

    lines = []
    seen = set()

    for line in text.splitlines():
        line = line.strip()

        if len(line) < 2:
            continue

        if line in seen:
            continue

        seen.add(line)
        lines.append(line)

    text = "\n".join(lines)
    
    return title, text


def crawl():

    visited = set()
    queue = deque([BASE_URL])

    pages = []

    while queue and len(visited) < MAX_PAGES:

        url = queue.popleft()

        if url in visited:
            continue

        visited.add(url)

        print("Crawling:", url)

        try:

            r = requests.get(url, headers=HEADERS, timeout=15)

            if r.status_code != 200:
                continue

            title, text = extract_text(r.text)

            if len(text) > 300:

                pages.append({
                    "title": title,
                    "url": url,
                    "text": text
                })

            soup = BeautifulSoup(r.text, "html.parser")

            for a in soup.find_all("a", href=True):

                link = urljoin(url, a["href"])

                parsed = urlparse(link)

                if parsed.netloc != "www.nitk.ac.in":
                    continue

                link = parsed.scheme + "://" + parsed.netloc + parsed.path

                if link.endswith(".pdf"):
                    continue

                if link not in visited:
                    queue.append(link)

        except Exception as e:

            print(e)

        time.sleep(0.3)

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


def search_knowledge(query, top_k=5):
    global _bm25, _pages

    if _bm25 is None:
        build_search_index()

    STOP_WORDS = {
        "who", "what", "where", "when", "why", "how",
        "is", "are", "was", "were", "the", "a", "an",
        "of", "for", "to", "in", "on", "at", "and",
        "tell", "me", "about", "please"
    }

    query_tokens = [
        w for w in re.findall(r'\w+', query.lower())
        if w not in STOP_WORDS
    ]

    print("=" * 50)
    print("Query:", query)
    print("Tokens:", query_tokens)

    scores = _bm25.get_scores(query_tokens)

    ranked = sorted(
        zip(scores, _pages),
        key=lambda x: x[0],
        reverse=True
    )

    print("Top Results:")

    for score, page in ranked[:5]:
        print(score, page["title"])

    results = [page for score, page in ranked[:top_k] if score > 0]

    print("Returned:", len(results))

    return results

def build_context(query):
    

    pages = search_knowledge(query, top_k=3)

    if not pages:
        return ""

    context = ""

    for page in pages:

        text = page["text"][:1200]

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
        
pages = load_knowledge()

count = 0

for page in pages:
    text = page["text"].lower()

    if "placement" in text:
        count += 1
        print(page["title"])
        print(page["url"])
        print("-" * 60)

print("Found:", count)