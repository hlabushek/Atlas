import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from atlas.db import sessions, engine
from atlas.models import Device, Task, VoiceControl, VoiceEvent, VoiceRequest, VoiceStation, Message, now
from atlas import voice_center as center, voice_gateway as gateway, local_voice
from atlas.voice_session import VoiceSession, command_grammar
from atlas.voice_quality import accept_trial, all_metrics, percentiles
from atlas.voice_privacy import sensitive_response


def setup_device(client):
    cid = client.post('/api/conversations', json={'title': 'Голос'}).json()['id']
    with sessions()() as session:
        device = Device(name='Микрофон', fingerprint=uuid.uuid4().hex * 2, enabled=True, roots=[], operations=[],
            expires_at=now() + timedelta(days=1), voice_enabled=True, voice_conversation_id=cid)
        session.add(device)
        session.commit()
        return device.id, device.fingerprint, cid


def request(session, fp, **extra):
    return gateway.submit_voice(session, fp, {'request_id': str(uuid.uuid4()), 'text': 'Объясни проект', 'mode': 'chat', **extra})


def test_voice_cancel_uses_web_gate_and_never_cancels_another_device(client):
    did, fp, cid = setup_device(client)
    with sessions()() as session:
        task = request(session, fp)
        assert gateway.voice_cancel(session, fp, task['task_id'])['status'] == 'CANCELLED'
        assert gateway.voice_cancel(session, fp, task['task_id'])['status'] == 'CANCELLED'
        other = Device(name='Другой', fingerprint=uuid.uuid4().hex * 2, enabled=True, roots=[], operations=[],
            expires_at=now() + timedelta(days=1), voice_enabled=True, voice_conversation_id=cid)
        session.add(other)
        session.commit()
        with pytest.raises(PermissionError): gateway.voice_cancel(session, other.fingerprint, task['task_id'])
        web_task = Task(conversation_id=cid, request_key=str(uuid.uuid4()), goal='Веб-запрос')
        session.add(web_task);session.commit()
        with pytest.raises(PermissionError): gateway.voice_cancel(session, fp, web_task.id)
        session.get(Device, did).voice_enabled = False;session.commit()
        with pytest.raises(PermissionError): gateway.voice_cancel(session, fp, task['task_id'])


def test_center_policy_revision_auth_secrets_and_local_only(client):
    did, fp, _ = setup_device(client)
    endpoint = '/api/voice/devices/' + did + '/settings'
    policy = center.CenterPolicy(network_mode='local_only').model_dump()
    response = client.put(endpoint, json={'revision': 0, 'policy': policy})
    assert response.status_code == 200
    assert client.put(endpoint, json={'revision': 0, 'policy': policy}).status_code == 409
    assert client.put(endpoint, json={'revision': 1, 'policy': {**policy, 'shell': 'cmd'}}).status_code == 422
    assert client.put(endpoint, json={'revision': 1, 'policy': {**policy, 'wake_words': ['test-owner-password-123']}}).status_code == 422
    with sessions()() as session:
        with pytest.raises(PermissionError): request(session, fp)
    client.post('/api/logout')
    for path in ('/api/voice/center', '/api/voice/capabilities', '/api/voice/events'):
        assert client.get(path).status_code == 401


def test_no_text_voice_reply_is_delivered_then_forgotten_and_retry_is_idempotent(client):
    _, fp, cid = setup_device(client)
    body = {'request_id': str(uuid.uuid4()), 'text': 'Временная фраза', 'mode': 'chat', 'store_transcripts': False}
    with sessions()() as session:
        queued = gateway.submit_voice(session, fp, body)
        tid = queued['task_id']
        task = session.get(Task, tid)
        task.status, task.result = 'COMPLETED', 'Временный ответ'
        session.commit()
        assert gateway.voice_result(session, fp, tid)['answer'] == 'Временный ответ'
        assert session.scalars(select(Message).where(Message.conversation_id == cid)).all() == []
        assert gateway.voice_consumed(session, fp, tid)['consumed']
        assert task.goal == '[Голосовой текст не сохранён]' and task.result is None
        assert gateway.submit_voice(session, fp, body)['task_id'] == tid
        assert session.get(VoiceRequest, tid).context == []
        with pytest.raises(ValueError): request(session, fp, mode='task', store_transcripts=False)


