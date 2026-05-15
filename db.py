import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
try:
    from supabase import Client, create_client
except ImportError:
    Client = Any
    create_client = None

logger = logging.getLogger('db')
_SUPABASE_CLIENT: Client | None = None
_SUPABASE_CLIENT_KEY: tuple[str, str] | None = None
_ANALYTICS_COLUMNS = {'sentiment', 'was_booked', 'interrupt_count', 'estimated_cost_usd', 'call_date', 'call_hour', 'call_day_of_week'}
_BASE_COLUMNS = {'phone_number', 'duration_seconds', 'transcript', 'summary', 'recording_url', 'caller_name'}
_APPOINTMENT_COLUMNS = 'id, created_at, updated_at, title, contact_name, contact_phone, scheduled_start, scheduled_end, timezone, status, notes, source'
_APPOINTMENT_STATUSES = {'scheduled', 'cancelled', 'completed'}
_APPOINTMENT_SOURCES = {'voice_agent', 'manual_ui'}
_IST = timezone(timedelta(hours=5, minutes=30))
_MAX_RETRIES = 3
_RETRY_DELAYS = [1.0, 2.0, 4.0]

class AppointmentError(Exception):
    """Base error for appointments data operations."""

class AppointmentConflictError(AppointmentError):
    """Raised when an appointment overlaps an active appointment."""

class AppointmentNotFoundError(AppointmentError):
    """Raised when an appointment row does not exist."""

class AppointmentValidationError(AppointmentError):
    """Raised when appointment input is invalid."""

def _is_retryable(err_str: str) -> bool:
    """True if the error is a transient network or SSL failure worth retrying."""
    transient = ('525', 'ssl', 'timeout', 'connection', 'network', '502', '503', '504')
    el = err_str.lower()
    return any((k in el for k in transient))

def _is_schema_error(err_str: str) -> bool:
    """True if Supabase returned PGRST204 — column not found in schema cache."""
    return 'PGRST204' in err_str or 'schema cache' in err_str.lower()

def _extract_missing_column(err_str: str) -> str | None:
    match = re.search("Could not find the '([^']+)' column", err_str, re.IGNORECASE)
    if match:
        return match.group(1)
    return None

def _missing_appointments_table_message() -> str:
    return 'Appointments table is missing. Run sql/supabase/setup.sql in Supabase.'

def _parse_iso_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        clean = value.strip()
        if not clean:
            raise AppointmentValidationError('Appointment datetime is required.')
        if clean.endswith('Z'):
            clean = clean[:-1] + '+00:00'
        try:
            dt = datetime.fromisoformat(clean)
        except ValueError as exc:
            raise AppointmentValidationError(f'Invalid appointment datetime: {value}') from exc
    else:
        raise AppointmentValidationError('Appointment datetime must be a string or datetime.')
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_IST)
    return dt

def _normalize_appointment_error(exc: Exception) -> AppointmentError:
    err = str(exc)
    if _is_schema_error(err):
        return AppointmentValidationError(_missing_appointments_table_message())
    if 'appointments_no_overlap' in err or '23P01' in err or 'overlap' in err.lower():
        return AppointmentConflictError('That time overlaps an existing scheduled appointment.')
    if 'appointments_valid_status' in err:
        return AppointmentValidationError('Appointment status is invalid.')
    if 'appointments_valid_source' in err:
        return AppointmentValidationError('Appointment source is invalid.')
    if 'appointments_valid_window' in err or 'scheduled_end' in err:
        return AppointmentValidationError('Appointment end time must be after start time.')
    return AppointmentError(err)

