"""Dedicated mTLS endpoint. Fingerprints come from the validated TLS peer, never HTTP headers."""
import hashlib,json,ssl,os,time
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlsplit
from .db import sessions
from .desktop import poll,command,complete
from .voice_gateway import submit_voice,voice_result,voice_stream_state


def _chunk(handler, payload):
    data = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    raw = ('data: ' + data + '\n\n' if getattr(handler, '_voice_sse', False) else data + '\n').encode('utf-8')
    handler.wfile.write(f'{len(raw):X}\r\n'.encode('ascii'))
    handler.wfile.write(raw)
    handler.wfile.write(b'\r\n')
    handler.wfile.flush()


def stream_voice(handler, fingerprint, task_id, sse=False):
    """Stream task lifecycle events over the already authenticated mTLS socket."""
    # Validate before sending headers so invalid task IDs still receive the
    # ordinary JSON/HTTP error response from the gateway.
    with sessions()() as session:
        voice_stream_state(session, fingerprint, task_id)
    deadline = time.monotonic() + 300
    last = None
    cursor = 0
    stream_id = None
    last_heartbeat = time.monotonic()
    seen_progress = set()
    terminal = {'COMPLETED', 'FAILED', 'CANCELLED'} if sse else {'COMPLETED', 'FAILED', 'CANCELLED', 'WAITING_APPROVAL', 'WAITING_DEVICE', 'PAUSED'}
    handler._voice_sse = sse
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream; charset=utf-8' if sse else 'application/x-ndjson; charset=utf-8')
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('Connection', 'keep-alive')
    handler.send_header('Transfer-Encoding', 'chunked')
    handler.end_headers()
    try:
        while time.monotonic() < deadline:
            with sessions()() as session:
                state = voice_stream_state(session, fingerprint, task_id, cursor)
            # Recovered worker attempts have new opaque event IDs; never expose
            # lease claims. A reset tells the client not to combine two answers.
            rows = state['fragments']
            if stream_id and state['stream_id'] and stream_id != state['stream_id']:
                _chunk(handler, {'type': 'reset', 'message': 'Atlas начал ответ заново'})
                cursor = 0
                with sessions()() as session:
                    state = voice_stream_state(session, fingerprint, task_id)
                rows = state['fragments']
            stream_id = state['stream_id'] or stream_id
            for event in state['progress']:
                if event['id'] not in seen_progress:
                    _chunk(handler, {**event, 'task_id': task_id})
                    seen_progress.add(event['id'])
            for row in rows:
                if row['sequence'] > cursor:
                    _chunk(handler, {'type': 'fragment', 'task_id': task_id, **row})
                    cursor = row['sequence']
            signature = (state['status'], state.get('answer'), state.get('error'), state['step'], state['tool'])
            if signature != last:
                last = signature
                event = {'type': 'status', **{key: state[key] for key in ('task_id', 'status', 'message', 'step', 'steps', 'tool')}}
                if state['status'] == 'COMPLETED':
                    event.update({'type': 'answer', 'answer': state.get('answer') or ''})
                elif state['status'] == 'FAILED' and state.get('error'):
                    event['message'] = 'Atlas не смог завершить запрос; подробности доступны в истории задач'
                _chunk(handler, event)
                if state['status'] in terminal:
                    break
            if time.monotonic() - last_heartbeat >= 4:
                _chunk(handler, {'type': 'heartbeat'})
                last_heartbeat = time.monotonic()
            time.sleep(0.1)
    except PermissionError:
        try:
            _chunk(handler, {'type': 'denied', 'message': 'Голосовой доступ отозван'})
        except OSError:
            pass
    except (BrokenPipeError, ConnectionResetError, TimeoutError):
        return
    except Exception:
        try:
            _chunk(handler, {'type': 'server_error', 'message': 'Поток остановлен; проверьте историю Atlas'})
        except OSError:
            pass
    finally:
        try:
            handler.wfile.write(b'0\r\n\r\n')
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

