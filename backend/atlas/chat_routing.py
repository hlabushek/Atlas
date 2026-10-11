"""Дешёвая явная маршрутизация, не классификатор полномочий и не LLM."""
import re
from .model_settings import Preferences, resolve
from .models import ModelPreferences

DEEP_REQUEST = re.compile(
    r'\b(?:подумай|обдумай|проанализируй|обоснуй|докажи|сравни|'
    r'разбери\s+(?:подробно|детально)|объясни\s+(?:подробно|детально)|'
    r'подробно\s+(?:разбери|объясни)|реши\s+(?:задачу|уравнение)|'
    r'проверь\s+(?:код|расч[её]т)|составь\s+план)\b', re.IGNORECASE)
FAST_REQUEST = re.compile(r'^\s*(?:атлас[,\s]+)?(?:быстро|кратко|коротко|ответь\s+кратко)\b',re.IGNORECASE)

def chat_profile(session, content, requested='auto'):
    if requested not in {'auto','main','planner'}:
        raise ValueError('Неизвестный режим ответа')
    prefs=Preferences.model_validate(session.get(ModelPreferences,1).config)
    if requested != 'auto':
        role,reason=requested,'manual'
    elif not prefs.auto_chat_routing:
        role,reason='main','auto_disabled'
    elif FAST_REQUEST.search(content):
        role,reason='main','brief_request'
    elif DEEP_REQUEST.search(content):
        role,reason='planner','deep_request'
    elif len(content)>1200:
        role,reason='planner','long_request'
    else:
        role,reason='main','ordinary_request'
    profile=dict(resolve(role,session).snapshot(),role=role,requested_role=requested,routing_reason=reason)
    # Активируется только по явному включению схемы владельцем. Старые настройки
    # и уже поставленные в очередь задачи сохраняют прежнее поведение.
    if prefs.auto_chat_routing:
        profile['chat_mode']='fast' if role=='main' else 'careful'
        if role=='main':profile['max_output_tokens']=min(profile['max_output_tokens'],512)
    return profile

def requested_role(profile):
    profile=profile or {}
    return profile.get('requested_role',profile.get('role','main'))