def _normalize_appointment_payload(payload: dict[str, Any], *, current: dict[str, Any] | None=None) -> dict[str, Any]:
    title = (payload.get('title') or '').strip() or (current or {}).get('title') or 'Site Visit'
    contact_name = (payload.get('contact_name') or (current or {}).get('contact_name') or '').strip()
    contact_phone = (payload.get('contact_phone') or (current or {}).get('contact_phone') or '').strip()
    notes = payload.get('notes')
    if notes is None:
        notes = (current or {}).get('notes') or ''
    notes = str(notes).strip()
    timezone_name = (payload.get('timezone') or '').strip() or (current or {}).get('timezone') or 'Asia/Kolkata'
    status = (payload.get('status') or (current or {}).get('status') or 'scheduled').strip().lower()
    source = (payload.get('source') or (current or {}).get('source') or 'manual_ui').strip().lower()
    if status not in _APPOINTMENT_STATUSES:
        raise AppointmentValidationError(f'Unsupported appointment status: {status}')
    if source not in _APPOINTMENT_SOURCES:
        raise AppointmentValidationError(f'Unsupported appointment source: {source}')
    start_value = payload.get('scheduled_start', (current or {}).get('scheduled_start'))
    if not start_value:
        raise AppointmentValidationError('scheduled_start is required.')
    start_dt = _parse_iso_datetime(start_value)
    end_value = payload.get('scheduled_end')
    if end_value:
        end_dt = _parse_iso_datetime(end_value)
    elif current and 'scheduled_start' in payload and ('scheduled_end' not in payload):
        current_start = _parse_iso_datetime(current['scheduled_start'])
        current_end = _parse_iso_datetime(current['scheduled_end'])
        end_dt = start_dt + (current_end - current_start)
    else:
        fallback_end = (current or {}).get('scheduled_end')
        end_dt = _parse_iso_datetime(fallback_end) if fallback_end else start_dt + timedelta(minutes=30)
    if end_dt <= start_dt:
        raise AppointmentValidationError('scheduled_end must be after scheduled_start.')
    return {'title': title, 'contact_name': contact_name, 'contact_phone': contact_phone, 'scheduled_start': start_dt.isoformat(), 'scheduled_end': end_dt.isoformat(), 'timezone': timezone_name, 'status': status, 'notes': notes, 'source': source}

def get_supabase() -> Client | None:
    if create_client is None:
        return None
    url = os.environ.get('SUPABASE_URL', '')
    key = os.environ.get('SUPABASE_KEY', '')
    if not url or not key:
        return None
    global _SUPABASE_CLIENT, _SUPABASE_CLIENT_KEY
    client_key = (url, key)
    if _SUPABASE_CLIENT is not None and _SUPABASE_CLIENT_KEY == client_key:
        return _SUPABASE_CLIENT
    try:
        _SUPABASE_CLIENT = create_client(url, key)
        _SUPABASE_CLIENT_KEY = client_key
        return _SUPABASE_CLIENT
    except Exception as e:
        logger.error(f'Failed to init Supabase client: {e}')
        return None