def stream_controls(handler, fingerprint):
    from .voice_center import control_snapshot
    with sessions()() as session:
        state = control_snapshot(session, fingerprint)
    handler._voice_sse = True
    handler.send_response(200)
    for key, value in [('Content-Type', 'text/event-stream; charset=utf-8'), ('Cache-Control', 'no-store'),
                       ('Transfer-Encoding', 'chunked')]: handler.send_header(key, value)
    handler.end_headers()
    deadline, last, heartbeat = time.monotonic() + 300, None, 0.0
    try:
        while time.monotonic() < deadline:
            if state != last or time.monotonic() - heartbeat > 4:
                _chunk(handler, state)
                last, heartbeat = state, time.monotonic()
            time.sleep(0.2)
            with sessions()() as session: state = control_snapshot(session, fingerprint)
    except (PermissionError, OSError): pass
    finally:
        try: handler.wfile.write(b'0\r\n\r\n'); handler.wfile.flush()
        except OSError: pass


def make_server(address,cert,key,ca):
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert,key);context.load_verify_locations(cafile=ca)
    context.verify_mode=ssl.CERT_REQUIRED
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):self.dispatch()
        def do_POST(self):self.dispatch()
        def dispatch(self):
            self.connection.settimeout(10)
            try:
                der=self.connection.getpeercert(binary_form=True)
                if not der:raise PermissionError('Certificate required')
                fingerprint=hashlib.sha256(der).hexdigest()
                size=int(self.headers.get('Content-Length','0'))
                if not 0<=size<=80000:raise ValueError('Request too large')
                body=json.loads(self.rfile.read(size)) if size else {}
                with sessions()() as s:
                    route=urlsplit(self.path)
                    if self.command=='GET' and route.path=='/next':result=poll(s,fingerprint)
                    elif self.command=='POST' and route.path=='/permit':
                        row=command(s,fingerprint,body['id'],body['token'])
                        result={'allowed':row.status=='LEASED'}
                    elif self.command=='POST' and route.path=='/result':result=complete(s,fingerprint,body['id'],body['token'],body.get('result'),body.get('error'))
                    elif self.command=='POST' and route.path=='/voice':result=submit_voice(s,fingerprint,body)
                    elif self.command=='POST' and route.path=='/voice-cancel':
                        from .voice_gateway import voice_cancel
                        result=voice_cancel(s,fingerprint,body.get('task_id'))
                    elif self.command=='POST' and route.path=='/voice-consumed':
                        from .voice_gateway import voice_consumed
                        result=voice_consumed(s,fingerprint,body.get('task_id'))
                    elif self.command=='POST' and route.path=='/voice-state':
                        from .voice_center import report
                        result=report(s,fingerprint,body)
                    elif self.command=='POST' and route.path=='/voice-control-result':
                        from .voice_center import acknowledge
                        result=acknowledge(s,fingerprint,body)
                    elif self.command=='GET' and route.path=='/voice-control-events':
                        self.connection.settimeout(320)
                        stream_controls(self, fingerprint)
                        return
                    elif self.command=='GET' and route.path.startswith('/voice-sse/'):
                        self.connection.settimeout(320)
                        stream_voice(self, fingerprint, route.path.removeprefix('/voice-sse/'), sse=True)
                        return
                    elif self.command=='GET' and route.path.startswith('/voice-result/'):
                        result=voice_result(s,fingerprint,route.path.removeprefix('/voice-result/'))
                    elif self.command=='GET' and route.path.startswith('/voice-events/'):
                        self.connection.settimeout(320)
                        stream_voice(self, fingerprint, route.path.removeprefix('/voice-events/'))
                        return
                    else:raise PermissionError('Route denied')
                    if self.command!='POST' or route.path not in {'/voice'}:s.commit()
                status=200
            except PermissionError:status=403;result={'error':'Доступ запрещён'}
            except Exception:status=422;result={'error':'Некорректный запрос или результат'}
            raw=json.dumps(result,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(address,Handler)
    server.socket=context.wrap_socket(server.socket,server_side=True)
    return server

if __name__=='__main__':
    server=make_server((os.environ.get('ATLAS_DESKTOP_HOST','127.0.0.1'),int(os.environ.get('ATLAS_DESKTOP_PORT','8443'))),os.environ['ATLAS_DESKTOP_CERT'],os.environ['ATLAS_DESKTOP_KEY'],os.environ['ATLAS_DESKTOP_CA'])
    print('Desktop mTLS gateway запущен',flush=True);server.serve_forever()
