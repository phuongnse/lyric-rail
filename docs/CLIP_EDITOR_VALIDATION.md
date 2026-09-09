# Clip editor validation

Windows native validation completed on 2026-09-09 for `clip-review-usability`.
The lifecycle reviews the complete change from
`292824f55864fe492800c77b3996b1b39aa3d46e`; its final profile reports and independent
review bind the repository snapshot. This record does not promote production readiness.

## Method

The actual Tauri development executable hosted the production `ClipEditor`, playback
and frame hooks, and focus-containment code in Windows WebView2 (Edge 152). A separate
QA application identifier and hidden window kept the user's application and desktop
untouched. The fixture was passed to the real `prepare_local_clip` IPC command;
native ffprobe, frame queries, opaque `clippreview` range serving, and WebView media
decoders were used without mocks. A temporary harness mounted the editor and drove its
DOM controls and WebView keyboard events through the loopback DevTools connection.

Media events, audio/video `currentTime`, paused state, and decoded video frame counts
provided runtime evidence. Audio output volume was zero during the final run to avoid
interrupting the user. This verifies native decoding and timing, not subjective
listening quality, the operating-system file picker, or physical mouse/keyboard input.
Viewport checks used 1280×820 and 960×640 inside the native WebView; they did not change
product window permissions. The earlier interrupted desktop-control attempt is not
counted as a completed test.

The generated fixture contains 120 seconds of 25 fps H264 video and AAC audio.
Its SHA-256 before and after testing was
`b9a8b932d92276b6733592e8a949ab12c8fd2a91812e18bc7268dfc4d67acd0a`.
No song was committed to either the QA or user catalog. Generated media, screenshots,
raw observations and temporary harness files remain excluded from Git.

## Observed outcomes

| Case | Native result |
| --- | --- |
| Section 20–80, listen to end | Starts at 75; pauses with the media clock at 80 |
| Section seek and Pause/Play | Seeks to 45.250, pauses near 46, resumes at that position; a live seek to 51.500 stays playing |
| End back one measured frame | End becomes 79.960; video seeks to the measured frame; tail starts at 74.960 and pauses at 79.960 |
| Listen to start | Section 20–80 auditions 20–25 |
| Short section 40–43 | Both edge auditions stay within 40–43 |
| Loop | Short section repeats twice within 40–43; the long section's tail repeats 75–80 |
| Rapid Play/Pause | Three rapid audition/pause attempts leave both media elements paused |
| Keyboard and zoom | End handle Left moves one frame; I/O mark stepped frames at 60.040/60.080; Space opens More controls without playback; Tab enters its controls; zoom halves the view; typing focus does not step media |
| Multiple songs | Names and invalid drafts survive switching; the repair action returns to the invalid song; reorder preserves identity; remove leaves the other song intact |
| Layout | Main controls and save action remain visible at both viewport sizes, without horizontal overflow; the default single-song view has 14 visible buttons |

The stopping clock is checked after pause/seek completes; this is not a claim of
sample-accurate speaker cutoff. Source cut boundaries remain integer milliseconds,
with measured frame timestamps used for frame editing.

## Verification boundary

Focused React regressions additionally cover fractional/VFR frame boundaries,
delayed video, stale requests, missing timestamps, invalid drafts, failed submission,
and pending-play cancellation. These mocks are regression tests, not native evidence.
All four profiles in [project.json](../.process/project.json) remain required:
`frontend`, `python`, `rust`, and `security`.

macOS and Linux native interaction checks were not performed on this Windows host.
The existing `desktop-media@1` readiness declaration remains `building`, with its
seven enforced capabilities and ten planned gaps unchanged.