def save_call_log(phone: str, duration: int, transcript: str, summary: str='', recording_url: str='', caller_name: str='', sentiment: str='unknown', estimated_cost_usd: float | None=None, call_date: str | None=None, call_hour: int | None=None, call_day_of_week: str | None=None, was_booked: bool=False, interrupt_count: int=0, call_room_id: str='') -> dict:
    """
    Insert a call log into Supabase.

    Strategy:
    1. Try with all columns (including analytics columns from migration_v2).
    2. If PGRST204 (column not in schema cache — migration not yet run),
       retry with only the base columns so the call is never silently lost.
    3. Retry up to 3× on transient SSL/network errors with exponential backoff.
    """
    url = os.environ.get('SUPABASE_URL', '')
    key = os.environ.get('SUPABASE_KEY', '')
    if not url or not key:
        logger.info(f'Supabase not configured. Local log -> {phone} {duration}s')
        return {'success': False, 'message': 'Supabase not configured'}
    supabase = get_supabase()
    if not supabase:
        return {'success': False, 'message': 'Supabase client failed'}
    full_data: dict[str, Any] = {'phone_number': phone, 'duration_seconds': duration, 'transcript': transcript, 'summary': summary, 'sentiment': sentiment, 'was_booked': was_booked, 'interrupt_count': interrupt_count}
    if recording_url:
        full_data['recording_url'] = recording_url
    if caller_name:
        full_data['caller_name'] = caller_name
    if estimated_cost_usd is not None:
        full_data['estimated_cost_usd'] = estimated_cost_usd
    if call_date:
        full_data['call_date'] = call_date
    if call_hour is not None:
        full_data['call_hour'] = call_hour
    if call_day_of_week:
        full_data['call_day_of_week'] = call_day_of_week
    if call_room_id:
        full_data['call_room_id'] = call_room_id
    base_data: dict[str, Any] = {k: v for k, v in full_data.items() if k not in _ANALYTICS_COLUMNS}

    def _try_insert(data: dict[str, Any], label: str) -> dict:
        payload = dict(data)
        transient_attempt = 0
        stripped_columns: list[str] = []
        while payload:
            try:
                res = supabase.table('call_logs').insert(payload).execute()
                if stripped_columns:
                    logger.warning(f"Saved call log for {phone} ({label}) after dropping unsupported columns: {', '.join(stripped_columns)}")
                else:
                    logger.info(f'Saved call log for {phone} ({label})')
                return {'success': True, 'data': res.data, 'dropped_columns': stripped_columns}
            except Exception as e:
                err = str(e)
                if _is_schema_error(err):
                    missing_col = _extract_missing_column(err)
                    if missing_col and missing_col in payload:
                        payload.pop(missing_col, None)
                        stripped_columns.append(missing_col)
                        logger.warning(f"Column '{missing_col}' is missing on call_logs. Retrying {label} insert without it.")
                        continue
                    logger.error(f'Failed to save call log ({label}) due to schema mismatch: {e}')
                    return {'success': False, 'message': err, 'dropped_columns': stripped_columns}
                if _is_retryable(err) and transient_attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(transient_attempt, len(_RETRY_DELAYS) - 1)]
                    transient_attempt += 1
                    logger.warning(f'Transient error (attempt {transient_attempt}), retrying in {delay}s: {err[:80]}')
                    time.sleep(delay)
                    continue
                logger.error(f'Failed to save call log ({label}): {e}')
                return {'success': False, 'message': err, 'dropped_columns': stripped_columns}
        logger.error(f'Failed to save call log ({label}): no compatible columns left to insert.')
        return {'success': False, 'message': 'No compatible columns left to insert'}
    result = _try_insert(full_data, 'full')
    if result.get('success'):
        return result
    if _is_schema_error(str(result.get('message', ''))):
        logger.warning('Analytics columns missing (run sql/supabase/setup.sql and migration_v2.sql). Falling back to base columns for this call log.')
        return _try_insert(base_data, 'base-fallback')
    return result

def fetch_call_logs(limit: int=50, *, phone_number: str | None=None, call_room_id: str | None=None) -> list:
    supabase = get_supabase()
    if not supabase:
        return []
    for attempt in range(_MAX_RETRIES):
        try:
            query = supabase.table('call_logs').select('*').order('created_at', desc=True)
            if phone_number:
                query = query.eq('phone_number', normalize_phone_number(phone_number))
            if call_room_id:
                query = query.eq('call_room_id', str(call_room_id))
            if limit:
                query = query.limit(limit)
            res = query.execute()
            return res.data or []
        except Exception as e:
            if _is_retryable(str(e)) and attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAYS[attempt])
                continue
            logger.error(f'Failed to fetch call logs: {e}')
            return []
    return []

