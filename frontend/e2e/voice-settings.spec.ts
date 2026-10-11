import {test, expect, Page} from '@playwright/test';
import {readFileSync} from 'node:fs';

const source = readFileSync('../backend/atlas/voice_settings.py', 'utf8');
const html = source.split("SETTINGS_HTML = r'''")[1].split("'''", 1)[0];

async function openSettings(page: Page) {
  await page.goto('about:blank');
  await page.evaluate(() => {
    const state = {stage: 'idle', message: 'Проверка не запущена'};
    const calls: string[] = [];
    const settings = {
      enabled: true, default_mode: 'chat', wake_words: ['атлас'], activation_mode: 'wake_word',
      ptt_key: 'F8', device_index: -1, sample_rate: 16000, min_confidence: 0.55,
      free_speech_enabled: true, free_speech_model_path: 'C:/models/whisper-medium',
      free_speech_device: 'cuda', free_speech_compute_type: 'float16',
      follow_up_seconds: 0, confirmation_timeout_seconds: 12,
      network_mode: 'atlas', store_transcripts: true, speak_sensitive: false, session_context_seconds: 60,
      confirm_local_actions: true, test_mode: false, speak_responses: false, voice_interrupt_enabled: false,
      tts_model_path: '', tts_models: [], tts_length_scale: 0.94, reply_timeout_seconds: 90,
      show_command_pill: true, command_pill_seconds: 5, profiles: [], active_profile: 'По умолчанию',
      audio_devices: [{index: 1, name: 'Тестовый микрофон', default: true}], scenarios: [],
      model_available: true, free_speech_dependency_available: true, free_speech_model_available: true,
      free_speech_models: [{path: 'C:/models/whisper-medium', name: 'whisper-medium', size_mb: 1457}],
    };
    Object.assign(window, {probeCalls: calls, pywebview: {api: {
      get_settings: async () => settings, get_history: async () => [],
      prepare_recognition_probe: async () => {calls.push('prepare'); Object.assign(state, {stage: 'ready', message: 'Модели готовы'});},
      recognition_probe_state: async () => state,
      begin_recognition_probe: async () => {
        calls.push('begin');
        Object.assign(state, {stage: 'completed', message: 'Проверка завершена', result: {
          vosk: 'расскажи какая сегодня погода', whisper: 'Расскажи, какая сегодня погода?',
          vosk_seconds: 0.04, whisper_seconds: 0.5, rms_percent: 8, whisper_status: 'ok',
          vosk_route: 'atlas', whisper_route: 'atlas',
          vosk_wer: {errors: 0, words: 4, percent: 0}, whisper_wer: {errors: 0, words: 4, percent: 0},
        }});
      },
      cancel_recognition_probe: async () => {calls.push('cancel'); Object.assign(state, {stage: 'cancelled', message: 'Проверка отменена'});},
      save_settings: async (value: object) => {Object.assign(settings, value); calls.push('save'); return settings;},
      open_model_settings: async () => {calls.push('models');return 'Открыты настройки моделей Atlas';},
      quality_metrics: async () => ({runtime_seconds: {interrupt: {samples: 0,p95: null}}}),
      accept_quality_trial: async () => {calls.push('quality');return {trials: 1, vosk: {wer_percent: 0}};},
      test_tts_latency: async () => {calls.push('tts');return {runtime_seconds: {tts_first_audio: {samples:1,p50:.4,p95:.4}}};},
    }}});
  });
  await page.setContent(html);
  await page.evaluate(() => window.dispatchEvent(new Event('pywebviewready')));
  await expect(page.locator('#bridge_status')).toContainText('Настройки подключены');
}

