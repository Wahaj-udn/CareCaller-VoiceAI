#!/usr/bin/env python3
"""
CareCaller Admin Dashboard - Flask Application
A complete Flask app serving the dashboard with file upload functionality
"""

from flask import Flask, render_template, request, jsonify, send_file, redirect
from flask_cors import CORS
import os
import json
import csv
import re
from pathlib import Path
from werkzeug.utils import secure_filename
from datetime import datetime

app = Flask(__name__, 
    template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
    static_folder=os.path.join(os.path.dirname(__file__), 'static'),
    instance_path=os.path.join(os.path.dirname(__file__), 'instance')
)
CORS(app)

# Configuration
BASE_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, os.pardir))
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'json'}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
RESULT_JSON_FALLBACK = os.path.join(PROJECT_ROOT, 'result.json')
OUTPUT_CSV_PRIMARY = os.getenv('OUTPUT_CSV_PATH', os.path.join(PROJECT_ROOT, 'output.csv'))

HEALTH_OUTPUT_COLUMNS = [
    'Overall Feeling',
    'Weight (lbs)',
    'Height (ft/in)',
    'Weight Lost This Month (lbs)',
    'Medication Side Effects',
    'Satisfied with Weight Loss Rate',
    'Goal Weight (lbs)',
    'Dosage Requests',
    'New Medications/Supplements',
    'New Medical Conditions',
    'New Allergies',
    'Recent Surgeries',
    'Questions for Doctor',
    'Shipping Address Changed',
]

PAGE_TEMPLATES = {
    'dashboard': 'dashboard.html',
    'live-calls': 'live-calls.html',
    'csv-data': 'csv-data.html'
}

# Create uploads folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE


def _default_dashboard_context(message='No CSV/JSON file found'):
    """Safe default dashboard context to avoid template undefined errors."""
    return {
        'has_data': False,
        'data_source': message,
        'total_records': 0,
        'active_calls': 0,
        'opted_out_count': 0,
        'opted_out_rate': 0.0,
        'connected_count': 0,
        'connected_rate': 0.0,
        'avg_duration': 'N/A',
        'top_outcome': 'N/A',
        'insights': [],
        'charts': {
            'outcome_distribution': {'labels': [], 'values': []},
            'direction_distribution': {'labels': [], 'values': []},
            'duration_distribution': {'labels': [], 'values': []}
        },
        'rows': []
    }


def _default_transcripts_context(message='No transcript data available'):
    """Safe default transcripts context to avoid template undefined errors."""
    return {
        'has_data': False,
        'data_source': message,
        'charts': {
            'outcome_distribution': {
                'labels': [],
                'values': []
            }
        },
        'rows': []
    }


def _default_csv_data_context(message='No output.csv data found'):
    return {
        'has_data': False,
        'data_source': message,
        'active_leads': 0,
        'valid_records': 0,
        'valid_records_rate': 0.0,
    }


def _default_live_calls_context(message='No live call data available'):
    return {
        'has_data': False,
        'data_source': message,
        'active_calls': 0,
        'status_label': 'No Active Call',
        'started_at': 'n/a',
        'duration_text': 'n/a',
        'messages': [],
        'objective_title': 'Waiting for active call',
        'objective_detail': 'No active conversation found in backend data.',
    }


def _existing_path(*candidates):
    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isfile(candidate):
            return candidate
    return None


def _primary_output_csv_file():
    return _existing_path(
        OUTPUT_CSV_PRIMARY,
        os.path.join(PROJECT_ROOT, 'output.csv'),
        os.path.join(BASE_DIR, 'output.csv'),
    )

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _pick_record_value(record, keys):
    """Case-insensitive field lookup for the first matching key."""
    if not isinstance(record, dict):
        return None

    normalized = {str(k).strip().lower(): v for k, v in record.items()}
    for key in keys:
        value = normalized.get(key.lower())
        if value not in (None, ''):
            return value
    return None


