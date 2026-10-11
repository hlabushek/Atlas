"""Deterministic answers from saved school data; no claim of live site access."""
import re
from datetime import date,datetime,timedelta

from sqlalchemy import select

from .diary import SCHOOL_TZ,school_day,school_days
from .models import Assignment
from .notifications import utc

WEEKDAYS={'понедельник':0,'вторник':1,'среду':2,'среда':2,'четверг':3,
          'пятницу':4,'пятница':4,'субботу':5,'суббота':5,'воскресенье':6}
MONTHS={'января':1,'февраля':2,'марта':3,'апреля':4,'мая':5,'июня':6,
        'июля':7,'августа':8,'сентября':9,'октября':10,'ноября':11,'декабря':12}

def _target(text:str,today:date):
    match=re.search(r'(?<!\d)(20\d\d)-(\d{1,2})-(\d{1,2})(?!\d)',text)
    if match:return date(*map(int,match.groups())),True
    match=re.search(r'(?<!\d)(\d{1,2})\.(\d{1,2})(?:\.(20\d\d))?(?!\d)',text)
    if match:return date(int(match[3] or today.year),int(match[2]),int(match[1])),True
    for month,number in MONTHS.items():
        match=re.search(r'(?<!\d)(\d{1,2})\s+'+month+r'(?:\s+(20\d\d))?',text)
        if match:return date(int(match[2] or today.year),number,int(match[1])),True
    if 'послезавтра' in text:return today+timedelta(days=2),True
    if 'завтра' in text:return today+timedelta(days=1),True
    if 'вчера' in text:return today-timedelta(days=1),True
    if 'сегодня' in text:return today,True
    for name,weekday in WEEKDAYS.items():
        if re.search(r'\b'+name+r'\b',text):
            delta=(weekday-today.weekday())%7
            if re.search(r'следующ\w*\s+'+name,text):delta+=7
            return today+timedelta(days=delta),True
    return today,False

def _intent(goal:str):
    text=goal.casefold()
    if re.search(r'\b(реши|решить|выполни|выполнить|помоги|объясни|составь|создай|напиши)\b',text):
        return None
    schedule=bool(re.search(r'расписани|\bуроки\b|\bуроков\b|\bпары\b',text))
    homework=bool(re.search(r'\bдз\b|домашн|задани|что задали',text))
    school=bool(re.search(r'школ|дневник|уроки|\bдз\b|домашн',text))
    if not (schedule or homework or (school and re.search(r'что\s+на|покажи',text))):return None
    if not school and schedule and re.search(r'работ|проект|встреч|поезд|достав|автобус|фильм|рейс',text):return None
    return text,schedule or not homework,homework or not schedule

def matches(goal:str)->bool:
    return _intent(goal) is not None

def _stamp(value:str|None):
    if not value:return 'неизвестно'
    return utc(datetime.fromisoformat(value)).astimezone(SCHOOL_TZ).strftime('%d.%m.%Y %H:%M МСК')

def _day_lines(data:dict,show_schedule:bool,show_homework:bool):
    lines=[date.fromisoformat(data['date']).strftime('%d.%m.%Y')]
    if show_schedule:
        if not data['schedule_known']:lines.append('Расписание на этот день не загружено.')
        elif not data['lessons']:lines.append('В загруженном расписании уроков нет.')
        else:
            lines.append('Уроки:')
            for index,lesson in enumerate(data['lessons'],1):
                time=(lesson.get('starts_at') or '')+('–'+lesson['ends_at'] if lesson.get('ends_at') else '')
                lines.append(f"{index}. {time+' ' if time else ''}{lesson['subject']}"+
                             (f", кабинет {lesson['room']}" if lesson.get('room') else '')+
                             (f"; ДЗ: {lesson['homework']}" if lesson.get('homework') else ''))
            lines.append('Расписание обновлено '+_stamp(data['schedule_synced_at'])+'.')
    if show_homework:
        if data['homework']:
            lines.append('Домашнее задание:')
            for item in data['homework']:
                lines.append(f"• {item['subject']}: {item['title']}"+
                             (f" (оценка {item['grade']})" if item.get('grade') else ''))
        else:lines.append('В сохранённых данных нет ДЗ на этот день; это не подтверждает, что его нет на сайте.')
    return '\n'.join(lines)

def reply(session,goal:str,today:date|None=None):
    intent=_intent(goal)
    if intent is None:return None
    text,show_schedule,show_homework=intent
    today=today or datetime.now(SCHOOL_TZ).date()
    try:target,explicit=_target(text,today)
    except ValueError:return 'Не удалось распознать дату. Укажите её в формате ДД.ММ.ГГГГ.'
    if (re.search(r'\bсписком\b|\bсписок\b|\bвсе\b|\bвсё\b',text)) and show_homework and not explicit and not show_schedule:
        assignments=session.scalars(select(Assignment).where(Assignment.provider=='school.mos.ru')
            .order_by(Assignment.due_at).limit(101)).all()
        if not assignments:return 'Сохранённых заданий пока нет. Это не означает, что их нет на школьном сайте.'
        lines=['Сохранённое ДЗ по датам:']
        for item in assignments[:100]:
            day=utc(item.due_at).astimezone(SCHOOL_TZ).strftime('%d.%m.%Y')
            lines.append(f'• {day} — {item.subject}: {item.title}')
        if len(assignments)>100:lines.append('Показаны первые 100 заданий; уточните дату для остальных.')
        latest=max(utc(item.synced_at) for item in assignments)
        lines.append('Последнее обновление этих заданий: '+_stamp(latest.isoformat())+'. Данные могут устареть.')
        return '\n'.join(lines)
    if 'недел' in text and not explicit:
        start=target-timedelta(days=target.weekday())
        if 'следующ' in text:start+=timedelta(days=7)
        days=school_days(session,start,7)
    else:days=[school_day(session,target)]
    output='\n\n'.join(_day_lines(day,show_schedule,show_homework) for day in days)
    if show_homework:
        output+='\n\nПоследнее обновление сохранённого ДЗ: '+_stamp(days[0]['homework_latest_sync'])+'. Данные могут устареть.'
    return output
