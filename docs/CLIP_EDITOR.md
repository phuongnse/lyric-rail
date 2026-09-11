# Cut songs from a local file

Open **Library → Local → Files** and choose one audio or video file. Your original
file stays unchanged.

In **Library**, an unfinished local media item can also be removed with **Remove from
library**. LyricRail asks for confirmation and removes only that one unfinished row;
the source media, lyric sidecar and sibling sections stay untouched. A queued item is
cancelled only if processing has not started. Authenticated `.lrail` packages, cloud
items and active processing never expose this action.

## One timeline for sections

**Trim your song** opens with the whole file selected, so an uncut video can be
edited and added to the queue immediately. To cut it, click the source timeline
once for Start and again for End; the whole-file default is replaced by that pair.
Each later pair creates another section in order. Click a section to select it,
drag its Start/End brackets, enter an exact timestamp or step an edge by a measured
frame. The selected section's exact frame timestamp is shown beside each edge.

**Remove** removes the selected block. The transport has only Play/Pause and source
frame stepping; section frame buttons sit beside Start and End. Moving an edge by a
frame immediately auditions the new beginning or ending of that section.

Invalid time drafts stay visible and block publishing until corrected. Escape in a
time field discards that edit. Up to 128 sections may overlap; **Add N songs to queue**
publishes the valid sections in the chosen order.

## Edit video information

Double-click a block or press **Edit video** to open a separate dialog. The timeline
stays in place behind it, with its preview paused and controls inactive.

The dialog previews only the selected interval. A source interval from 20 to 80
seconds appears as a 60-second video, with a clock and seek control starting at
00:00. Playback and seeking cannot leave that interval; the end frame remains inside
the cut. The dialog contains Video name, Artist, Composer and Lyrics. Section bounds,
selection and management stay on the source timeline.

**Save** applies information and lyrics without changing the cut. **Cancel**, the
close button or Escape discards the information draft and returns to the timeline.
The final queue action is the only action that publishes the sections.

`VideoEditor` is a shared component: callers provide media, playback bounds and
metadata, and receive metadata only when saving. It is independent of the section
picker and can also be used by a future Library edit action.

Lyrics remain exactly as entered. LyricRail never infers, corrects or replaces them.
Empty lyrics leave the Library item waiting for Paste or TXT. A full-source sidecar
is never silently assigned to an extracted section.

## Preview and precision

On the source timeline, seeking outside the selected block browses the source;
selecting a block makes Play/Pause review that section. An edge nudge starts at the
new Start or auditions the final five seconds ending at the new End. Pause holds the
position; Play resumes the selected section.

Exact frame actions use measured timestamps, including variable frame rates. A
pending frame adjustment waits for nearby timestamps; seeking, typing or opening
the information dialog cancels it. If frame inspection fails, playback, seeking and
exact time entry remain available, with **Retry frame details** for another attempt.
No nominal-FPS estimate replaces missing frame evidence.

Playback remains bounded while video startup is pending. A startup that exceeds ten
seconds stops with a retryable error. Pause also cancels a pending start. Unsupported
media can use **Prepare compatible preview**, with cancellation in the preparation UI.

## Keyboard

| Focus | Key | Action |
| --- | --- | --- |
| Source preview | Left / Right | Previous / next measured frame; 10 ms for audio |
| Source preview | I / O | Set Start / End at the playhead |
| Source preview | Space | Play / pause |
| Timeline bracket | Arrow keys | Move that edge by one measured frame or 10 ms and audition it |
| Timeline bracket | Home / End | Try the file start / end while preserving a valid cut |
| Video dialog seek | Arrows, Home / End | Seek only within the selected video |
| Video dialog | Escape | Discard information edits and close only this dialog |
| Either dialog | Tab / Shift+Tab | Move within the active dialog |

Typing in a time, name, metadata or lyrics field does not trigger preview shortcuts.