def _extract_name_from_transcript(record):
    """Try to infer the callee name from transcript messages."""
    transcript = record.get('transcript') if isinstance(record, dict) else None
    if not isinstance(transcript, list):
        return None

    for item in transcript[:3]:
        if not isinstance(item, dict):
            continue
        message = str(item.get('message', '')).strip()
        if not message:
            continue

        match = re.search(r'speaking with\s+([^?.!,]+)', message, re.IGNORECASE)
        if match:
            return match.group(1).strip().title()
    return None


def _parse_seconds(value):
    """Parse duration strings/numbers into seconds."""
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip().lower()
    if not text:
        return None

    if text.isdigit():
        return int(text)

    if ':' in text:
        parts = text.split(':')
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            minutes, seconds = map(int, parts)
            return minutes * 60 + seconds
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            hours, minutes, seconds = map(int, parts)
            return hours * 3600 + minutes * 60 + seconds

    match = re.search(r'(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?', text)
    if match and any(match.groups()):
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds

    return None


def _format_duration(seconds):
    """Format seconds as MM:SS or HH:MM:SS."""
    if seconds is None:
        return 'N/A'
    seconds = int(seconds)
    if seconds < 3600:
        return f"{seconds // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _display_or_na(value):
    """Return lowercase n/a for empty values so templates remain clean."""
    if value in (None, ''):
        return 'n/a'
    text = str(value).strip()
    return text if text else 'n/a'


