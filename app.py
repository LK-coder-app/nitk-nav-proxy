from multiprocessing import context
import os
import json
import random
import time
import base64
import urllib.request
import urllib.parse
import urllib.error
import smtplib
from email.mime.text import MIMEText
import firebase_admin
from firebase_admin import credentials, auth as fb_auth
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import numpy as np
import threading
from openai import OpenAI
import re
from urllib.parse import quote
from crawler import (
    build_search_index,
    refresh_knowledge,
    download_knowledge,
    get_collection_count
)
from crawler import download_chroma
from flask import Flask, request, jsonify
from flask_cors import CORS
from crawler import build_context
from dotenv import load_dotenv
from build_chroma import build_database

load_dotenv()

app = Flask(__name__)
CORS(app)
knowledge_ready = False

# ── Environment variables (set these in Render dashboard) ─────────────────
ORS_KEY        = os.environ.get('ORS_KEY', '')
TWILIO_SID     = os.environ.get('TWILIO_SID', '')
TWILIO_TOKEN   = os.environ.get('TWILIO_TOKEN', '')
TWILIO_FROM    = os.environ.get('TWILIO_FROM', '')
GMAIL_USER     = os.environ.get('GMAIL_USER', '')
GMAIL_PASS     = os.environ.get('GMAIL_PASS', '')
GMAIL_TO       = os.environ.get('GMAIL_TO', '')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
KNOWLEDGE_REFRESH_KEY = os.environ.get('KNOWLEDGE_REFRESH_KEY', '') 
GEMINI_MODEL   = 'gemini-2.5-flash'
# ── Auto-updating NITK knowledge base — scraped from nitk.ac.in ───────────
# ── Firebase Admin SDK — needed for OTP-based login and password reset ────
FIREBASE_SERVICE_ACCOUNT_JSON = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON', '')

if FIREBASE_SERVICE_ACCOUNT_JSON:
    try:
        _service_account_info = json.loads(FIREBASE_SERVICE_ACCOUNT_JSON)
        _cred = credentials.Certificate(_service_account_info)
        firebase_admin.initialize_app(_cred)
        print('✅ Firebase Admin initialized')
    except Exception as e:
        print(f'❌ Firebase Admin init failed: {e}')
else:
    print('⚠️ FIREBASE_SERVICE_ACCOUNT_JSON not set — auth endpoints will fail')

_account_otp_store = {}  # email -> {otp, expiry}

print("GROQ key exists:", bool(GROQ_API_KEY))
print("GROQ key length:", len(GROQ_API_KEY))

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is missing!")

groq_client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

