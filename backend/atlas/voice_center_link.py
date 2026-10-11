"""Исходящий mTLS/SSE link. Только метаданные, фиксированные controls, без shell."""
from copy import deepcopy
import json
import queue
import threading
from .voice_privacy import private_transcript


class VoiceCenterLink:
    def __init__(self, path, config):
        self.path, self.config = path, dict(config)
        self.stop = threading.Event()
        self.commands = queue.Queue(maxsize=16)
        self.lock = threading.Lock()
        self.state = {'state': 'listening', 'route': 'local'}
        self.events = queue.Queue(maxsize=50)
        self.client = None
        self.revision = -1
        self.threads = []

    def __enter__(self):
        if self.config.get('voice_center_enabled') and self.config.get('companion_config'):
            for fn in (self.receive, self.publish):
                thread = threading.Thread(target=fn, daemon=True)
                thread.start()
                self.threads.append(thread)
        return self

    def __exit__(self, *_):
        self.stop.set()
        if self.client:
            try: self.client.close()
            except Exception: pass
        for thread in self.threads: thread.join(timeout=0.5)

    def update(self, *, event=False, **values):
        # Do not let partial ASR or arbitrary exception strings reach the server.
        from .voice_settings import effective_config
        try: config = effective_config(self.path)
        except (OSError, ValueError): config = self.config
        keep = config.get('store_transcripts', True) and config.get('network_mode') != 'local_only'
        if not keep:
            values.pop('transcript', None)
            values.pop('context', None)
        if private_transcript(json.dumps(values, ensure_ascii=False)): return
        with self.lock:
            if not keep:
                self.state.pop('transcript', None)
                self.state.pop('context', None)
            self.state.update(values)
            self.state.update(network_mode=config.get('network_mode', 'atlas'), activation_mode=config.get('activation_mode', 'wake_word'),
                              default_mode=config.get('default_mode', 'chat'))
            if event:
                try: self.events.put_nowait(deepcopy(self.state))
                except queue.Full: pass

    def public_policy(self):
        from .voice_center import CenterPolicy
        from .voice_settings import effective_config
        config = effective_config(self.path)
        return {key: config.get(key, field.default) for key, field in CenterPolicy.model_fields.items() if key in config}

    def publish(self):
        from .local_voice import _atlas_client
        while not self.stop.is_set():
            try:
                with _atlas_client(self.config) as client:
                    while not self.stop.is_set():
                        try: event = self.events.get(timeout=1)
                        except queue.Empty: event = None
                        if self.stop.is_set(): return
                        with self.lock: value = deepcopy(self.state)
                        from .voice_quality import all_metrics
                        value['metrics'] = all_metrics(self.path)
                        response = client.post('/voice-state', json={'state': event or value, 'event': event is not None, 'settings': self.public_policy()})
                        response.raise_for_status()
            except Exception:
                self.stop.wait(3)

    def receive(self):
        from .local_voice import _atlas_client
        from .voice_settings import update_settings, effective_config, update_scenarios
        while not self.stop.is_set():
            try:
                with _atlas_client(self.config) as client:
                    self.client = client
                    with client.stream('GET', '/voice-control-events', timeout=10) as response:
                        response.raise_for_status()
                        for line in response.iter_lines():
                            if self.stop.is_set(): return
                            if not line.startswith('data: ') or len(line) > 30000: continue
                            state = json.loads(line[6:])
                            policy = state.get('policy', {})
                            if policy and state['revision'] != self.revision:
                                from .voice_center import CenterPolicy
                                policy = CenterPolicy.model_validate(policy).model_dump()
                                scenarios = policy.pop('scenarios', [])
                                # Local allowlist validation is the final authority for scenario targets.
                                current = effective_config(self.path)
                                from .voice_settings import validate_scenarios
                                scenario_error = ''
                                try: validate_scenarios(scenarios, current)
                                except (ValueError, TypeError, KeyError):
                                    scenarios = None
                                    scenario_error = 'Сценарии не применены: проверьте локальный allowlist и параметры. Остальные настройки применены.'
                                update_settings(self.path, policy)
                                if scenarios is not None: update_scenarios(self.path, scenarios)
                                self.revision = state['revision']
                                self.update(event=bool(scenario_error), policy_error=scenario_error)
                            command = state.get('control')
                            if command:
                                client.post('/voice-control-result', json={'id': command['id'], 'status': 'RUNNING'}).raise_for_status()
                                try: self.commands.put_nowait(command)
                                except queue.Full:
                                    client.post('/voice-control-result', json={'id': command['id'], 'status': 'FAILED'}).raise_for_status()
            except Exception:
                self.stop.wait(3)
            finally:
                self.client = None

    def finish(self, command, success):
        from .local_voice import _atlas_client
        def complete():
            try:
                with _atlas_client(self.config) as client:
                    client.post('/voice-control-result', json={'id': command['id'], 'status': 'COMPLETED' if success else 'FAILED'}).raise_for_status()
            except Exception: pass  # No replay; server shows UNKNOWN after expiry.
        threading.Thread(target=complete, daemon=True).start()