def test_abandoned_no_text_terminal_request_cleanup_never_changes_other_tasks(client):
    _, fp, cid = setup_device(client)
    with sessions()() as session:
        queued = request(session, fp, store_transcripts=False)
        task = session.get(Task, queued['task_id'])
        task.status, task.result = 'COMPLETED', 'Ответ'
        receipt = session.get(VoiceRequest, task.id)
        receipt.created_at = now() - timedelta(minutes=16)
        preserved = Task(conversation_id=cid, request_key=str(uuid.uuid4()), goal='Исторический текст', status='COMPLETED')
        session.add(preserved);session.commit()
        center.prune(session);session.commit()
        assert task.goal == '[Голосовой текст не сохранён]'
        assert preserved.goal == 'Исторический текст'


def test_controls_are_single_use_and_lost_dispatch_is_not_replayed(client):
    did, fp, _ = setup_device(client)
    with sessions()() as session:
        center.report(session, fp, {'state': {'state': 'listening', 'rms_percent': 3}, 'settings': center.CenterPolicy().model_dump()})
    endpoint = '/api/voice/devices/' + did + '/control'
    assert client.post(endpoint, json={'kind': 'approve'}).status_code == 422
    command = client.post(endpoint, json={'kind': 'stop'}).json()
    assert client.post(endpoint, json={'kind': 'stop'}).status_code == 409
    with sessions()() as session:
        first = center.control_snapshot(session, fp)['control']
        assert first['id'] == command['id']
        assert 'control' not in center.control_snapshot(session, fp)
        center.acknowledge(session, fp, {'id': first['id'], 'status': 'RUNNING'})
        with pytest.raises(PermissionError): center.acknowledge(session, fp, {'id': first['id'], 'status': 'RUNNING'})
        center.acknowledge(session, fp, {'id': first['id'], 'status': 'COMPLETED'})
        with pytest.raises(PermissionError): center.acknowledge(session, fp, {'id': first['id'], 'status': 'COMPLETED'})
        lost = VoiceControl(device_id=did, kind='repeat', status='DISPATCHED', expires_at=now() - timedelta(seconds=1))
        session.add(lost);session.flush()
        center.prune(session);session.commit()
        assert lost.status == 'UNKNOWN'


def test_telemetry_is_secret_safe_and_retention_policy_is_enforced(client):
    did, fp, _ = setup_device(client)
    with sessions()() as session:
        with pytest.raises(ValueError): center.report(session, fp, {'state': {'state': 'listening', 'transcript': 'password=secret-value'}})
        session.rollback()
        center.report(session, fp, {'state': {'state': 'recognizing', 'transcript': 'Обычная фраза'}, 'event': True,
                                    'settings': center.CenterPolicy(store_transcripts=False).model_dump()})
        assert 'transcript' not in session.get(VoiceStation, did).state
        assert 'transcript' not in session.scalar(select(VoiceEvent)).data
        assert center.snapshot(session)[0]['connected']


def test_shared_capability_denies_before_action_and_expires():
    action = [{'type': 'open_site', 'site': 'дневник'}]
    from atlas.capabilities import local_policy
    assert local_policy(action, {})['needs_confirmation']
    with pytest.raises(PermissionError): local_policy(action, {'local_grants': []})
    with pytest.raises(PermissionError): local_policy(action, {'local_grant_expires_at': time.time() - 1})
    assert local_policy(action, {'confirm_local_actions': False})['risk'] == 'medium'
    with pytest.raises(PermissionError): local_voice._run_action({'type': 'time_now'}, {'local_grants': []})


def test_structured_router_conflict_and_follow_up_are_bounded():
    config = {'sites': {'дневник': 'https://school.example'}}
    decision = local_voice._classify_intent('открой дневник', config, .91)
    assert {key: decision[key] for key in ('intent', 'target', 'confidence', 'risk', 'needs_confirmation')} == {
        'intent': 'open_site', 'target': 'дневник', 'confidence': .91, 'risk': 'medium', 'needs_confirmation': True}
    config['scenarios'] = [{'name': 'Громче', 'phrases': ['сделай громче'], 'actions': [{'type': 'media', 'command': 'next'}]}]
    assert local_voice._classify_intent('сделай громче', config)['route'] == 'clarify'
    session = VoiceSession()
    session.remember('user', 'сделай тише', [{'type': 'volume_delta', 'percent': -10}])
    assert session.resolve('еще на пять', {}) == [{'type': 'volume_delta', 'percent': -5}]
    assert isinstance(session.resolve('это не то', {}), str)
    for i in range(10): session.remember('user', str(i))
    assert len(session.turns) == 5
    session.updated = time.monotonic() - 121
    assert session.resolve('еще на пять', {}) is None
    assert '[unk]' in command_grammar(config)