def fetch_appointments(*, start_iso: str | None=None, end_iso: str | None=None, statuses: list[str] | None=None, limit: int=500) -> list:
    supabase = get_supabase()
    if not supabase:
        return []
    try:
        query = supabase.table('appointments').select(_APPOINTMENT_COLUMNS).order('scheduled_start')
        if start_iso:
            query = query.gt('scheduled_end', start_iso)
        if end_iso:
            query = query.lt('scheduled_start', end_iso)
        if statuses:
            cleaned = [status.strip().lower() for status in statuses if status.strip()]
            if cleaned:
                if len(cleaned) == 1:
                    query = query.eq('status', cleaned[0])
                else:
                    query = query.in_('status', cleaned)
        if limit:
            query = query.limit(limit)
        res = query.execute()
        return res.data or []
    except Exception as exc:
        normalized = _normalize_appointment_error(exc)
        logger.error(f'Failed to fetch appointments: {normalized}')
        raise normalized

def get_appointment(appointment_id: str | int) -> dict:
    supabase = get_supabase()
    if not supabase:
        raise AppointmentValidationError('Supabase not configured.')
    try:
        res = supabase.table('appointments').select(_APPOINTMENT_COLUMNS).eq('id', str(appointment_id)).single().execute()
        if not res.data:
            raise AppointmentNotFoundError(f'Appointment {appointment_id} not found.')
        return res.data
    except AppointmentNotFoundError:
        raise
    except Exception as exc:
        normalized = _normalize_appointment_error(exc)
        logger.error(f'Failed to fetch appointment {appointment_id}: {normalized}')
        raise normalized

def create_appointment(payload: dict[str, Any]) -> dict:
    supabase = get_supabase()
    if not supabase:
        raise AppointmentValidationError('Supabase not configured.')
    data = _normalize_appointment_payload(payload)
    try:
        res = supabase.table('appointments').insert(data).execute()
        if not res.data:
            raise AppointmentError('Appointment insert returned no data.')
        return res.data[0]
    except Exception as exc:
        normalized = _normalize_appointment_error(exc)
        logger.error(f'Failed to create appointment: {normalized}')
        raise normalized

def update_appointment(appointment_id: str | int, payload: dict[str, Any]) -> dict:
    supabase = get_supabase()
    if not supabase:
        raise AppointmentValidationError('Supabase not configured.')
    current = get_appointment(appointment_id)
    data = _normalize_appointment_payload(payload, current=current)
    try:
        res = supabase.table('appointments').update(data).eq('id', str(appointment_id)).execute()
        if not res.data:
            raise AppointmentNotFoundError(f'Appointment {appointment_id} not found.')
        return res.data[0]
    except AppointmentNotFoundError:
        raise
    except Exception as exc:
        normalized = _normalize_appointment_error(exc)
        logger.error(f'Failed to update appointment {appointment_id}: {normalized}')
        raise normalized

def cancel_appointment(appointment_id: str | int, reason: str='') -> dict:
    current = get_appointment(appointment_id)
    notes = (current.get('notes') or '').strip()
    reason = reason.strip()
    if reason:
        notes = f'{notes}\n\nCancellation reason: {reason}'.strip()
    return update_appointment(appointment_id, {'status': 'cancelled', 'notes': notes})

def fetch_stats() -> dict:
    _empty = {'total_calls': 0, 'total_bookings': 0, 'avg_duration': 0, 'booking_rate': 0}
    supabase = get_supabase()
    if not supabase:
        return _empty
    try:
        rows = supabase.table('call_logs').select('duration_seconds, summary, was_booked').execute().data or []
        total = len(rows)
        bookings = sum((1 for row in rows if row.get('was_booked') or 'confirmed' in (row.get('summary') or '').lower()))
        durations = [row['duration_seconds'] for row in rows if row.get('duration_seconds')]
        avg_dur = round(sum(durations) / len(durations)) if durations else 0
        rate = round(bookings / total * 100) if total else 0
        return {'total_calls': total, 'total_bookings': bookings, 'avg_duration': avg_dur, 'booking_rate': rate}
    except Exception as e:
        if _is_schema_error(str(e)):
            try:
                rows = supabase.table('call_logs').select('duration_seconds, summary').execute().data or []
                total = len(rows)
                bookings = sum((1 for row in rows if 'confirmed' in (row.get('summary') or '').lower()))
                durations = [row['duration_seconds'] for row in rows if row.get('duration_seconds')]
                avg_dur = round(sum(durations) / len(durations)) if durations else 0
                rate = round(bookings / total * 100) if total else 0
                return {'total_calls': total, 'total_bookings': bookings, 'avg_duration': avg_dur, 'booking_rate': rate}
            except Exception as fallback_exc:
                logger.error(f'Failed to fetch stats (fallback): {fallback_exc}')
                return _empty
        logger.error(f'Failed to fetch stats: {e}')
        return _empty
