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
ORS_KEY       = os.environ.get('ORS_KEY', '')
TWILIO_SID    = os.environ.get('TWILIO_SID', '')
TWILIO_TOKEN  = os.environ.get('TWILIO_TOKEN', '')
TWILIO_FROM   = os.environ.get('TWILIO_FROM', '')
GMAIL_USER    = os.environ.get('GMAIL_USER', '')
GMAIL_PASS    = os.environ.get('GMAIL_PASS', '')
GMAIL_TO      = os.environ.get('GMAIL_TO', '')

# ── OTP store (in-memory — resets on server restart, fine for free tier) ──
_otp_store = {}

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
    expiry = time.time() + 300   # 5 minutes
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