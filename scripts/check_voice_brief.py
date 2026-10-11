"""Одна явная платная проверка короткой генерации, без микрофона/TTS/истории текста."""
import argparse
import re
from atlas.local_voice import _send_to_atlas, _atlas_client
from atlas.voice_settings import config_path, effective_config
from atlas.voice_speech import SpeechText


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Один запрос к настроенной модели, с обычным бюджетным ledger')
    args = parser.parse_args()
    if not args.live:
        parser.error('Для платной проверки требуется явный --live')
    config = effective_config(config_path(None))
    config['store_transcripts'] = False
    tasks = []
    config['_voice_task_callback'] = tasks.append
    answer = _send_to_atlas('Объясни, что такое нейросеть и как она устроена.', config)
    assert len(tasks) == 1
    with _atlas_client(config) as client:
        result = client.get('/voice-result/' + tasks[0])
        result.raise_for_status()
        assert result.json()['status'] == 'COMPLETED', 'Модель ещё не завершила настоящий ответ'
    count = len(re.findall(r'\b[\w-]+\b', answer))
    assert count <= 90, 'Модель не соблюла короткий голосовой стиль'
    assert not re.search(r'[*`]|_\S[^\n]*_|^\s*#|^\s*[-+]\s', answer, re.MULTILINE), 'Модель вернула разметку'
    assert ' '.join(SpeechText().feed(answer, final=True))
    print(f'PASS: реальная модель вернула краткий ответ ({count} слов) без Markdown; текст обработан без озвучки')


if __name__ == '__main__':
    main()
