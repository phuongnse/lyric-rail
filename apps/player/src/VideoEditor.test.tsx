// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { VideoEditor } from "./VideoEditor";

let host: HTMLDivElement, root: Root, tick: FrameRequestCallback;
const save = vi.fn(), close = vi.fn();
const dialog = () => document.querySelector<HTMLDivElement>('.clip-video-dialog')!;
const button = (name: string) => [...dialog().querySelectorAll('button')].find(item => item.textContent === name)!;
const change = async (name: string, value: string) => act(async () => {
  const field = dialog().querySelector<HTMLInputElement | HTMLTextAreaElement>(`[aria-label="${name}"]`)!;
  const prototype = field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(field, value);
  field.dispatchEvent(new Event('input', { bubbles: true }));
  field.dispatchEvent(new Event('change', { bubbles: true }));
});
beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { tick = callback; return 1; });
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
  save.mockClear(); close.mockClear();
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  await act(async () => root.render(<VideoEditor preview={{ clipId: 'shared', suggestedTitle: 'Source', durationMillis: 120000, sizeBytes: 100,
    previewUrl: 'http://fixture/audio', videoUrl: 'http://fixture/video', videoOffsetMillis: 10000,
    frameTimesMillis: [10000, 20000, 20040, 79960, 80000, 120000] }} range={{ startMillis: 20000, endMillis: 80000 }} value={{ title: 'Chosen video', artist: 'Before' }} busy={false} onClose={close} onSave={save} onPlay={() => {}} />));
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it('works independently of clipping and exposes only video information with a relative preview', () => {
  expect(document.querySelector('.clip-picker')).toBeNull();
  expect(dialog().querySelector('.clip-section-track')).toBeNull();
  expect(dialog().querySelector('[aria-label*="start time"]')).toBeNull();
  expect(dialog().querySelector('[aria-label="Selected section"]')).toBeNull();
  const seek = dialog().querySelector<HTMLInputElement>('[aria-label="Seek video"]')!;
  expect([seek.min, seek.max, seek.value]).toEqual(['0', '60000', '0']);
  expect(dialog().querySelector('audio')!.currentTime).toBe(20);
  expect(dialog().querySelector('video')!.currentTime).toBeCloseTo(10.00001);
  expect(dialog().querySelector('output')!.textContent).toBe('00:00:00.000 / 00:01:00.000');
  expect(document.activeElement).toBe(dialog().querySelector('[aria-label="Video name"]'));
});

it('clamps seeking to the clip and never displays the frame after its end', async () => {
  await change('Seek video', '15000');
  expect(dialog().querySelector('audio')!.currentTime).toBe(35);
  expect(dialog().querySelector('video')!.currentTime).toBe(25);
  await change('Seek video', '60000');
  expect(dialog().querySelector('audio')!.currentTime).toBe(80);
  expect(dialog().querySelector('video')!.currentTime).toBeLessThan(70);
  await act(async () => button('Play').click());
  expect(dialog().querySelector('audio')!.currentTime).toBe(20);
  dialog().querySelector('audio')!.currentTime = 81;
  await act(async () => tick(1));
  expect(dialog().querySelector('audio')!.currentTime).toBe(80);
  expect(dialog().querySelector('video')!.currentTime).toBeLessThan(70);
  expect(button('Play')).toBeDefined();
});

it('saves metadata only, preserves UTF-8 lyrics and can clear optional fields', async () => {
  await change('Video name', ' Edited '); await change('Artist', '');
  const lyrics = '  Dòng một\r\nDòng hai\n';
  // Browsers normalize textarea newlines to LF before input; preserve its actual value.
  await change('Lyrics', lyrics);
  const entered = dialog().querySelector<HTMLTextAreaElement>('[aria-label="Lyrics"]')!.value;
  await act(async () => button('Save').click());
  expect(save).toHaveBeenCalledWith({ title: 'Edited', lyrics: entered });
  expect(save.mock.calls[0][0]).not.toHaveProperty('startMillis');
  expect(save.mock.calls[0][0]).not.toHaveProperty('endMillis');
});

it('blocks invalid names and lyric input without rewriting the draft', async () => {
  await change('Video name', ''); expect(button('Save').disabled).toBe(true);
  await change('Video name', 'Valid'); await change('Lyrics', 'bad\0text');
  expect(button('Save').disabled).toBe(true);
  expect(dialog().querySelector<HTMLTextAreaElement>('[aria-label="Lyrics"]')!.value).toBe('bad\0text');
});

it('discards on Cancel and handles Escape without dismissing the source dialog', async () => {
  await change('Video name', 'Unsaved');
  await act(async () => button('Cancel').click());
  expect(close).toHaveBeenCalledOnce(); expect(save).not.toHaveBeenCalled();
  const escaped = vi.fn(); document.addEventListener('keydown', escaped);
  await act(async () => dialog().querySelector('input')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })));
  expect(close).toHaveBeenCalledTimes(2); expect(escaped).not.toHaveBeenCalled();
  document.removeEventListener('keydown', escaped);
});

it('contains keyboard focus within the independent modal', async () => {
  const last = button('Save'); last.focus();
  const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
  await act(async () => last.dispatchEvent(event));
  expect(event.defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(dialog().querySelector('[aria-label="Close video editor"]'));
});

it('bounds playback even while the video start promise is pending', async () => {
  const pending: Array<() => void> = [];
  vi.mocked(HTMLMediaElement.prototype.play).mockImplementation(() => new Promise<void>(resolve => pending.push(resolve)));
  await act(async () => button('Play').click());
  dialog().querySelector('audio')!.currentTime = 81;
  await act(async () => tick(1));
  expect(dialog().querySelector('audio')!.currentTime).toBe(80);
  expect(dialog().querySelector('video')!.currentTime).toBeLessThan(70);
  await act(async () => pending.forEach(resolve => resolve()));
  expect(button('Pause')).toBeUndefined();
});