WA_MESSAGE_TYPES = {'text', 'image', 'document', 'audio'}
AUTOMATION_CHANNELS = {'call', 'whatsapp'}
AUTOMATION_JOB_STATUSES = {'pending', 'retry', 'processing', 'sent', 'failed', 'cancelled', 'pending_review', 'launched'}

def normalize_phone_number(phone_number: str) -> str:
    raw = str(phone_number or '').strip()
    if ':' in raw:
        raw = raw.split(':', 1)[1].strip()
    if not raw:
        return ''
    if raw.startswith('+'):
        return raw
    digits = re.sub('\\D', '', raw)
    if not digits:
        return ''
    if len(digits) == 12 and digits.startswith('91') and (digits[2] in '6789'):
        return f'+{digits}'
    if len(digits) == 10 and digits[0] in '6789':
        return f'+91{digits}'
    return f'+{digits}'

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _safe_preview(text: str, limit: int=160) -> str:
    clean = re.sub('\\s+', ' ', str(text or '').strip())
    return clean[:limit]

def _safe_timestamp(value: Any) -> float:
    raw = str(value or '').strip()
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00')).timestamp()
    except Exception:
        return 0.0

def _schema_tolerant_insert(table_name: str, payload: dict[str, Any], *, label: str) -> dict | None:
    supabase = get_supabase()
    if not supabase:
        return None
    data = dict(payload)
    dropped: list[str] = []
    while data:
        try:
            res = supabase.table(table_name).insert(data).execute()
            rows = res.data or []
            if dropped:
                logger.warning(f"{label}: inserted after dropping unsupported columns: {', '.join(dropped)}")
            return rows[0] if rows else data
        except Exception as exc:
            err = str(exc)
            if _is_schema_error(err):
                missing_col = _extract_missing_column(err)
                if missing_col and missing_col in data:
                    data.pop(missing_col, None)
                    dropped.append(missing_col)
                    continue
            logger.error(f'{label}: {exc}')
            return None
    logger.error(f'{label}: no compatible columns left to insert.')
    return None

def _schema_tolerant_update(table_name: str, *, payload: dict[str, Any], match_field: str, match_value: Any, label: str) -> dict | None:
    supabase = get_supabase()
    if not supabase:
        return None
    data = dict(payload)
    dropped: list[str] = []
    while data:
        try:
            res = supabase.table(table_name).update(data).eq(match_field, match_value).execute()
            rows = res.data or []
            if dropped:
                logger.warning(f"{label}: updated after dropping unsupported columns: {', '.join(dropped)}")
            return rows[0] if rows else None
        except Exception as exc:
            err = str(exc)
            if _is_schema_error(err):
                missing_col = _extract_missing_column(err)
                if missing_col and missing_col in data:
                    data.pop(missing_col, None)
                    dropped.append(missing_col)
                    continue
            logger.error(f'{label}: {exc}')
            return None
    logger.error(f'{label}: no compatible columns left to update.')
    return None

