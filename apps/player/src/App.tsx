import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import "@lyricrail/ui/theme.css";
import "./App.css";
import { playbackStartTime } from "./playback";

type AudioTrack = {
  id: "karaoke" | "original-reference";
  name: string;
  url: string;
  default: boolean;
};

type PackageMetadata = {
  title?: string;
  referenceArtist?: string;
  description?: string;
  language?: string;
  tags?: string[];
  sources?: Array<{
    kind?: string;
    fileName?: string;
    url?: string;
    webpageUrl?: string;
    provider?: string;
  }>;
  rights?: {
    ownershipClaimed?: boolean;
    licenseProvided?: boolean;
    intendedUse?: string;
    notice?: string;
  };
  lyrics?: { lineCount?: number; wordCount?: number; dynamicRendering?: boolean };
};

type Syllable = {
  text: string;
  start?: number;
  end?: number;
  visualStart?: number;
  visualEnd?: number;
  role?: string;
};

type LyricLine = {
  text: string;
  role?: string;
  slot?: "top" | "bottom";
  syllables?: Syllable[];
};

type RenderEvent = {
  lineIndex?: number;
  slot?: "top" | "bottom";
  role?: string;
  displayStart: number;
  displayEnd: number;
  vocalStart?: number;
  vocalEnd?: number;
  showRoleCue?: boolean;
  line: LyricLine;
};

type OpenPackageResult = {
  packageId: string;
  minimumPlayerVersion: string;
  metadata: PackageMetadata;
  assets: unknown;
  lyrics: { lines?: LyricLine[] };
  renderPlan: { events?: RenderEvent[] };
  media: {
    videoUrl: string;
    audioTracks: AudioTrack[];
  };
};

type PlayerStatus = {
  version: string;
  packageOpen: boolean;
  vaultAvailable: boolean;
};

type LibraryPackage = {
  path: string;
  packageId?: string;
  title: string;
  referenceArtist?: string;
  valid: boolean;
  error?: string;
};

type PendingTrackChange = { time: number; resume: boolean } | null;

function Icon({
  name,
}: {
  name:
    | "folder"
    | "play"
    | "pause"
    | "volume"
    | "lock"
    | "info"
    | "music"
    | "list";
}) {
  const paths = {
    folder: <><path d="M3 7h7l2 2h9v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M3 7V5a2 2 0 0 1 2-2h4l2 2"/></>,
    play: <path d="m8 5 11 7-11 7Z"/>,
    pause: <><path d="M8 5v14M16 5v14"/></>,
    volume: <><path d="M5 10v4h4l5 4V6l-5 4Z"/><path d="M17 9a4 4 0 0 1 0 6M19 6a8 8 0 0 1 0 12"/></>,
    lock: <><rect x="4" y="10" width="16" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/></>,
    info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></>,
    music: <><path d="M9 18V6l10-2v12"/><circle cx="6" cy="18" r="3"/><circle cx="16" cy="16" r="3"/></>,
    list: <><path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6h.01M4 12h.01M4 18h.01"/></>,
  };
  return <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}

function formatTime(seconds: number) {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const whole = Math.floor(seconds);
  return Math.floor(whole / 60) + ":" + String(whole % 60).padStart(2, "0");
}

function roleLabel(role?: string) {
  if (role === "female") return "Nữ";
  if (role === "duet") return "Song ca";
  return "Nam";
}

function LyricEventView({ event, time }: { event: RenderEvent; time: number }) {
  const line = event.line;
  const syllables = line.syllables ?? [];
  const role = line.role || event.role || "male";
  return (
    <div className={"lyric-line role-" + role}>
      {event.showRoleCue && <span className="role-cue">{roleLabel(role)}</span>}
      <div className="lyric-words">
        {syllables.length
          ? syllables.map((syllable, index) => {
              const start = syllable.visualStart ?? syllable.start ?? event.vocalStart ?? 0;
              const end = Math.max(
                start + 0.01,
                syllable.visualEnd ?? syllable.end ?? event.vocalEnd ?? start + 0.01,
              );
              const fill = Math.max(0, Math.min(1, (time - start) / (end - start)));
              return (
                <span
                  className="lyric-syllable"
                  key={(event.lineIndex ?? 0) + "-" + index}
                  style={{ "--fill": fill * 100 + "%" } as CSSProperties}
                >
                  {syllable.text}
                </span>
              );
            })
          : <span className="lyric-syllable">{line.text}</span>}
      </div>
    </div>
  );
}