def _parse_datetime_value(value):
    """Parse common datetime formats used in call exports."""
    if value in (None, ''):
        return None

    text = str(value).strip()
    if not text:
        return None

    # Support a trailing Z in ISO values.
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'

    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass

    for fmt in (
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%m/%d/%Y %H:%M:%S',
        '%m/%d/%Y %H:%M',
        '%m/%d/%Y %I:%M %p',
        '%d-%m-%Y %H:%M:%S',
        '%d-%m-%Y %H:%M'
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    return None


def _format_datetime_for_table(record):
    """Return a human readable datetime string or n/a."""
    raw = _pick_record_value(
        record,
        ['date_time', 'datetime', 'timestamp', 'created_at', 'call_time', 'date', 'time']
    )
    parsed = _parse_datetime_value(raw)
    if parsed is None:
        return 'n/a'
    return parsed.strftime('%b %d, %Y %I:%M %p')


def _build_transcript_glimpse(record, max_length=160):
    """Return a short transcript preview instead of the full transcript text."""
    text = ''

    if isinstance(record, dict):
        transcript_text = record.get('transcript_text')
        if isinstance(transcript_text, str):
            text = transcript_text

    if not text and isinstance(record, dict):
        transcript_value = record.get('transcript')
        if isinstance(transcript_value, str):
            text = transcript_value

    if not text and isinstance(record, dict):
        transcript_items = record.get('transcript')
        if isinstance(transcript_items, list):
            messages = []
            for item in transcript_items:
                if not isinstance(item, dict):
                    continue
                msg = str(item.get('message', '')).strip()
                if msg:
                    messages.append(msg)
                if len(messages) >= 4:
                    break
            text = ' '.join(messages)

    text = re.sub(r'\s+', ' ', str(text)).strip()
    if not text:
        return 'n/a'

    if len(text) <= max_length:
        return text

    trimmed = text[:max_length].rsplit(' ', 1)[0].strip()
    if not trimmed:
        trimmed = text[:max_length].strip()
    return f"{trimmed}..."


def _build_transcript_rows(records):
    """Map transcript records for the transcripts table."""
    rows = []
    for record in records:
        if not isinstance(record, dict):
            continue

        outcome = _pick_record_value(record, ['outcome', 'call_status', 'status', 'result'])

        call_duration = _pick_record_value(
            record,
            ['call_duration', 'call duration', 'duration', 'duration_seconds', 'talk_time']
        )
        call_duration_text = _display_or_na(call_duration)

        direction = _pick_record_value(
            record,
            ['direction', 'call_direction', 'direction_type']
        )

        outcome_text = _display_or_na(outcome)
        direction_text = _display_or_na(direction)
        transcript_full = _build_transcript_glimpse(record, max_length=10000)
        transcript_short = _build_transcript_glimpse(record, max_length=160)
        has_more = (
            transcript_full != 'n/a'
            and transcript_short != 'n/a'
            and len(transcript_full) > len(transcript_short)
        )

        rows.append({
            'outcome': outcome_text.lower() if outcome_text != 'n/a' else 'n/a',
            'call_duration': call_duration_text,
            'direction': direction_text.lower() if direction_text != 'n/a' else 'n/a',
            'transcript': transcript_short,
            'transcript_short': transcript_short,
            'transcript_full': transcript_full,
            'has_more': has_more,
        })

    return rows


def get_transcripts_context():
    """Build transcripts rows from output.csv only."""
    source_path = _primary_output_csv_file()

    if source_path is None:
        return _default_transcripts_context('No output.csv file found')

    try:
        records = _load_records_from_file(source_path)
    except Exception as exc:
        return _default_transcripts_context(f'Error reading {os.path.basename(source_path)}: {exc}')

    rows = _build_transcript_rows(records)

    outcome_counts = {}
    for row in rows:
        label = row.get('outcome', 'n/a')
        if label == 'n/a':
            continue
        outcome_counts[label] = outcome_counts.get(label, 0) + 1

    top_outcomes = sorted(outcome_counts.items(), key=lambda item: item[1], reverse=True)[:8]

    if not rows:
        return {
            'has_data': False,
            'data_source': os.path.basename(source_path),
            'charts': {
                'outcome_distribution': {
                    'labels': [],
                    'values': []
                }
            },
            'rows': []
        }

    latest_five = list(reversed(rows[-5:]))
    return {
        'has_data': True,
        'data_source': os.path.basename(source_path),
        'charts': {
            'outcome_distribution': {
                'labels': [item[0] for item in top_outcomes],
                'values': [item[1] for item in top_outcomes]
            }
        },
        'rows': latest_five
    }


@app.route('/api/transcripts-data', methods=['GET'])
def transcripts_data_api():
    """Return transcript section data sourced from output.csv."""
    data = get_transcripts_context()
    source_path = _primary_output_csv_file()
    file_mtime = None
    if source_path:
        try:
            file_mtime = datetime.fromtimestamp(os.path.getmtime(source_path)).isoformat()
        except OSError:
            file_mtime = None

    return jsonify({
        'success': True,
        'data': data,
        'file_mtime': file_mtime,
    }), 200


def _status_category(status):
    text = str(status or '').strip().lower().replace('-', '_').replace(' ', '_')
    if text in {'opted_out', 'opt_out', 'optout', 'do_not_call', 'dnc', 'unsubscribe'}:
        return 'opted_out'
    if text in {'ongoing', 'active', 'in_progress', 'inprogress', 'live'}:
        return 'ongoing'
    if text in {'completed', 'success', 'successful', 'done'}:
        return 'completed'
    if text in {'failed', 'error', 'cancelled', 'canceled', 'terminated'}:
        return 'failed'
    if text in {'voicemail', 'no_answer', 'busy'}:
        return 'voicemail'
    return 'other'


def _normalize_direction(value):
    text = str(value or '').strip().lower()
    if text == 'inbound':
        return 'Inbound'
    if text == 'outbound':
        return 'Outbound'
    return 'Unknown'


def _normalize_json_records(payload):
    """Normalize common JSON payload shapes into a list of records."""
    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return []

    for key in ('transcripts', 'records', 'data', 'results', 'rows', 'items'):
        if isinstance(payload.get(key), list):
            return payload[key]

    # Fallback: first list-valued field
    for value in payload.values():
        if isinstance(value, list):
            return value

    return [payload]


def _latest_csv_json_file():
    """Get the newest uploaded CSV/JSON file."""
    candidates = []
    for filename in os.listdir(UPLOAD_FOLDER):
        if filename == 'upload_log.json':
            continue
        ext = os.path.splitext(filename)[1].lower()
        if ext not in {'.csv', '.json'}:
            continue
        path = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.isfile(path):
            candidates.append(path)

    if not candidates:
        return None

    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates[0]


def _latest_json_file():
    candidates = []
    for filename in os.listdir(UPLOAD_FOLDER):
        if filename == 'upload_log.json':
            continue
        path = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.isfile(path) and filename.lower().endswith('.json'):
            candidates.append(path)
    if not candidates:
        return None
    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates[0]


def _load_records_from_file(path):
    """Load records from a CSV or JSON file."""
    ext = os.path.splitext(path)[1].lower()

    if ext == '.json':
        with open(path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        return _normalize_json_records(payload)

    if ext == '.csv':
        with open(path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            return list(reader)

    return []


def _build_dashboard_rows(records):
    rows = []
    for idx, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            continue

        name = _pick_record_value(
            record,
            ['name', 'user_name', 'full_name', 'recipient_name', 'customer_name', 'patient name']
        )
        if not name:
            name = _extract_name_from_transcript(record)
        if not name:
            name = f'Record {idx}'

        phone = _pick_record_value(record, ['phone', 'phone_number', 'contact', 'mobile', 'phone number']) or '--'

        status = _pick_record_value(record, ['call_status', 'status', 'outcome', 'state'])
        if not status:
            answered_count = 0
            for col in HEALTH_OUTPUT_COLUMNS:
                value = _pick_record_value(record, [col])
                if value not in (None, ''):
                    answered_count += 1
            if answered_count == 0:
                status = 'pending'
            elif answered_count >= len(HEALTH_OUTPUT_COLUMNS):
                status = 'completed'
            else:
                status = 'in_progress'

        status_text = str(status).replace('_', ' ').title()
        status_kind = _status_category(status)

        direction = _normalize_direction(
            _pick_record_value(record, ['direction', 'call_direction', 'direction_type'])
        )

        duration_seconds = _parse_seconds(
            _pick_record_value(record, ['call_duration', 'call duration', 'duration', 'duration_seconds', 'talk_time'])
        )

        rows.append({
            'name': str(name),
            'phone': str(phone),
            'status_text': status_text,
            'status_kind': status_kind,
            'direction': direction,
            'duration_seconds': duration_seconds,
            'duration_text': _format_duration(duration_seconds),
            'action_text': 'View Live' if status_kind == 'ongoing' else 'View Transcript'
        })

    return rows


def get_dashboard_context():
    """Build dashboard stats and table rows from uploaded data sources."""
    source_path = _primary_output_csv_file() or _latest_csv_json_file()
    if source_path is None:
        source_path = _existing_path(RESULT_JSON_FALLBACK)

    if source_path is None:
        return _default_dashboard_context('No CSV/JSON file found')

    try:
        records = _load_records_from_file(source_path)
    except Exception as exc:
        return _default_dashboard_context(f'Error reading {os.path.basename(source_path)}: {exc}')

    rows = _build_dashboard_rows(records)
    total_records = len(rows)
    active_calls = sum(1 for row in rows if row['status_kind'] == 'ongoing')
    opted_out_count = sum(1 for row in rows if row['status_kind'] == 'opted_out')

    durations = [row['duration_seconds'] for row in rows if row['duration_seconds'] is not None]
    avg_duration = _format_duration(int(sum(durations) / len(durations))) if durations else 'N/A'

    outcome_counts = {}
    for row in rows:
        label = row['status_text']
        outcome_counts[label] = outcome_counts.get(label, 0) + 1
    top_outcome = max(outcome_counts, key=outcome_counts.get) if outcome_counts else 'N/A'

    direction_counts = {'Inbound': 0, 'Outbound': 0, 'Unknown': 0}
    for row in rows:
        direction_counts[row['direction']] = direction_counts.get(row['direction'], 0) + 1

    duration_buckets = {
        '0-30 sec': 0,
        '31-60 sec': 0,
        '1-2 min': 0,
        '2+ min': 0
    }
    for row in rows:
        seconds = row['duration_seconds']
        if seconds is None:
            continue
        if seconds <= 30:
            duration_buckets['0-30 sec'] += 1
        elif seconds <= 60:
            duration_buckets['31-60 sec'] += 1
        elif seconds <= 120:
            duration_buckets['1-2 min'] += 1
        else:
            duration_buckets['2+ min'] += 1

    top_outcomes = sorted(outcome_counts.items(), key=lambda item: item[1], reverse=True)[:5]

    voicemail_count = sum(1 for row in rows if row['status_kind'] == 'voicemail')
    connected_count = max(total_records - voicemail_count, 0)

    opted_out_rate = round((opted_out_count / total_records) * 100, 1) if total_records else 0.0
    connected_rate = round((connected_count / total_records) * 100, 1) if total_records else 0.0

    insights = [
        f"{total_records} calls analyzed from {os.path.basename(source_path)}.",
        f"{opted_out_count} opted out ({opted_out_rate}%).",
        f"{connected_count} connected calls ({connected_rate}%) vs {voicemail_count} voicemail/no-answer."
    ]

    if top_outcome != 'N/A':
        insights.append(f"Most common outcome: {top_outcome}.")

    return {
        'has_data': total_records > 0,
        'data_source': os.path.basename(source_path),
        'total_records': total_records,
        'active_calls': active_calls,
        'opted_out_count': opted_out_count,
        'opted_out_rate': opted_out_rate,
        'connected_count': connected_count,
        'connected_rate': connected_rate,
        'avg_duration': avg_duration,
        'top_outcome': top_outcome,
        'insights': insights,
        'charts': {
            'outcome_distribution': {
                'labels': [item[0] for item in top_outcomes],
                'values': [item[1] for item in top_outcomes]
            },
            'direction_distribution': {
                'labels': list(direction_counts.keys()),
                'values': list(direction_counts.values())
            },
            'duration_distribution': {
                'labels': list(duration_buckets.keys()),
                'values': list(duration_buckets.values())
            }
        },
        'rows': rows[:25]
    }


def get_csv_data_context():
    source_path = _primary_output_csv_file()
    if source_path is None:
        return _default_csv_data_context('No output.csv file found')

    try:
        rows = _load_records_from_file(source_path)
    except Exception as exc:
        return _default_csv_data_context(f'Error reading {os.path.basename(source_path)}: {exc}')

    total = len(rows)
    if total == 0:
        return {
            'has_data': False,
            'data_source': os.path.basename(source_path),
            'active_leads': 0,
            'valid_records': 0,
            'valid_records_rate': 0.0,
        }

    valid_records = 0
    for row in rows:
        answered = 0
        for col in HEALTH_OUTPUT_COLUMNS:
            value = _pick_record_value(row, [col])
            if value not in (None, ''):
                answered += 1
        if answered > 0:
            valid_records += 1

    valid_rate = round((valid_records / total) * 100, 1) if total else 0.0

    return {
        'has_data': True,
        'data_source': os.path.basename(source_path),
        'active_leads': total,
        'valid_records': valid_records,
        'valid_records_rate': valid_rate,
    }


def get_live_calls_context():
    source_path = _latest_json_file() or _existing_path(RESULT_JSON_FALLBACK)
    if source_path is None:
        return _default_live_calls_context('No JSON call data found')

    try:
        records = _load_records_from_file(source_path)
    except Exception as exc:
        return _default_live_calls_context(f'Error reading {os.path.basename(source_path)}: {exc}')

    active = []
    for record in records:
        outcome = _pick_record_value(record, ['outcome', 'call_status', 'status', 'result'])
        if _status_category(outcome) == 'ongoing':
            active.append(record)

    current = active[-1] if active else (records[-1] if records else None)
    if not isinstance(current, dict):
        return _default_live_calls_context(f'No call entries in {os.path.basename(source_path)}')

    transcript_items = current.get('transcript') if isinstance(current.get('transcript'), list) else []
    messages = []
    for idx, item in enumerate(transcript_items[-8:], start=1):
        if not isinstance(item, dict):
            continue
        role = str(item.get('role', '')).strip().lower() or 'agent'
        role_label = 'Agent' if role == 'agent' else 'User'
        role_kind = 'agent' if role == 'agent' else 'user'
        text = str(item.get('message', '')).strip()
        if not text:
            continue
        messages.append({
            'role_label': role_label,
            'role_kind': role_kind,
            'message': text,
            'time_label': f'#{idx}',
        })

    duration_seconds = _parse_seconds(
        _pick_record_value(current, ['call_duration', 'duration', 'duration_seconds', 'talk_time'])
    )

    status = _pick_record_value(current, ['outcome', 'call_status', 'status', 'result']) or 'unknown'
    status_kind = _status_category(status)

    return {
        'has_data': True,
        'data_source': os.path.basename(source_path),
        'active_calls': len(active),
        'status_label': 'Ongoing' if status_kind == 'ongoing' else str(status).replace('_', ' ').title(),
        'started_at': _format_datetime_for_table(current),
        'duration_text': _format_duration(duration_seconds),
        'messages': messages,
        'objective_title': 'Live call monitoring',
        'objective_detail': f"Source: {os.path.basename(source_path)}",
    }

def log_upload(log_data):
    """Log upload activity"""
    try:
        log_file = os.path.join(UPLOAD_FOLDER, 'upload_log.json')
        
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                logs = json.load(f)
        else:
            logs = []
        
        logs.insert(0, log_data)
        logs = logs[:1000]
        
        with open(log_file, 'w') as f:
            json.dump(logs, f, indent=2)
    except Exception as e:
        print(f'Error logging upload: {str(e)}')

# ==================== PAGE ROUTES ====================

def render_dashboard_page(page_name):
    """Render a page template using base.html inheritance."""
    context = {
        'current_page': page_name,
        'dashboard_data': _default_dashboard_context(),
        'transcripts_data': _default_transcripts_context(),
        'csv_data_data': _default_csv_data_context(),
        'live_calls_data': _default_live_calls_context(),
    }
    if page_name == 'dashboard':
        try:
            context['dashboard_data'] = get_dashboard_context()
        except Exception as exc:
            context['dashboard_data'] = _default_dashboard_context(f'Dashboard analytics error: {exc}')
        try:
            context['transcripts_data'] = get_transcripts_context()
        except Exception as exc:
            context['transcripts_data'] = _default_transcripts_context(f'Transcript analytics error: {exc}')
    if page_name == 'csv-data':
        try:
            context['csv_data_data'] = get_csv_data_context()
        except Exception as exc:
            context['csv_data_data'] = _default_csv_data_context(f'CSV data error: {exc}')
    if page_name == 'live-calls':
        try:
            context['live_calls_data'] = get_live_calls_context()
        except Exception as exc:
            context['live_calls_data'] = _default_live_calls_context(f'Live calls error: {exc}')
    return render_template(PAGE_TEMPLATES[page_name], **context)


@app.route('/')
def home_page():
    """Serve dashboard home"""
    return render_dashboard_page('dashboard')


@app.route('/dashboard')
def dashboard_page():
    """Serve dashboard page"""
    return render_dashboard_page('dashboard')


@app.route('/live-calls')
def live_calls_page():
    """Serve live calls page"""
    return render_dashboard_page('live-calls')


@app.route('/csv-data')
def csv_data_page():
    """Serve CSV data page"""
    return render_dashboard_page('csv-data')


@app.route('/transcripts')
def transcripts_page():
    """Legacy transcripts route now redirects to dashboard."""
    return redirect('/dashboard')

# ==================== API ROUTES ====================

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Handle file uploads"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'error': 'File type not allowed. Use: csv, xlsx, or json'}), 400
        
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        
        if file_size > MAX_FILE_SIZE:
            return jsonify({'error': 'File size exceeds 50MB limit'}), 400
        
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
        unique_filename = timestamp + filename
        filepath = os.path.join(UPLOAD_FOLDER, unique_filename)
        
        file.save(filepath)
        
        log_upload({
            'filename': unique_filename,
            'original_filename': filename,
            'file_size': file_size,
            'upload_time': datetime.now().isoformat(),
            'status': 'success'
        })
        
        return jsonify({
            'success': True,
            'message': 'File uploaded successfully',
            'fileName': filename,
            'filePath': filepath,
            'uniqueFileName': unique_filename,
            'fileSize': file_size,
            'uploadTime': datetime.now().isoformat()
        }), 200
    
    except Exception as e:
        print(f'Upload error: {str(e)}')
        return jsonify({'error': f'Upload failed: {str(e)}'}), 500

@app.route('/api/uploads', methods=['GET'])
def list_uploads():
    """List all uploaded files"""
    try:
        files = []
        for filename in sorted(os.listdir(UPLOAD_FOLDER), reverse=True):
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.isfile(filepath) and filename != 'upload_log.json':
                stat = os.stat(filepath)
                files.append({
                    'filename': filename,
                    'size': stat.st_size,
                    'modified': datetime.fromtimestamp(stat.st_mtime).isoformat()
                })
        
        return jsonify({
            'success': True,
            'count': len(files),
            'files': files
        }), 200
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/uploads/<filename>', methods=['GET'])
def download_file(filename):
    """Download an uploaded file"""
    try:
        filepath = os.path.join(UPLOAD_FOLDER, secure_filename(filename))
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        
        return send_file(filepath, as_attachment=True)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/uploads/<filename>', methods=['DELETE'])
def delete_file(filename):
    """Delete an uploaded file"""
    try:
        filepath = os.path.join(UPLOAD_FOLDER, secure_filename(filename))
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found'}), 404
        
        os.remove(filepath)
        
        log_upload({
            'filename': filename,
            'action': 'delete',
            'delete_time': datetime.now().isoformat(),
            'status': 'deleted'
        })
        
        return jsonify({
            'success': True,
            'message': 'File deleted successfully'
        }), 200
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    uploads_count = len([f for f in os.listdir(UPLOAD_FOLDER) 
                        if os.path.isfile(os.path.join(UPLOAD_FOLDER, f)) and f != 'upload_log.json'])
    
    return jsonify({
        'status': 'ok',
        'app': 'CareCaller Admin Dashboard',
        'upload_folder': UPLOAD_FOLDER,
        'uploads_count': uploads_count,
        'timestamp': datetime.now().isoformat()
    }), 200

# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors with route-appropriate responses"""
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found'}), 404
    return render_dashboard_page('dashboard'), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    return jsonify({'error': 'Internal server error'}), 500

# ==================== CONTEXT PROCESSORS ====================

@app.context_processor
def inject_config():
    """Inject config into templates"""
    return {
        'app_name': 'CareCaller Admin',
        'version': '1.0.0',
        'dashboard_data': _default_dashboard_context(),
        'transcripts_data': _default_transcripts_context()
    }

if __name__ == '__main__':
    host = os.getenv('DASHBOARD_HOST', 'localhost')
    port = int(os.getenv('DASHBOARD_PORT', '8000'))

    print("=" * 60)
    print("CareCaller Admin Dashboard - Flask Application")
    print("=" * 60)
    print(f'\nTemplates folder: {TEMPLATES_DIR}')
    print(f'Upload folder: {UPLOAD_FOLDER}')
    print(f'\nServer starting on http://{host}:{port}')
    print('\nRoutes:')
    print('  GET  /                 - Dashboard home')
    print('  GET  /dashboard        - Dashboard')
    print('  GET  /live-calls       - Live calls')
    print('  GET  /csv-data         - CSV data')
    print('  GET  /transcripts      - Redirect to Dashboard')
    print('\nAPI Endpoints:')
    print('  POST   /api/upload              - Upload a file')
    print('  GET    /api/uploads             - List all uploads')
    print('  GET    /api/uploads/<filename>  - Download file')
    print('  DELETE /api/uploads/<filename>  - Delete file')
    print('  GET    /api/health              - Health check')
    print("\n" + "=" * 60)
    
    app.run(debug=True, host=host, port=port)