test('voice comparison waits for preparation and explicit recording countdown', async ({page}, testInfo) => {
  await openSettings(page);
  await expect(page.locator('#free_speech_model_path')).toHaveValue('C:/models/whisper-medium');
  await page.locator('#expected_phrase').fill('расскажи какая сегодня погода');
  await page.getByRole('button', {name: 'Подготовить сравнение'}).click();
  await expect(page.locator('#begin_probe')).toBeEnabled();
  expect(await page.evaluate(() => (window as unknown as {probeCalls: string[]}).probeCalls)).toEqual(['prepare']);
  await page.getByRole('button', {name: 'Начать запись'}).click();
  await expect(page.locator('#probe_status')).toContainText('Приготовьтесь');
  await expect(page.locator('#save')).toBeDisabled();
  await expect(page.locator('#probe_results')).toContainText('Whisper: Расскажи', {timeout: 8000});
  await expect(page.locator('#probe_results')).toContainText('ошибок слов: 0/4');
  await expect(page.locator('#save')).toBeEnabled();
  await page.screenshot({path: testInfo.outputPath('voice-settings.png'), fullPage: true});
});

test('quality lab stores explicit numerical trial only and reports unknown measurements',async({page})=>{
 await openSettings(page);
 await page.locator('#quality_refresh').click();
 await expect(page.locator('#quality_result')).toContainText('null');
 await page.locator('#prepare_probe').click();
 await expect(page.locator('#begin_probe')).toBeEnabled();
 await page.locator('#begin_probe').click();
 await expect(page.locator('#probe_results')).toContainText('Whisper:',{timeout:8000});
 await page.locator('#quality_condition').selectOption('music');
 await page.locator('#quality_wake').uncheck();
 await page.locator('#quality_accept').click();
 await expect(page.locator('#quality_result')).toContainText('"trials": 1');
 await page.locator('#quality_tts').click();
 await expect(page.locator('#quality_result')).toContainText('tts_first_audio');
});

test('cancel during countdown prevents microphone start and settings preserve new fields', async ({page}) => {
  await openSettings(page);
  await expect(page.locator('#follow_up_seconds')).toBeDisabled();
  await page.locator('#confirmation_timeout_seconds').fill('20');
  await page.locator('#voice_interrupt_enabled').check();
  await page.locator('#save').click();
  await expect(page.locator('#follow_up_seconds')).toHaveValue('0');
  await expect(page.locator('#confirmation_timeout_seconds')).toHaveValue('20');
  await expect(page.locator('#voice_interrupt_enabled')).toBeChecked();
  await page.locator('#prepare_probe').click();
  await expect(page.locator('#begin_probe')).toBeEnabled();
  await page.locator('#begin_probe').click();
  await expect(page.locator('#probe_status')).toContainText('Приготовьтесь');
  await page.locator('#cancel_probe').click();
  await expect(page.locator('#probe_status')).toContainText('Проверка отменена');
  await page.waitForTimeout(3200);
  expect(await page.evaluate(() => (window as unknown as {probeCalls: string[]}).probeCalls)).not.toContain('begin');
});

test('strict activation is default and conversation or PTT requires explicit choice', async ({page}) => {
  await openSettings(page);
  await expect(page.locator('#activation_mode')).toHaveValue('wake_word');
  await expect(page.locator('#activation_hint')).toContainText('фоновая речь игнорируется');
  await expect(page.locator('#follow_up_seconds')).toBeDisabled();
  await page.locator('#activation_mode').selectOption('wake_word_followup');
  await expect(page.locator('#follow_up_seconds')).toBeEnabled();
  await page.locator('#follow_up_seconds').fill('9');
  await page.locator('#save').click();
  await expect(page.locator('#follow_up_seconds')).toHaveValue('9');
  await page.locator('#activation_mode').selectOption('wake_word');
  await page.locator('#save').click();
  await expect(page.locator('#activation_mode')).toHaveValue('wake_word');
  await expect(page.locator('#follow_up_seconds')).toBeDisabled();
  await page.locator('#activation_mode').selectOption('push_to_talk');
  await expect(page.locator('#ptt_key')).toBeEnabled();
  await expect(page.locator('#wake_words')).toBeDisabled();
  await page.locator('#model_settings').click();
  await expect(page.locator('#model_settings_result')).toContainText('Открыты настройки моделей');
  expect(await page.evaluate(() => (window as unknown as {probeCalls: string[]}).probeCalls)).toContain('models');
});