def test_clarification_never_picks_first_and_is_permission_bound():
    from atlas.voice_dialogue import VoiceDialogue
    d = VoiceDialogue()
    options = [{'name': 'Сайт дневник', 'actions': [{'type': 'open_site', 'site': 'дневник'}]},
               {'name': 'Приложение дневник', 'actions': [{'type': 'launch_app', 'app': 'дневник'}]}]
    d.ask(options, {}, 1)
    assert d.resolve_choice('дневник', {}, 2) is None
    d.ask(options, {}, 3)
    assert d.resolve_choice('сайт дневник', {}, 4) == options[0]['actions']
    d.ask(options, {}, 5)
    assert d.resolve_choice('сайт дневник', {'local_grants': []}, 6) is None


def test_quality_metrics_do_not_store_text_and_empty_samples_are_unknown(tmp_path):
    path = tmp_path / 'quality-lab.json'
    result = {'vosk': 'атлас сделай громче', 'whisper': 'Атлас, сделай громче', 'whisper_status': 'ok',
              'vosk_seconds': .1, 'whisper_seconds': .4}
    metrics = accept_trial(path, result, 'Атлас, сделай громче', 'volume_delta', True, 'quiet', {})
    assert metrics['vosk']['command_accuracy_percent'] == 100
    assert metrics['whisper']['cer_percent'] == 0
    assert 'сделай' not in path.read_text(encoding='utf-8')
    accept_trial(path, {**result, 'vosk': 'атлас который час'}, 'обычная речь', '', False, 'music', {})
    assert all_metrics(tmp_path / 'voice.json')['quality_lab']['vosk']['false_wake_percent'] == 100
    assert percentiles([])['p95'] is None
    assert percentiles([.1,.2,.3])['p95'] == .3
    assert sensitive_response('Паспорт номер 1234') and sensitive_response('Напиши на user@example.com')


def test_quality_correctness_compares_parameters_not_only_intent(tmp_path):
    result = {'vosk': 'атлас поставь громкость тридцать', 'whisper_status': 'empty', 'whisper': ''}
    metrics = accept_trial(tmp_path / 'quality.json', result, 'атлас поставь громкость сорок', 'volume_set', True, 'quiet', {})
    assert metrics['vosk']['command_accuracy_percent'] == 0


def test_cancel_control_is_bound_to_displayed_task_and_retention_clears_events(client):
    did, fp, _ = setup_device(client)
    with sessions()() as session:
        task = request(session, fp)
        center.report(session, fp, {'state': {'state': 'listening', 'task_id': task['task_id'], 'transcript': 'Текст'},
                                    'event': True, 'settings': center.CenterPolicy().model_dump()})
    command = client.post('/api/voice/devices/' + did + '/control', json={'kind': 'cancel'})
    assert command.status_code == 202
    with sessions()() as session:
        assert session.get(VoiceControl, command.json()['id']).payload['task_id'] == task['task_id']
    assert client.put('/api/voice/devices/' + did + '/settings', json={
        'revision': 1, 'policy': center.CenterPolicy(store_transcripts=False).model_dump()}).status_code == 200
    with sessions()() as session:
        assert 'transcript' not in session.scalar(select(VoiceEvent)).data


def test_local_only_never_constructs_a_network_client(monkeypatch):
    monkeypatch.setattr(local_voice, '_atlas_client', lambda _: pytest.fail('Network identity constructed'))
    with pytest.raises(PermissionError): local_voice._send_to_atlas('Привет', {'network_mode': 'local_only'})


def test_voice_0015_migration_preserves_prior_data_and_matches_models(client):
    from alembic import command
    from alembic.config import Config
    config = Config('alembic.ini')
    command.downgrade(config, '0014')
    with engine().begin() as connection:
        connection.execute(text("INSERT INTO conversations(id,title,created_at) VALUES ('voice-legacy','Не менять',CURRENT_TIMESTAMP)"))
    command.upgrade(config, 'head')
    with engine().connect() as connection:
        assert connection.scalar(text("SELECT title FROM conversations WHERE id='voice-legacy'")) == 'Не менять'
    command.check(config)