def create_automation_job(payload: dict[str, Any]) -> dict | None:
    supabase = get_supabase()
    if not supabase:
        return None
    channel = str(payload.get('channel') or 'call').strip().lower()
    phone = normalize_phone_number(payload.get('phone_number', ''))
    if channel not in AUTOMATION_CHANNELS:
        raise ValueError('Unsupported automation channel')
    status = str(payload.get('status') or ('pending_review' if channel == 'call' else 'pending')).strip().lower()
    if status not in AUTOMATION_JOB_STATUSES:
        status = 'pending_review' if channel == 'call' else 'pending'
    row = {'channel': channel, 'trigger_event': str(payload.get('trigger_event') or '').strip() or None, 'phone_number': phone, 'caller_name': str(payload.get('caller_name') or '').strip(), 'template_name': str(payload.get('template_name') or '').strip() or None, 'message_type': str(payload.get('message_type') or 'text').strip().lower() or 'text', 'body_text': str(payload.get('body_text') or '').strip(), 'caption': str(payload.get('caption') or '').strip(), 'media_url': str(payload.get('media_url') or '').strip() or None, 'mime_type': str(payload.get('mime_type') or '').strip() or None, 'file_name': str(payload.get('file_name') or '').strip() or None, 'asset_id': payload.get('asset_id'), 'related_call_room_id': str(payload.get('related_call_room_id') or '').strip() or None, 'related_call_log_id': payload.get('related_call_log_id'), 'related_appointment_id': payload.get('related_appointment_id'), 'scheduled_for': str(payload.get('scheduled_for') or _utcnow_iso()), 'status': status, 'retry_count': int(payload.get('retry_count') or 0), 'max_retries': int(payload.get('max_retries') or 3), 'last_error': str(payload.get('last_error') or '').strip() or None, 'idempotency_key': str(payload.get('idempotency_key') or '').strip(), 'payload': payload.get('payload') or {}, 'created_at': _utcnow_iso(), 'updated_at': _utcnow_iso(), 'provider_message_id': str(payload.get('provider_message_id') or '').strip() or None, 'dispatch_id': str(payload.get('dispatch_id') or '').strip() or None}
    if not row['idempotency_key']:
        raise ValueError('Automation job idempotency_key is required.')
    try:
        res = supabase.table('automation_jobs').upsert(row, on_conflict='idempotency_key').execute()
        rows = res.data or []
        return rows[0] if rows else row
    except Exception as exc:
        logger.error(f'Failed to create automation job: {exc}')
        return None

def list_automation_jobs(*, limit: int=200, statuses: list[str] | None=None, channel: str | None=None, phone_number: str | None=None, appointment_id: str | int | None=None) -> list[dict[str, Any]]:
    supabase = get_supabase()
    if not supabase:
        return []
    try:
        query = supabase.table('automation_jobs').select('*').order('scheduled_for')
        if statuses:
            cleaned = [str(status).strip().lower() for status in statuses if str(status).strip()]
            if cleaned:
                if len(cleaned) == 1:
                    query = query.eq('status', cleaned[0])
                else:
                    query = query.in_('status', cleaned)
        if channel:
            query = query.eq('channel', channel)
        if phone_number:
            query = query.eq('phone_number', normalize_phone_number(phone_number))
        if appointment_id not in (None, ''):
            query = query.eq('related_appointment_id', str(appointment_id))
        if limit:
            query = query.limit(limit)
        res = query.execute()
        return res.data or []
    except Exception as exc:
        logger.error(f'Failed to fetch automation jobs: {exc}')
        return []

def get_automation_job(job_id: str | int) -> dict | None:
    supabase = get_supabase()
    if not supabase:
        return None
    try:
        res = supabase.table('automation_jobs').select('*').eq('id', str(job_id)).limit(1).execute()
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as exc:
        logger.error(f'Failed to fetch automation job {job_id}: {exc}')
        return None

