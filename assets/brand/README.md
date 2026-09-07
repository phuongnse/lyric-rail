# LyricRail brand mark

`lyricrail-mark.svg` is the canonical source for every in-app and bundle icon.

The two rows each contain three lyric-syllable capsules on a shared timeline. Gold and
cyan exchange lanes across one wide diagonal negative-space playhead. The upper middle
capsule resolves fully to gold while the lower middle keeps a smaller cyan lead-in and
a larger gold completion, representing duet roles, timed highlighting and audio-track
switching without using a generic note, microphone,
waveform or security symbol.

The field around the lyric rows is transparent in both the in-app mark and every
native desktop icon. Ink is reserved for the intentional diagonal playhead separator;
do not add an enclosing tile or platform-specific background.

Palette:

- Ink: `#0B0E15`
- Karaoke gold: `#FFCC4D`
- Vocal cyan: `#5BD8D2`

The master contains only flat SVG geometry. It has no font, raster image, gradient,
filter, shadow or third-party artwork. Regenerate the Tauri icon set from repository
root with:

```text
npm run brand:icons
```

That command uses the lockfile-owned Tauri CLI and rewrites `generated-icons.json`
with the source and output SHA-256 values. Do not hand-edit generated bundle icons.

The image-generation drafts that established the lyric-row/playhead direction were
exploration only; this deterministic SVG is the shipping master.
