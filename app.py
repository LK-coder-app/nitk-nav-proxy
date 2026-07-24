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
from google import genai

from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ── Environment variables (set these in Render dashboard) ─────────────────
ORS_KEY        = os.environ.get('ORS_KEY', '')
TWILIO_SID     = os.environ.get('TWILIO_SID', '')
TWILIO_TOKEN   = os.environ.get('TWILIO_TOKEN', '')
TWILIO_FROM    = os.environ.get('TWILIO_FROM', '')
GMAIL_USER     = os.environ.get('GMAIL_USER', '')
GMAIL_PASS     = os.environ.get('GMAIL_PASS', '')
GMAIL_TO       = os.environ.get('GMAIL_TO', '')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL   = 'gemini-2.5-flash'
# ── Auto-updating NITK knowledge base — scraped from nitk.ac.in ───────────
NITK_BASE_URL          = 'https://www.nitk.ac.in/'
MAX_PAGES_TO_CRAWL     = 18
KNOWLEDGE_REFRESH_KEY  = os.environ.get('KNOWLEDGE_REFRESH_KEY', 'changeme')

_knowledge_chunks = []          # [{'text':..., 'source':..., 'embedding': np.array}, ...]
_knowledge_lock   = threading.Lock()
_knowledge_ready  = False

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

client = genai.Client(api_key=GEMINI_API_KEY)

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

def _fetch_page(url):
    try:
        resp = requests.get(url, timeout=15, headers={
            'User-Agent': 'Mozilla/5.0 (NITK-Nav-Assistant/1.0)'
        })
        if resp.status_code != 200:
            return None, []
        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'noscript']):
            tag.decompose()
        text = ' '.join(soup.get_text(separator=' ', strip=True).split())

        links = []
        base_domain = urlparse(NITK_BASE_URL).netloc
        for a in soup.find_all('a', href=True):
            full_url = urljoin(url, a['href']).split('#')[0]
            if urlparse(full_url).netloc == base_domain and full_url.startswith('http'):
                if not any(full_url.lower().endswith(ext)
                           for ext in ['.pdf', '.jpg', '.png', '.zip', '.doc', '.docx']):
                    links.append(full_url)
        return text, links
    except Exception as e:
        print(f'⚠️ Failed to fetch {url}: {e}')
        return None, []


def _chunk_text(text, source, chunk_words=180, overlap_words=40):
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        piece = words[i:i + chunk_words]
        if len(piece) < 30:
            break
        chunks.append({'text': ' '.join(piece), 'source': source})
        i += chunk_words - overlap_words
    return chunks


def _embed_text(text):
    try:
        response = client.models.embed_content(
            model="gemini-embedding-001",
            contents=text,
        )

        return np.array(response.embeddings[0].values, dtype=np.float32)

    except Exception as e:
        print(f"⚠️ Embedding failed: {e}")
        return None


def _build_knowledge_base():
    global _knowledge_chunks, _knowledge_ready
    print('🔄 Refreshing NITK knowledge base from nitk.ac.in ...')

    visited, to_visit, new_chunks = set(), [NITK_BASE_URL], []

    while to_visit and len(visited) < MAX_PAGES_TO_CRAWL:
        url = to_visit.pop(0)
        if url in visited:
            continue
        visited.add(url)

        text, links = _fetch_page(url)
        if text and len(text) > 200:
            for chunk in _chunk_text(text, url):
                emb = _embed_text(chunk['text'])
                if emb is not None:
                    chunk['embedding'] = emb
                    new_chunks.append(chunk)
            print(f'✅ Indexed {url}')

        for link in links:
            if link not in visited and link not in to_visit:
                to_visit.append(link)

    with _knowledge_lock:
        if new_chunks:
            _knowledge_chunks = new_chunks
            _knowledge_ready = True
            print(f"✅ Saved {len(_knowledge_chunks)} chunks")
        else:
            print("❌ No chunks generated. Keeping previous knowledge base.")

    print(f"✅ Knowledge base ready — {len(new_chunks)} chunks from {len(visited)} pages")


def _retrieve_relevant_chunks(query, top_k=5):
    with _knowledge_lock:
        snapshot = list(_knowledge_chunks)
    if not snapshot:
        return []
    q_emb = _embed_text(query)
    if q_emb is None:
        return []
    scored = [
        (float(np.dot(q_emb, c['embedding']) /
               (np.linalg.norm(q_emb) * np.linalg.norm(c['embedding']) + 1e-8)), c)
        for c in snapshot
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:top_k]]

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
    try:
        data    = request.get_json(force=True)
        message = (data.get('message') or '').strip()
        history = data.get('history', [])
        if not message:
            return jsonify({'reply': ''})

        relevant = _retrieve_relevant_chunks(message, top_k=5)
        context_text = '\n\n'.join(f"[Source: {c['source']}]\n{c['text']}" for c in relevant) \
            if relevant else "(No specific retrieved content — be upfront if unsure of specifics.)"

        system_prompt = f"{NITK_CHAT_BASE_PROMPT}\n\n──────────\n{context_text}\n──────────"

        contents = [{"role": t.get('role', 'user'), "parts": [{"text": t.get('text', '')}]}
                    for t in history]
        contents.append({"role": "user", "parts": [{"text": message}]})

        gemini_url = f'https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent'
        body = json.dumps({
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
        }).encode()
        req = urllib.request.Request(
            gemini_url, data=body, method='POST',
            headers={'Content-Type': 'application/json', 'x-goog-api-key': GEMINI_API_KEY}
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            result = json.loads(r.read().decode())

        if not result.get('candidates'):
            return jsonify({'reply': "Sorry, I couldn't process that. Please try again."})

        reply_text = result['candidates'][0]['content']['parts'][0]['text']
        return jsonify({'reply': reply_text.strip()})

    except Exception as e:
        print(f'❌ NITK chat error: {e}')
        return jsonify({'reply': "Sorry, something went wrong. Please try again."}), 500


@app.route('/refresh-knowledge')
def refresh_knowledge():
    if request.args.get('key', '') != KNOWLEDGE_REFRESH_KEY:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    threading.Thread(target=_build_knowledge_base, daemon=True).start()
    return jsonify({'success': True, 'message': 'Refresh started in background'})


@app.route('/knowledge-status')
def knowledge_status():
    with _knowledge_lock:
        print("STATUS:", len(_knowledge_chunks), _knowledge_ready)
        return jsonify({
            "ready": _knowledge_ready,
            "chunkCount": len(_knowledge_chunks)
        })


# Build the knowledge base once at startup, in the background, so it's not
# empty for the first users. Runs under gunicorn too (module-level, not
# inside __main__).
# Disabled automatic knowledge build
# threading.Thread(target=_build_knowledge_base, daemon=True).start()

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


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8081))
    print(f'✅ Server starting on port {port}')
    app.run(host='0.0.0.0', port=port)

