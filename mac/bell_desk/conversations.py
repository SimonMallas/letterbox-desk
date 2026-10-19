"""Desk-local conversation projection. Letter files and phone state stay unchanged."""
import datetime
import time

from .watcher import read_letter_text
from . import attachments
import json


class Conversations:
    def __init__(self, app, mail):
        self.app, self.mail = app, mail
        self.viewing, self.visible = None, set()

    def rows(self, seat):
        incoming = []
        with self.mail.lock:
            files = self.mail._files()
            for name, path in files.items():
                r = self.mail._record(name, path)
                if r and r['from'] == seat:
                    incoming.append({**self.mail._decorate(r), 'direction': 'in', 'text': read_letter_text(path),
                                     'at': self.timestamp(r['ts']), 'via': ''})
        incoming.sort(key=lambda r: (r["at"], r["id"]))
        for item in reversed(self.app.state.sent_letters(limit=None)):
            part = next((r for r in item['results'] if r.get('to') == seat), None)
            if part:
                incoming.append({'id': item['request_id'] + '/' + seat, 'from': 'desk', 'direction': 'out',
                                 'text': part.get('text', self.app._copy_text(item.get('text') or '', seat,
                                         [r['to'] for r in item['results']])), 'at': item['at'], 'ts': '',
                                 'read': True, 'status': part['status'], 'request_id': item['request_id'],
                                 'via': item['via'], 're': part.get('re', item['re'])})
        for row in incoming:
            # Only our recorded outgoing copies carry trusted picture references.
            if row['direction'] == 'out' and '\nDesk picture: ' in row['text']:
                try:
                    record = json.loads(row['text'].rsplit('\nDesk picture: ', 1)[1])
                    row['picture_status'] = attachments.inspect_copy(self.app.state.config(), self.app.bus_dir, record)
                except (ValueError, TypeError):
                    row['picture_status'] = 'missing'
        return sorted(incoming, key=lambda r: r['at'])

    @staticmethod
    def timestamp(value):
        try:
            return datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except (ValueError, TypeError):
            return 0

    def snapshot(self):
        view = self.app.state.desk_view()
        keep = view.get('keep', 3)
        if type(keep) is not int or keep < 1:
            keep = 3
        done = view.get('done', {})
        if not isinstance(done, dict):
            done = {}
        cutoff = time.time() - keep * 86400
        result = []
        for seat in self.app.seats:
            rows = self.rows(seat)
            latest = next((r['id'] for r in reversed(rows) if r['direction'] == 'in'), '')
            archived = done.get(seat)
            hidden = isinstance(archived, list) and all(r['id'] in archived for r in rows)
            result.append({'seat': seat, 'latest': latest, 'last_at': rows[-1]['at'] if rows else 0, 'done': hidden,
                           'unread': sum(not r['read'] for r in rows if r['direction'] == 'in'),
                           'messages': [r for r in rows if r['at'] >= cutoff or not r['read'] or (seat == self.viewing and r['id'] in self.visible)]})
        cfg = self.app.state.config()
        return {'type': 'conversations', 'conversations': result, 'keep': keep,
                'picture_readers': [seat for seat in self.app.seats if attachments.verified(cfg, self.app.bus_dir, seat)],
                'look': view.get('look', 'auto'), 'rings': view.get('rings', True)}

    def update(self, message):
        view = self.app.state.desk_view()
        seat = message.get('seat')
        if message.get('action') == 'close':
            self.viewing, self.visible = None, set()
        elif message.get('action') == 'done' and seat in self.app.seats:
            self.viewing, self.visible = None, set()
            done = view.setdefault('done', {})
            done[seat] = [r['id'] for r in self.rows(seat)]
            for r in self.rows(seat):
                if r['direction'] == 'in':
                    self.mail.mark_read(r['id'])
        elif message.get('action') == 'restore' and seat in self.app.seats:
            view.setdefault('done', {}).pop(seat, None)
        elif message.get('action') == 'read' and seat in self.app.seats:
            if self.viewing != seat:
                self.viewing, self.visible = seat, set()
            self.visible.update(r['id'] for r in self.rows(seat) if not r['read'])
            for r in self.rows(seat):
                if r['direction'] == 'in':
                    self.mail.mark_read(r['id'])
        elif message.get('action') == 'settings':
            if type(message.get('keep')) is int:
                view['keep'] = max(1, min(3650, message['keep']))
            if message.get('look') in ('day', 'auto', 'night'):
                view['look'] = message['look']
            if type(message.get('rings')) is bool:
                view['rings'] = message['rings']
                if not view['rings']:
                    with self.app.lock:
                        self.app.last_mail = None
        self.app.state.save_desk_view(view)

    def send(self, draft):
        seat, to, text = draft.get('agent'), draft.get('to'), draft.get('text')
        if seat is not None and seat not in self.app.seats:
            return {'status': 'not_sent', 'reason': 'unknown_seat'}
        latest = next((r['id'] for r in reversed(self.rows(seat)) if r['direction'] == 'in'), '') if seat else ''
        if not isinstance(to, list):
            return {'status': 'not_sent', 'reason': 'no_seats'}
        _, problem = self.app._seats_from(to)
        if problem:
            return {'status':'not_sent','reason':problem}
        picture = draft.get('picture')
        allowed, denied = [], []
        if isinstance(picture, dict):
            for target in to:
                (allowed if attachments.verified(self.app.state.config(), self.app.bus_dir, target) else denied).append(target)
            if allowed:
                try:
                    record = attachments.copy_picture(self.app.state.config(), self.app.bus_dir, picture)
                    text = (text or '') + '\n' + attachments.line(record)
                except attachments.PictureError as exc:
                    return {'status': 'not_sent', 'reason': str(exc)}
        else:
            allowed = to
        if not allowed:
            return {'status': 'not_sent', 'reason': 'picture_access',
                    'results': [{'to': t, 'status': 'not_attempted', 'reason': 'picture_access'} for t in denied]}
        summary = self.app._from_mac(to, text, re_id=latest if seat in to else '', reply_seat=seat, blocked_seats=denied, picture_copy=isinstance(picture, dict))
        return summary