def _send_twilio_sms(phone_e164, body_text):
    payload = urllib.parse.urlencode({
        'To':   phone_e164,
        'From': TWILIO_FROM,
        'Body': body_text,
    }).encode()
    twilio_url = f'https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json'
    creds_b64 = base64.b64encode(f'{TWILIO_SID}:{TWILIO_TOKEN}'.encode()).decode()
    req = urllib.request.Request(
        twilio_url, data=payload, method='POST',
        headers={
            'Authorization': f'Basic {creds_b64}',
            'Content-Type':  'application/x-www-form-urlencoded',
        }
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

import threading
import time

def auto_refresh():
    while True:
        try:
            print("Refreshing NITK knowledge...")
            refresh_knowledge()
            print("Knowledge refreshed successfully.")
        except Exception as e:
            print("Refresh Error:", e)

        # Sleep for 24 hours
        time.sleep(24 * 60 * 60)
        
def initialize_knowledge():
    global knowledge_ready

    try:
        print("=" * 60)
        print("Initializing NITK Knowledge Base...")

        print("Step 1: Checking knowledge.json")

        # Step 1 - Download knowledge.json only if missing
        if not os.path.exists("knowledge.json"):
            print("knowledge.json not found.")
            download_knowledge()
        else:
            print("knowledge.json found.")

        print("Step 2: knowledge.json OK")
        # Step 2 - Check ChromaDB
        print("Step 3: Checking ChromaDB")
        count = get_collection_count()

        print(f"Collection count: {count}")

        if count == 0:
            print("Downloading ChromaDB...")
            print("Building ChromaDB from knowledge.json...")
            build_database()

            client = chromadb.PersistentClient(path="nitk_chroma")

            collection = client.get_collection(
                name="nitk",
                embedding_function=embedding_function
            )

            count = get_collection_count()
            print(f"Collection count after download: {count}")

        else:
            print(f"ChromaDB already contains {count} documents.")

        print("Step 5: Setting knowledge_ready")

        knowledge_ready = True

        print("Knowledge initialization completed.")
        print("=" * 60)

    except Exception as e:
        print("Knowledge initialization failed:")
        print(e)

    #threading.Thread(
        #target=auto_refresh,
        #daemon=True
    #).start()



# Initialize knowledge base when the app is imported by Gunicorn
initialize_knowledge()

def search_nitk_page(query):
    """
    Search the official NITK website using Google's index and
    return the best matching NITK page.
    """

    try:
        headers = {
            "User-Agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }

        search_url = (
            "https://www.google.com/search?q="
            + quote("site:nitk.ac.in " + query)
        )

        html = requests.get(
            search_url,
            headers=headers,
            timeout=10
        ).text

        # Find NITK links inside Google's response
        links = re.findall(
            r'https://www\.nitk\.ac\.in[^"&<> ]+',
            html
        )

        seen = set()

        for link in links:
            if link in seen:
                continue

            seen.add(link)

            if "/document/" in link:
                continue

            if ".pdf" in link.lower():
                continue

            return link

        return "https://www.nitk.ac.in/"

    except Exception as e:
        print("Search Error:", e)
        return "https://www.nitk.ac.in/"


def fetch_nitk_page(url):
    try:
        headers = {
            "User-Agent":
            "Mozilla/5.0"
        }

        response = requests.get(
            url,
            headers=headers,
            timeout=10
        )

        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()

        text = soup.get_text(separator=" ")

        return " ".join(text.split())[:25000]

    except Exception as e:
        print(e)
        return ""

# ── OTP store (in-memory — resets on server restart, fine for free tier) ──
_otp_store = {}

# ── NITK Chatbot system prompt (top-level constant, used by /nitk-chat) ───
NITK_CHAT_BASE_PROMPT = """You are the NITK Assistant, a friendly conversational AI for
National Institute of Technology Karnataka (NITK), Surathkal. Chat naturally and
helpfully — but ONLY about NITK and directly related topics.

OUT OF SCOPE: politely decline anything unrelated to NITK, no matter how it's rephrased.

Below is information retrieved live from NITK's official website. Base your answer on
it. If it doesn't cover the question, say so honestly and suggest checking nitk.ac.in
directly — never invent specifics that aren't supported by the retrieved text."""

threading.Thread(
    target=initialize_knowledge,
    daemon=True
).start()
# ─────────────────────────────────────────────────────────────────────────────
# ORS ROUTING
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/ors-route')
def ors_route():
    start = request.args.get('start', '')
    end   = request.args.get('end',   '')

    if not start or not end:
        return jsonify({'error': 'Missing start or end'}), 400

    print(f'📍 Routing {start} → {end}')

    url = (
        f'https://api.openrouteservice.org'
        f'/v2/directions/foot-walking'
        f'?api_key={ORS_KEY}'
        f'&start={start}&end={end}'
    )
    req = urllib.request.Request(
        url,
        headers={
            'Accept':       'application/json, application/geo+json',
            'Content-Type': 'application/json',
            'User-Agent':   'Mozilla/5.0 CampusNav/1.0',
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
        print(f'✅ ORS returned {len(data)} bytes')
        return app.response_class(
            response=data,
            status=200,
            mimetype='application/json'
        )
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f'❌ ORS HTTP {e.code}: {body}')
        return app.response_class(
            response=body,
            status=500,
            mimetype='application/json'
        )
    except Exception as e:
        print(f'❌ ORS error: {e}')
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# SEND OTP (Twilio)
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/send-otp')
def send_otp():
    phone = request.args.get('phone', '')

    if not phone or len(phone) != 10:
        return jsonify({'success': False, 'message': 'Invalid phone number'})

    otp    = str(random.randint(100000, 999999))
    expiry = time.time() + 300
    _otp_store[phone] = {'otp': otp, 'expiry': expiry}

    print(f'📱 OTP for {phone}: {otp}')

    try:
        to_number = '+91' + phone
        payload   = urllib.parse.urlencode({
            'To':   to_number,
            'From': TWILIO_FROM,
            'Body': f'Your NITK Navigation OTP is {otp}. Valid for 5 minutes. Do not share.',
        }).encode()

        twilio_url  = f'https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json'
        credentials = base64.b64encode(
            f'{TWILIO_SID}:{TWILIO_TOKEN}'.encode()
        ).decode()

        req = urllib.request.Request(
            twilio_url,
            data    = payload,
            method  = 'POST',
            headers = {
                'Authorization': f'Basic {credentials}',
                'Content-Type':  'application/x-www-form-urlencoded',
            }
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read().decode())
            print(f'📨 Twilio SMS sent: {result.get("sid")}')

        return jsonify({'success': True, 'message': 'OTP sent successfully'})

    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f'❌ Twilio error {e.code}: {body}')
        return jsonify({
            'success': True,
            'message': 'OTP sent — check server logs',
            'demo_otp': otp
        })
    except Exception as e:
        print(f'❌ OTP send error: {e}')
        return jsonify({
            'success': True,
            'message': 'OTP sent — check server logs',
            'demo_otp': otp
        })


# ─────────────────────────────────────────────────────────────────────────────
# VERIFY OTP
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/verify-otp')
def verify_otp():
    phone = request.args.get('phone', '')
    otp   = request.args.get('otp',   '')

    record = _otp_store.get(phone)

    if not record:
        return jsonify({'success': False,
                        'message': 'No OTP found. Please request again.'})

    if time.time() > record['expiry']:
        del _otp_store[phone]
        return jsonify({'success': False,
                        'message': 'OTP expired. Please request a new one.'})

    if record['otp'] != otp:
        return jsonify({'success': False,
                        'message': 'Incorrect OTP. Please try again.'})

    del _otp_store[phone]
    return jsonify({'success': True, 'message': 'OTP verified successfully'})


# ─────────────────────────────────────────────────────────────────────────────
# CONTACT EMAIL
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/send-contact', methods=['POST'])
def send_contact():
    try:
        data    = request.get_json(force=True)
        name    = data.get('name',    '')
        email   = data.get('email',   '')
        message = data.get('message', '')

        print(f'📧 Contact from {name} ({email})')

        msg = MIMEText(
            f'Name: {name}\n'
            f'Email: {email}\n\n'
            f'Message:\n{message}',
            'plain'
        )
        msg['Subject'] = f'NITK Nav Contact: {name}'
        msg['From']    = GMAIL_USER
        msg['To']      = GMAIL_TO

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_USER, GMAIL_PASS)
            s.send_message(msg)

        print(f'✅ Contact email sent')
        return jsonify({'success': True})

    except Exception as e:
        print(f'❌ Email error: {e}')
        return jsonify({'success': False, 'message': str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# NATURAL LANGUAGE DESTINATION SEARCH (typed or transcribed voice, 3 languages)
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/nl-destination', methods=['POST'])
def nl_destination():
    try:
        data      = request.get_json(force=True)
        query     = (data.get('query') or '').strip()
        buildings = data.get('buildings', [])

        if not query:
            return jsonify({'buildingId': None, 'reply': ''})

        building_list_text = '\n'.join(
            f"- id: {b.get('id')}, name: {b.get('name')}, info: {b.get('info')}"
            for b in buildings
        )

        system_prompt = (
            "You are a campus navigation assistant for NITK Surathkal. "
            "The user describes where they want to go, in English, Hindi, or Kannada — "
            "either a direct building name or a natural description "
            "(for example 'I want to print something' should match a library or admin block). "
            "Here is the list of available campus buildings:\n"
            f"{building_list_text}\n\n"
            "Reply with ONLY raw JSON, no markdown, no code fences, in this exact format:\n"
            '{"buildingId": "<id from the list above, or null if no good match>", '
            '"reply": "<a short, friendly one-sentence reply in the SAME language the user wrote in, '
            'confirming the destination, or asking them to clarify if there is no good match>"}'
        )

        gemini_url = (
            f'https://generativelanguage.googleapis.com/v1beta/models/'
            f'{GEMINI_MODEL}:generateContent'
        )

        body = json.dumps({
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": query}]}],
        }).encode()

        req = urllib.request.Request(
            gemini_url,
            data=body,
            method='POST',
            headers={
                'Content-Type':   'application/json',
                'x-goog-api-key': GEMINI_API_KEY,
            }
        )

        with urllib.request.urlopen(req, timeout=20) as r:
            result = json.loads(r.read().decode())

        if not result.get('candidates'):
            return jsonify({'buildingId': None, 'reply': '', 'error': 'no_candidates'})

        text  = result['candidates'][0]['content']['parts'][0]['text']
        clean = text.strip()
        if clean.startswith('```'):
            clean = clean.split('```')[1]
            if clean.startswith('json'):
                clean = clean[4:]
        clean = clean.strip()

        parsed = json.loads(clean)
        print(f'🤖 NL query: "{query}" → {parsed}')
        return jsonify(parsed)

    except Exception as e:
        print(f'❌ NL destination error: {e}')
        return jsonify({'buildingId': None, 'reply': '', 'error': str(e)})

# ─────────────────────────────────────────────────────────────────────────────
# NITK CHATBOT — general conversation, scoped to NITK topics only
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/nitk-chat', methods=['POST'])
def nitk_chat():
    global knowledge_ready

    if not knowledge_ready:
        return jsonify({
            "reply": "The NITK knowledge base is still loading. Please try again in a minute."
        })
    try:
        data = request.get_json(force=True)

        message = (data.get("message") or "").strip()

        history = data.get("history", [])

        if not message:
            return jsonify({"reply": ""})

        context = build_context(message)

        print("=" * 50)
        print("Message:", message)
        print("Context received:", len(context))
        print(context[:1000])
        
        if not context.strip():
            return jsonify({
                "reply": "I couldn't find that information in the available NITK knowledge base."
            })
        prompt = f"""
        You are the official AI Assistant for NITK Surathkal.

        You must answer ONLY from the retrieved knowledge below.

        IMPORTANT RULES

        1. Never invent facts.

        2. Never use outside knowledge.

        3. Combine information from multiple retrieved pages into one complete answer.

        4. If the answer appears in a table, summarize it naturally.

        5. If multiple pages mention different parts of the answer, merge them.

        6. Mention the relevant department or page title whenever appropriate.

        7. If the answer is not present in the retrieved knowledge, reply exactly:

        "I couldn't find that information in the available NITK knowledge base."

        8. Keep answers clear, well-structured, and concise.

        --------------------------
        NITK KNOWLEDGE

        {context}

        --------------------------

        User Question:

        {message}
        """

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "You are the official AI Assistant for NITK Surathkal."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3,
        )

        reply = response.choices[0].message.content

        return jsonify({
            "reply": reply.strip()
        })

    except Exception as e:

        print("CHAT ERROR:", e)

        return jsonify({
            "reply": "Sorry, something went wrong."
        }), 500


# Build the knowledge base once at startup, in the background, so it's not
# empty for the first users. Runs under gunicorn too (module-level, not
# inside __main__).


# ─────────────────────────────────────────────────────────────────────────────
# STORE VERIFIED PHONE — called once, right after registration
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/set-user-phone', methods=['POST'])
def set_user_phone():
    try:
        data  = request.get_json(force=True)
        uid   = data.get('uid', '')
        phone = data.get('phone', '')

        if not uid or not phone or len(phone) != 10:
            return jsonify({'success': False, 'message': 'Missing uid or invalid phone'})

        fb_auth.update_user(uid, phone_number=f'+91{phone}')
        return jsonify({'success': True})
    except Exception as e:
        print(f'❌ set_user_phone error: {e}')
        return jsonify({'success': False, 'message': str(e)})

@app.route('/check-phone-status')
def check_phone_status():
    try:
        email = (request.args.get('email') or '').strip().lower()
        if not email:
            return jsonify({'success': False, 'message': 'Email required'})
        try:
            user = fb_auth.get_user_by_email(email)
        except fb_auth.UserNotFoundError:
            return jsonify({'success': False, 'message': 'No account found with this email'})
        return jsonify({'success': True, 'hasVerifiedPhone': bool(user.phone_number)})
    except Exception as e:
        print(f'❌ check_phone_status error: {e}')
        return jsonify({'success': False, 'message': str(e)})
# ─────────────────────────────────────────────────────────────────────────────
# SEND OTP — used for both OTP-login and password reset (same purpose: prove
# you own the account associated with this email, via its verified phone)
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/send-login-otp', methods=['POST'])
def send_login_otp():
    try:
        data  = request.get_json(force=True)
        email = (data.get('email') or '').strip().lower()
        if not email:
            return jsonify({'success': False, 'message': 'Email required'})

        try:
            user = fb_auth.get_user_by_email(email)
        except fb_auth.UserNotFoundError:
            return jsonify({'success': False, 'message': 'No account found with this email'})

        phone = user.phone_number
        if not phone:
            return jsonify({'success': False,
                            'message': 'No verified phone number on file for this account'})

        otp = str(random.randint(100000, 999999))
        _account_otp_store[email] = {'otp': otp, 'expiry': time.time() + 300}
        print(f'📱 Account OTP for {email} ({phone}): {otp}')

        masked = phone
        if len(phone) > 7:
            masked = phone[:3] + '•' * (len(phone) - 7) + phone[-4:]

        try:
            _send_twilio_sms(phone, f'Your NITK Navigation OTP is {otp}. Valid for 5 minutes.')
        except Exception as e:
            print(f'⚠️ Twilio send failed (non-fatal, OTP still valid): {e}')

        return jsonify({'success': True, 'maskedPhone': masked, 'demo_otp': otp})

    except Exception as e:
        print(f'❌ send_login_otp error: {e}')
        return jsonify({'success': False, 'message': str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# VERIFY OTP → LOGIN (mints a real Firebase sign-in token)
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/verify-login-otp', methods=['POST'])
def verify_login_otp():
    try:
        data  = request.get_json(force=True)
        email = (data.get('email') or '').strip().lower()
        otp   = (data.get('otp') or '').strip()

        record = _account_otp_store.get(email)
        if not record:
            return jsonify({'success': False, 'message': 'No OTP found. Please request again.'})
        if time.time() > record['expiry']:
            del _account_otp_store[email]
            return jsonify({'success': False, 'message': 'OTP expired. Please request a new one.'})
        if record['otp'] != otp:
            return jsonify({'success': False, 'message': 'Incorrect OTP.'})

        del _account_otp_store[email]

        user = fb_auth.get_user_by_email(email)
        token = fb_auth.create_custom_token(user.uid)
        token_str = token.decode('utf-8') if isinstance(token, bytes) else token

        return jsonify({'success': True, 'token': token_str})

    except Exception as e:
        print(f'❌ verify_login_otp error: {e}')
        return jsonify({'success': False, 'message': str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# VERIFY OTP → RESET PASSWORD
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/verify-reset-otp', methods=['POST'])
def verify_reset_otp():
    try:
        data         = request.get_json(force=True)
        email        = (data.get('email') or '').strip().lower()
        otp          = (data.get('otp') or '').strip()
        new_password = data.get('newPassword', '')

        if len(new_password) < 6:
            return jsonify({'success': False, 'message': 'Password must be at least 6 characters'})

        record = _account_otp_store.get(email)
        if not record:
            return jsonify({'success': False, 'message': 'No OTP found. Please request again.'})
        if time.time() > record['expiry']:
            del _account_otp_store[email]
            return jsonify({'success': False, 'message': 'OTP expired. Please request a new one.'})
        if record['otp'] != otp:
            return jsonify({'success': False, 'message': 'Incorrect OTP.'})

        del _account_otp_store[email]

        user = fb_auth.get_user_by_email(email)
        fb_auth.update_user(user.uid, password=new_password)

        return jsonify({'success': True, 'message': 'Password changed successfully'})

    except Exception as e:
        print(f'❌ verify_reset_otp error: {e}')
        return jsonify({'success': False, 'message': str(e)})

# ─────────────────────────────────────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/')
def health():
    return jsonify({'status': 'NITK Navigation Proxy running ✅'})

@app.route('/ping')
def ping():
    return jsonify({'pong': True})

@app.route("/refresh-knowledge")
def refresh():

    key = request.args.get("key", "")

    if key != KNOWLEDGE_REFRESH_KEY:
        return jsonify({
            "success": False
        }), 403

    threading.Thread(
        target=refresh_knowledge,
        daemon=True
    ).start()

    return jsonify({
        "success": True,
        "message": "Knowledge refresh started."
    })

from crawler import load_knowledge

@app.route("/knowledge-status")
def knowledge_status():

    pages = load_knowledge()

    return jsonify({
        "ready": len(pages) > 0,
        "pages": len(pages)
    })

@app.route("/debug-db")
def debug_db():

    import sqlite3

    conn = sqlite3.connect("nitk_chroma/chroma.sqlite3")
    cur = conn.cursor()

    cur.execute("""
        SELECT id, scope
        FROM segments
    """)

    segments = cur.fetchall()

    cur.execute("""
        SELECT segment_id,
               COUNT(*)
        FROM embeddings
        GROUP BY segment_id
    """)

    counts = cur.fetchall()

    conn.close()

    return {
        "segments": segments,
        "embedding_counts": counts
    }

@app.route("/debug-files")
def debug_files():

    import os

    result = []

    for root, dirs, files in os.walk("nitk_chroma"):
        for f in files:
            path = os.path.join(root, f)
            result.append({
                "file": path,
                "size": os.path.getsize(path)
            })

    return result
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8081))
    print(f'✅ Server starting on port {port}')
    app.run(host='0.0.0.0', port=port)

