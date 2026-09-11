export type VideoMetadata = { title: string; artist?: string; composer?: string; lyrics?: string };
export const MAX_VIDEO_TEXT_CHARS = 200;
export const MAX_LYRICS_BYTES = 1_000_000;

export function validVideoMetadata(value: VideoMetadata): boolean {
  const text = (value?: string) => value === undefined || (value.length <= MAX_VIDEO_TEXT_CHARS && !/[\u0000-\u001f\u007f-\u009f]/u.test(value));
  return value.title.trim().length > 0 && text(value.title) && text(value.artist) && text(value.composer)
    && (value.lyrics === undefined || (new TextEncoder().encode(value.lyrics).byteLength <= MAX_LYRICS_BYTES && !value.lyrics.includes('\0')));
}

export function cleanVideoMetadata(value: VideoMetadata): VideoMetadata {
  const result: VideoMetadata = { title: value.title.trim() };
  if (value.artist?.trim()) result.artist = value.artist.trim();
  if (value.composer?.trim()) result.composer = value.composer.trim();
  if (value.lyrics?.trim()) result.lyrics = value.lyrics;
  return result;
}
