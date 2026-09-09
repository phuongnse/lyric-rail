# Cut a song from a local file

Open **Library → Local → Files** and choose one audio or video file. The editor starts
with the whole file selected. Your original file stays unchanged.

1. Drag the green **Start** and gold **End** handles on the source timeline to choose
   the song. Click or scrub the timeline to find a moment in the source.
2. Fine-tune the times beside the video. You can enter seconds (`79.960`), minutes and
   seconds (`01:19.960`), or hours, minutes and seconds. The two arrows beside each
   field move that edge by one actual video frame, or 10 ms for audio. **Set at
   playhead** uses the current preview position.
3. Use **Listen to start** or **Listen to end** to audition up to five seconds inside
   the song. Adjust the edge and listen again. **Add 1 song to queue** saves the
   selected range; add that song's exact lyrics in Library to start processing.

## Listen and adjust

The slider under **Play** seeks only within the selected song. It also returns to
the full selected interval after a short edge audition. **Pause** holds your place;
**Play** resumes there. At the end of an audition, Play repeats that audition.
**Loop** repeats the current song or the current short audition.

For a 20–80 second section, Listen to end plays 75–80 seconds. On a 25 fps file,
moving End back one frame gives 79.960, and the next tail audition starts at 74.960.
For a section shorter than five seconds, both auditions stay inside that section.

The source timeline is for locating and trimming material. Seeking outside the
selected song enters **Browsing full file**; seeking inside it returns to song
review. Use the slider under Play or an edge audition to return to the selected song.
Changing the timeline view does not move the playhead or alter the cut.

**Whole file** restores the complete source view. **Fit song** enlarges the selected
range. **More controls** contains zoom, pan, Go to Start/End, frame stepping, volume
and keyboard help. Going to or nudging an edge shows that frame; Play then auditions
the corresponding edge when its position is at the end of the audition range.

Exact frame actions use measured source timestamps, including variable frame rates.
If nearby frames are still loading, an endpoint nudge waits for them. Seeking,
typing a time or selecting another song cancels that pending adjustment. Playback,
seeking and exact time entry remain available when frame inspection fails; use
**Retry frame details** for another attempt. No nominal-FPS estimate replaces missing
frame evidence.

## More than one song

**Add another song** creates a section at the playhead. Expand **N songs selected**
to switch between songs, change their order or remove a section. Edit the active
song's name beside the video. The editor supports up to 128 sections.

Each section becomes a separate Library item, placed at the top of Library and the
queue in editor order. Each waits for its own exact lyrics; full-file sidecar lyrics
are not silently assigned to extracted sections.

Unfinished or invalid times stay with their song when you switch. Saving remains
disabled until they are corrected. The error names the affected song and, when
needed, provides **Go to song N**. Closing the editor cancels this selection without
modifying the source file.

## Keyboard

| Focus | Key | Action |
| --- | --- | --- |
| Video preview | Left / Right | Previous / next measured frame; 10 ms for audio |
| Video preview | I / O | Set Start / End at the playhead |
| Video preview | Space | Play / pause |
| Start or End timeline handle | Arrow keys | Move that edge by one frame or 10 ms |
| Start or End timeline handle | Home / End | Try the file start / end while preserving a valid range |
| Section seek slider | Arrows, Home / End | Seek within the song |
| More controls / song list heading | Enter / Space | Expand / collapse |
| Any editor control | Tab / Shift+Tab | Move within the editor |

Typing in a name or time field does not trigger playback shortcuts. Buttons retain
their normal keyboard activation.