function App() {
  const [status, setStatus] = useState<PlayerStatus | null>(null);
  const [pkg, setPackage] = useState<OpenPackageResult | null>(null);
  const [packagePath, setPackagePath] = useState("");
  const [trackId, setTrackId] = useState<AudioTrack["id"]>("karaoke");
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(0.9);
  const [detailsOpen, setDetailsOpen] = useState(true);
  const [activeView, setActiveView] = useState<"player" | "library">("player");
  const [libraryDirectory, setLibraryDirectory] = useState("");
  const [libraryPackages, setLibraryPackages] = useState<LibraryPackage[]>([]);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [error, setError] = useState("");
  const videoRef = useRef<HTMLVideoElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const pendingTrackChange = useRef<PendingTrackChange>(null);

  const track = useMemo(
    () => pkg?.media.audioTracks.find((candidate) => candidate.id === trackId)
      ?? pkg?.media.audioTracks[0],
    [pkg, trackId],
  );
  const events = pkg?.renderPlan.events ?? [];
  const visibleEvents = useMemo(
    () => events
      .filter((event) => time >= event.displayStart && time <= event.displayEnd)
      .sort((left, right) => {
        if (left.slot === right.slot) return left.displayStart - right.displayStart;
        return left.slot === "top" ? -1 : 1;
      })
      .slice(0, 2),
    [events, time],
  );

  useEffect(() => {
    let disposed = false;
    let stopListening: (() => void) | undefined;

    invoke<PlayerStatus>("player_status")
      .then(setStatus)
      .catch((reason) => setError(String(reason)));

    invoke<string | null>("take_startup_package")
      .then((path) => {
        if (!disposed && path) void loadPackage(path);
      })
      .catch((reason) => setError(String(reason)));

    void listen<string>("player-open-package", ({ payload }) => {
      if (!disposed) void loadPackage(payload);
    }).then((unlisten) => {
      if (disposed) unlisten();
      else stopListening = unlisten;
    }).catch((reason) => setError(String(reason)));

    return () => {
      disposed = true;
      stopListening?.();
    };
  }, []);

  useEffect(() => {
    let frame = 0;
    const tick = () => {
      const audio = audioRef.current;
      const video = videoRef.current;
      if (audio) {
        setTime(audio.currentTime);
        if (
          video
          && !audio.paused
          && video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA
          && Math.abs(video.currentTime - audio.currentTime) > 0.12
        ) {
          video.currentTime = audio.currentTime;
        }
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    const handleKeyboard = (event: KeyboardEvent) => {
      if (!pkg || event.target instanceof HTMLInputElement) return;
      if (event.code === "Space") {
        event.preventDefault();
        void togglePlayback();
      } else if (event.code === "ArrowLeft") {
        event.preventDefault();
        seekTo(Math.max(0, time - 5));
      } else if (event.code === "ArrowRight") {
        event.preventDefault();
        seekTo(Math.min(duration, time + 5));
      }
    };
    window.addEventListener("keydown", handleKeyboard);
    return () => window.removeEventListener("keydown", handleKeyboard);
  });

  async function choosePackage() {
    setError("");
    const selected = await open({
      multiple: false,
      directory: false,
      filters: [{ name: "LyricRail Karaoke Package", extensions: ["lrail"] }],
    });
    if (typeof selected !== "string") return;
    await loadPackage(selected);
  }

  async function loadPackage(selected: string) {
    setError("");
    try {
      const opened = await invoke<OpenPackageResult>("open_package", { path: selected });
      const defaultTrack = opened.media.audioTracks.find((candidate) => candidate.default)
        ?? opened.media.audioTracks[0];
      setPackage(opened);
      setPackagePath(selected);
      setTrackId(defaultTrack.id);
      setTime(0);
      setDuration(0);
      setPlaying(false);
      setActiveView("player");
      setStatus((current) => current
        ? { ...current, packageOpen: true, vaultAvailable: true }
        : current);
    } catch (reason) {
      setError(String(reason));
    }
  }

  async function chooseLibrary() {
    setError("");
    const selected = await open({ multiple: false, directory: true });
    if (typeof selected !== "string") return;
    setLibraryLoading(true);
    try {
      const packages = await invoke<LibraryPackage[]>("scan_library", { root: selected });
      setLibraryDirectory(selected);
      setLibraryPackages(packages);
      setActiveView("library");
    } catch (reason) {
      setError(String(reason));
    } finally {
      setLibraryLoading(false);
    }
  }

  async function togglePlayback() {
    const audio = audioRef.current;
    const video = videoRef.current;
    if (!audio || !video) return;
    if (audio.paused) {
      setError("");
      try {
        const startTime = playbackStartTime(
          audio.currentTime,
          audio.duration,
          audio.ended,
        );
        audio.currentTime = startTime;
        video.currentTime = startTime;
        setTime(startTime);
        await Promise.all([video.play(), audio.play()]);
        setPlaying(true);
      } catch (reason) {
        audio.pause();
        video.pause();
        setPlaying(false);
        setError("Không thể bắt đầu phát media: " + String(reason));
      }
    } else {
      audio.pause();
      video.pause();
      setPlaying(false);
    }
  }

  function seekTo(value: number) {
    const next = Math.max(0, Math.min(duration || value, value));
    if (audioRef.current) audioRef.current.currentTime = next;
    if (videoRef.current) videoRef.current.currentTime = next;
    setTime(next);
  }

  function changeTrack(nextId: AudioTrack["id"]) {
    const audio = audioRef.current;
    if (!audio || nextId === trackId) return;
    pendingTrackChange.current = { time: audio.currentTime, resume: !audio.paused };
    audio.pause();
    setPlaying(false);
    setTrackId(nextId);
  }

  async function resumeAfterTrackChange() {
    const pending = pendingTrackChange.current;
    const audio = audioRef.current;
    const video = videoRef.current;
    if (!pending || !audio || !video) return;
    pendingTrackChange.current = null;
    audio.currentTime = pending.time;
    video.currentTime = pending.time;
    if (pending.resume) {
      try {
        await Promise.all([video.play(), audio.play()]);
        setPlaying(true);
      } catch (reason) {
        setError("Không thể tiếp tục sau khi đổi audio: " + String(reason));
      }
    }
  }

  function changeVolume(next: number) {
    setVolume(next);
    if (audioRef.current) audioRef.current.volume = next;
  }

  function handleControlKey(
    event: ReactKeyboardEvent<HTMLDivElement>,
    action: () => void,
  ) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      action();
    }
  }

  const metadata = pkg?.metadata;
  const displayTitle = metadata?.title || (pkg ? "Untitled karaoke" : "No package open");
  const displayArtist = metadata?.referenceArtist || "Private LyricRail package";
  const source = metadata?.sources?.[0];
  const sourceLabel = source?.webpageUrl || source?.url || source?.fileName || "";

  return (
    <div className={"player-shell " + (detailsOpen ? "" : "details-collapsed")}>
      <header className="player-topbar">
        <div className="player-brand">
          <div className="brand-mark"><span /></div>
          <div><strong>LyricRail</strong><small>PLAYER</small></div>
        </div>
        <div className="now-playing">
          <span className="cover-mini"><Icon name="music" /></span>
          <div><strong>{displayTitle}</strong><small>{displayArtist}</small></div>
        </div>
        <div className="top-actions">
          <span className={"pill vault-pill " + (status?.vaultAvailable ? "ready" : "")}>
            <span className="status-dot" />
            {status?.vaultAvailable ? "Device vault" : "Vault unavailable"}
          </span>
          <button className="button" onClick={choosePackage}><Icon name="folder" />Open .lrail</button>
          <button
            className={"icon-button " + (detailsOpen ? "active" : "")}
            onClick={() => setDetailsOpen((value) => !value)}
            aria-label="Toggle package details"
          >
            <Icon name="info" />
          </button>
        </div>
      </header>

      <aside className="library-rail">
        <p className="eyebrow">Library</p>
        <button
          className={"library-item " + (activeView === "player" ? "active" : "")}
          onClick={() => setActiveView("player")}
        ><Icon name="music" /><span>Now playing</span></button>
        <button
          className={"library-item " + (activeView === "library" ? "active" : "")}
          onClick={() => libraryDirectory ? setActiveView("library") : void chooseLibrary()}
        ><Icon name="list" /><span>Private packages</span></button>
        <div className="library-spacer" />
        <div className="security-note"><Icon name="lock" /><div><strong>Authenticated playback</strong><span>No clear media file. Admins, debuggers, and recording remain outside this protection.</span></div></div>
        <small className="player-version">Player {status?.version ?? "0.8.0"}</small>
      </aside>

      <main className="player-main">
        {activeView === "library" ? (
          <section className="library-browser card">
            <header>
              <div>
                <p className="eyebrow">Authenticated local library</p>
                <h1>Private packages</h1>
                <p>{libraryDirectory || "Choose a directory containing .lrail packages."}</p>
              </div>
              <button className="button" onClick={() => void chooseLibrary()} disabled={libraryLoading}>
                <Icon name="folder" />{libraryLoading ? "Scanning…" : "Choose folder"}
              </button>
            </header>
            <div className="library-summary">
              <span><strong>{libraryPackages.filter((item) => item.valid).length}</strong> authenticated</span>
              <span><strong>{libraryPackages.filter((item) => !item.valid).length}</strong> unreadable</span>
              <small>Read-only scan · no library database</small>
            </div>
            <div className="package-grid">
              {libraryPackages.map((item) => (
                <button
                  className={"package-card " + (item.valid ? "" : "invalid")}
                  key={item.path}
                  onClick={() => item.valid && void loadPackage(item.path)}
                  disabled={!item.valid}
                  title={item.error || item.path}
                >
                  <span className="package-cover"><Icon name={item.valid ? "music" : "lock"} /></span>
                  <span className="package-copy">
                    <strong>{item.title}</strong>
                    <small>{item.referenceArtist || (item.valid ? "Private karaoke package" : "Authentication failed")}</small>
                    <code>{item.packageId || item.path}</code>
                  </span>
                </button>
              ))}
              {!libraryLoading && libraryPackages.length === 0 && (
                <div className="library-empty"><Icon name="list" /><strong>No .lrail packages found</strong><span>The scan includes four directory levels and never follows symlinks.</span></div>
              )}
            </div>
          </section>
        ) : !pkg ? (
          <section className="empty-player card">
            <div className="empty-mark"><div className="brand-mark"><span /></div></div>
            <p className="eyebrow">Private karaoke player</p>
            <h1>Open an authenticated package</h1>
            <p>LyricRail verifies the manifest and every media chunk before it is played. Keys stay in the native core.</p>
            <button className="button button-primary" onClick={choosePackage}><Icon name="folder" />Choose .lrail package</button>
            <div className="empty-features">
              <span><b>2</b> switchable audio tracks</span>
              <span><b>✓</b> dynamic word timing</span>
              <span><b>0</b> clear temporary files</span>
            </div>
          </section>
        ) : (
          <>
            <section className="video-stage card">
              <video
                ref={videoRef}
                src={pkg.media.videoUrl}
                aria-hidden="true"
                tabIndex={-1}
                muted
                playsInline
                preload="metadata"
                onClick={() => void togglePlayback()}
                onDoubleClick={() => document.fullscreenElement
                  ? void document.exitFullscreen()
                  : void document.querySelector(".video-stage")?.requestFullscreen()}
              />
              <audio
                ref={audioRef}
                src={track?.url}
                preload="metadata"
                onLoadedMetadata={(event) => {
                  setDuration(Number.isFinite(event.currentTarget.duration)
                    ? event.currentTarget.duration
                    : 0);
                  event.currentTarget.volume = volume;
                  void resumeAfterTrackChange();
                }}
                onEnded={() => {
                  videoRef.current?.pause();
                  setPlaying(false);
                }}
                onError={() => setError("Không thể đọc audio đã mã hóa trong package.")}
              />
              <div className="stage-shade" />
              <div className="stage-badges">
                <span className="pill"><Icon name="lock" />Authenticated stream</span>
                <span className="pill">Dynamic lyrics</span>
              </div>
              <div className="lyric-overlay" aria-live="off">
                {visibleEvents.length
                  ? visibleEvents.map((event, index) => (
                      <LyricEventView
                        key={(event.lineIndex ?? index) + "-" + event.displayStart}
                        event={event}
                        time={time}
                      />
                    ))
                  : <div className="instrumental-cue">♪</div>}
              </div>
              <button className="stage-play" onClick={() => void togglePlayback()} aria-label={playing ? "Pause" : "Play"}>
                <Icon name={playing ? "pause" : "play"} />
              </button>
            </section>

            <section className="transport card">
              <div className="timeline">
                <span>{formatTime(time)}</span>
                <input
                  type="range"
                  min="0"
                  max={duration || 1}
                  step="0.01"
                  value={Math.min(time, duration || 1)}
                  onChange={(event) => seekTo(Number(event.target.value))}
                  aria-label="Playback position"
                  style={{ "--progress": (duration ? (time / duration) * 100 : 0) + "%" } as CSSProperties}
                />
                <span>{formatTime(duration)}</span>
              </div>
              <div className="transport-row">
                <div className="track-switch" role="radiogroup" aria-label="Audio track">
                  {pkg.media.audioTracks.map((candidate) => (
                    <div
                      key={candidate.id}
                      className={"track-option " + (trackId === candidate.id ? "active" : "")}
                      role="radio"
                      aria-checked={trackId === candidate.id}
                      tabIndex={0}
                      onClick={() => changeTrack(candidate.id)}
                      onKeyDown={(event) => handleControlKey(event, () => changeTrack(candidate.id))}
                    >
                      <span>{candidate.id === "karaoke" ? "K" : "O"}</span>
                      <div><strong>{candidate.name}</strong><small>{candidate.id === "karaoke" ? "Instrumental" : "Reference vocal"}</small></div>
                    </div>
                  ))}
                </div>
                <button className="main-play" onClick={() => void togglePlayback()} aria-label={playing ? "Pause" : "Play"}>
                  <Icon name={playing ? "pause" : "play"} />
                </button>
                <div className="volume-control">
                  <Icon name="volume" />
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.01"
                    value={volume}
                    onChange={(event) => changeVolume(Number(event.target.value))}
                    aria-label="Volume"
                  />
                  <span>{Math.round(volume * 100)}%</span>
                </div>
              </div>
            </section>
          </>
        )}
        {error && <div className="player-error">{error}</div>}
      </main>

      {detailsOpen && (
        <aside className="details-panel">
          <div className="details-heading"><div><p className="eyebrow">Encrypted metadata</p><h2>Package details</h2></div><Icon name="lock" /></div>
          {pkg ? (
            <div className="details-scroll">
              <section>
                <span className="detail-label">Title</span>
                <strong className="detail-title">{displayTitle}</strong>
                <span className="detail-artist">{displayArtist}</span>
              </section>
              {metadata?.description && (
                <section><span className="detail-label">Description</span><p>{metadata.description}</p></section>
              )}
              <section className="detail-grid">
                <div><span className="detail-label">Lyrics</span><strong>{metadata?.lyrics?.lineCount ?? "—"} lines</strong></div>
                <div><span className="detail-label">Language</span><strong>{(metadata?.language || "vi").toUpperCase()}</strong></div>
                <div><span className="detail-label">Audio</span><strong>{pkg.media.audioTracks.length} tracks</strong></div>
                <div><span className="detail-label">Format</span><strong>LRAIL v1</strong></div>
              </section>
              <section>
                <span className="detail-label">Source attribution</span>
                {sourceLabel
                  ? <div className="source-box"><span>{source?.provider || source?.kind || "Source"}</span><p>{sourceLabel}</p></div>
                  : <p className="muted">No source reference was supplied.</p>}
              </section>
              <section>
                <span className="detail-label">Rights statement</span>
                <div className="rights-box"><Icon name="info" /><p><strong>Private personal use</strong><span>{metadata?.rights?.notice || "No copyright or license claim is included."}</span></p></div>
              </section>
              <section>
                <span className="detail-label">Package identity</span>
                <code className="package-id">{pkg.packageId}</code>
                <small className="package-path">{packagePath}</small>
              </section>
            </div>
          ) : (
            <div className="details-empty"><Icon name="info" /><p>Metadata becomes available only after the package manifest is authenticated.</p></div>
          )}
        </aside>
      )}
    </div>
  );
}

export default App;
