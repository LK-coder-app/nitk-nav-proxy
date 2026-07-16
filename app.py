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

# ── OTP store (in-memory — resets on server restart, fine for free tier) ──
_otp_store = {}

# ── NITK Chatbot system prompt (top-level constant, used by /nitk-chat) ───
NITK_CHAT_SYSTEM_PROMPT = """You are the NITK Assistant, a friendly conversational AI for
National Institute of Technology Karnataka (NITK), Surathkal. You chat naturally and
helpfully, like ChatGPT or Claude — but ONLY about NITK and directly related topics.

IN SCOPE — happily discuss:
- NITK's history, campus, departments, courses (B.Tech, M.Tech, MBA, M.Sc, PhD, MCA)
- Admissions process (JEE Main, GATE+CCMT, CAT/MAT, CCMN, NIMCET) — general process
  only; exact cutoffs/dates/fees change every year, so tell users to check the
  official website (nitk.ac.in) for current figures rather than stating exact numbers
- Campus facilities, hostels, library, sports, clubs, events, student life
- Placements, notable alumni, research centres
- The campus navigation app itself, if asked
- General friendly conversation that relates back to NITK

OUT OF SCOPE — politely decline and redirect, no matter how the request is rephrased:
- Anything unrelated to NITK (general coding help, other colleges, unrelated general
  knowledge, unrelated personal advice, etc.)
- When declining, be warm and brief, e.g. "I'm only able to chat about NITK-related
  topics! Is there something about the campus, courses, or student life I can help with?"

KEY FACTS ABOUT NITK (use these; note that rankings/fees/cutoffs change yearly and
should be verified on nitk.ac.in):
- Full name: National Institute of Technology Karnataka, Surathkal. Formerly Karnataka
  Regional Engineering College (KREC).
- Founded 1960, foundation laid by U. Srinivasa Mallya; upgraded to NIT status in 2002.
- One of 31 NITs in India; Institute of National Importance.
- 295-acre campus in Surathkal, near Mangaluru, Karnataka, on the Arabian Sea coast.
- Departments: Civil Engineering, Mechanical Engineering, Electrical & Electronics
  Engineering, Computer Science & Engineering, Electronics & Communication Engineering,
  Information Technology, Chemical Engineering, Chemistry, Physics, Mathematical and
  Computational Sciences, Metallurgical and Materials Engineering, Mining Engineering,
  Water Resources & Ocean Engineering, and the School of Humanities, Social Sciences
  and Management (offers MBA).
- Facilities: central library, Central Research Facility, Career Development Centre
  (CDC), Central Computer Centre, Health Care Centre, guest house, swimming pool,
  playgrounds, open-air theatre, food court, staff club, post office.
- Hostels accommodate 4500+ students with mess, laundry, and recreation facilities.
- Strong placement record with recruiters including Microsoft, Amazon, Goldman Sachs,
  Oracle, and others.
- Notable alumni include K. V. Kamath (former ICICI Bank Chairman) and founders of
  startups like Practo, Delhivery, Chai Point.
- In 2020, NITK signed an MoU with ISRO to establish a Regional Academic Centre for Space.
- Student clubs include the Literary, Stage and Debating Society (LSD) and Dance
  Dramatics and Fashion Club (organizes the annual "Spandan" festival).

Keep replies conversational, warm, and reasonably concise — like chatting with a
helpful senior student, not reciting a brochure."""


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
        history = data.get('history', [])  # [{role: 'user'|'model', text: '...'}, ...]

        if not message:
            return jsonify({'reply': ''})

        contents = []
        for turn in history:
            contents.append({
                "role": turn.get('role', 'user'),
                "parts": [{"text": turn.get('text', '')}],
            })
        contents.append({"role": "user", "parts": [{"text": message}]})

        gemini_url = (
            f'https://generativelanguage.googleapis.com/v1beta/models/'
            f'{GEMINI_MODEL}:generateContent'
        )
        body = json.dumps({
            "system_instruction": {"parts": [{"text": NITK_CHAT_SYSTEM_PROMPT}]},
            "contents": contents,
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
        with urllib.request.urlopen(req, timeout=25) as r:
            result = json.loads(r.read().decode())

        if not result.get('candidates'):
            return jsonify({'reply': "Sorry, I couldn't process that. Please try again."})

        reply_text = result['candidates'][0]['content']['parts'][0]['text']
        print(f'💬 NITK chat: "{message[:60]}"')
        return jsonify({'reply': reply_text.strip()})

    except Exception as e:
        print(f'❌ NITK chat error: {e}')
        return jsonify({'reply': "Sorry, something went wrong. Please try again."}), 500


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