def update_automation_job(job_id: str | int, payload: dict[str, Any]) -> dict | None:
    supabase = get_supabase()
    if not supabase:
        return None
    data = dict(payload)
    data['updated_at'] = _utcnow_iso()
    try:
        res = supabase.table('automation_jobs').update(data).eq('id', str(job_id)).execute()
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as exc:
        logger.error(f'Failed to update automation job {job_id}: {exc}')
        return None

def cancel_automation_jobs(*, related_appointment_id: str | int | None=None, channel: str | None=None, phone_number: str | None=None, statuses: list[str] | None=None) -> list[dict[str, Any]]:
    supabase = get_supabase()
    if not supabase:
        return []
    statuses = statuses or ['pending', 'retry', 'pending_review']
    try:
        query = supabase.table('automation_jobs').update({'status': 'cancelled', 'updated_at': _utcnow_iso()})
        if related_appointment_id not in (None, ''):
            query = query.eq('related_appointment_id', str(related_appointment_id))
        if channel:
            query = query.eq('channel', channel)
        if phone_number:
            query = query.eq('phone_number', normalize_phone_number(phone_number))
        if statuses:
            if len(statuses) == 1:
                query = query.eq('status', statuses[0])
            else:
                query = query.in_('status', statuses)
        res = query.execute()
        return res.data or []
    except Exception as exc:
        logger.error(f'Failed to cancel automation jobs: {exc}')
        return []

def fetch_due_automation_jobs(*, limit: int = 20, channel: str | None = None, now_iso: str | None = None) -> list[dict[str, Any]]:
    supabase = get_supabase()
    if not supabase:
        return []
    now_iso = now_iso or _utcnow_iso()
    try:
        query = supabase.table('automation_jobs').select('*').lte('scheduled_for', now_iso).order('scheduled_for').limit(limit)
        if channel:
            query = query.eq('channel', channel)
        query = query.in_('status', ['pending', 'retry'])
        res = query.execute()
        return res.data or []
    except Exception as exc:
        logger.error(f'Failed to fetch due automation jobs: {exc}')
        return []

def save_call_turn_metric(payload: dict[str, Any]) -> dict | None:
    supabase = get_supabase()
    if not supabase:
        return None
    row = {'call_room_id': str(payload.get('call_room_id') or '').strip() or None, 'phone_number': normalize_phone_number(payload.get('phone_number', '')) or None, 'turn_index': int(payload.get('turn_index') or 0), 'speaker': str(payload.get('speaker') or 'assistant').strip().lower() or 'assistant', 'stt_endpoint_ms': payload.get('stt_endpoint_ms'), 'llm_first_token_ms': payload.get('llm_first_token_ms'), 'tts_first_audio_ms': payload.get('tts_first_audio_ms'), 'tool_ms': payload.get('tool_ms'), 'total_turn_ms': payload.get('total_turn_ms'), 'metadata': payload.get('metadata') or {}, 'created_at': str(payload.get('created_at') or _utcnow_iso())}
    try:
        res = supabase.table('call_turn_metrics').insert(row).execute()
        rows = res.data or []
        return rows[0] if rows else row
    except Exception as exc:
        if _is_schema_error(str(exc)):
            return None
        logger.error(f'Failed to save call turn metric: {exc}')
        return None

def list_call_turn_metrics(*, call_room_id: str | None=None, phone_number: str | None=None, limit: int=200) -> list[dict[str, Any]]:
    supabase = get_supabase()
    if not supabase:
        return []
    try:
        query = supabase.table('call_turn_metrics').select('*').order('created_at')
        if call_room_id:
            query = query.eq('call_room_id', str(call_room_id))
        if phone_number:
            query = query.eq('phone_number', normalize_phone_number(phone_number))
        if limit:
            query = query.limit(limit)
        res = query.execute()
        return res.data or []
    except Exception as exc:
        if _is_schema_error(str(exc)):
            return []
        logger.error(f'Failed to fetch call turn metrics: {exc}')
        return []
