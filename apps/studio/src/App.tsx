import { useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open, save } from "@tauri-apps/plugin-dialog";
import "@lyricrail/ui/theme.css";
import "./App.css";

type StudioStatus = {
  version: string;
  pipelineActive: boolean;
  vaultInitialized: boolean;
  vaultRotationActive: boolean;
  vaultRotationStatus?: RotationStatus;
  vaultRotationError?: string;
  pipelineAvailable: boolean;
  pipelineRoot?: string;
  runtimeError?: string;
  runtimeIntegrity?: "signed-verified" | "development-unverified";
  runtimeKeyId?: string;
  runtimeManifestSha256?: string;
  runtimeFileCount?: number;
};

type WorkspaceVolumeStatus = {
  state: "protected" | "unprotected" | "unknown";
  platform: string;
  path: string;
  volume?: string;
  detail: string;
  enforced: boolean;
};

type RotationStatus = {
  rotationId: string;
  libraryRoot: string;
  packageCount: number;
  dualWrappedPackages: number;
  newOnlyPackages: number;
  currentKeySwitched: boolean;
  packagesVerifiedWithNewKey: boolean;
};

type RotationReport = {
  rotationId: string;
  libraryRoot: string;
  packageCount: number;
  resumed: boolean;
  dualWrappedPackages: number;
  newOnlyPackages: number;
  archivedJournal: string;
};

type RecoveryToolLaunch = {
  processId: number;
  operation: "export" | "verify" | "restore";
};

type LogEvent = { stream: "stdout" | "stderr"; line: string };
type CompletedEvent = { success: boolean; exitCode: number | null };

const stages = [
  "Analyze source",
  "Separate audio",
  "Align lyrics",
  "Classify voices",
  "Build visuals",
  "Prepare playback",
  "Encrypt package",
];

function Icon({ name }: { name: "media" | "lyrics" | "lock" | "audio" | "spark" | "folder" }) {
  const paths = {
    media: <><rect x="3" y="4" width="18" height="16" rx="3"/><path d="m10 9 5 3-5 3Z"/></>,
    lyrics: <><path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/></>,
    lock: <><rect x="4" y="10" width="16" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/></>,
    audio: <><path d="M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4"/></>,
    spark: <><path d="m12 3 1.4 4.6L18 9l-4.6 1.4L12 15l-1.4-4.6L6 9l4.6-1.4Z"/><path d="m18.5 15 .7 2.3 2.3.7-2.3.7-.7 2.3-.7-2.3-2.3-.7 2.3-.7Z"/></>,
    folder: <><path d="M3 7h7l2 2h9v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M3 7V5a2 2 0 0 1 2-2h4l2 2"/></>,
  };
  return <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}

function fileName(path: string) {
  return path.split(/[\\/]/).pop() || path;
}

function App() {
  const [status, setStatus] = useState<StudioStatus | null>(null);
  const [workspaceVolume, setWorkspaceVolume] = useState<WorkspaceVolumeStatus | null>(null);
  const [mediaPath, setMediaPath] = useState("");
  const [lyricsPath, setLyricsPath] = useState("");
  const [title, setTitle] = useState("");
  const [artist, setArtist] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [running, setRunning] = useState(false);
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [error, setError] = useState("");
  const [result, setResult] = useState<CompletedEvent | null>(null);
  const [rotationRoot, setRotationRoot] = useState("");
  const [rotationBusy, setRotationBusy] = useState(false);
  const [rotationMessage, setRotationMessage] = useState("");
  const [recoveryMessage, setRecoveryMessage] = useState("");

  useEffect(() => {
    invoke<StudioStatus>("studio_status")
      .then((value) => {
        setStatus(value);
        setRunning(value.pipelineActive);
        setRotationBusy(value.vaultRotationActive);
        if (value.vaultRotationStatus?.libraryRoot) {
          setRotationRoot(value.vaultRotationStatus.libraryRoot);
        }
      })
      .catch((reason) => setError(String(reason)));
    invoke<WorkspaceVolumeStatus>("workspace_volume_status")
      .then(setWorkspaceVolume)
      .catch((reason) => setWorkspaceVolume({
        state: "unknown",
        platform: "unknown",
        path: "Unavailable",
        detail: String(reason),
        enforced: true,
      }));
    const subscriptions = Promise.all([
      listen<LogEvent>("studio-pipeline-log", ({ payload }) => {
        setLogs((current) => [...current.slice(-399), payload]);
      }),
      listen<CompletedEvent>("studio-pipeline-completed", ({ payload }) => {
        setRunning(false);
        setResult(payload);
        setStatus((current) => current ? { ...current, pipelineActive: false, vaultInitialized: true } : current);
      }),
    ]);
    return () => { subscriptions.then((items) => items.forEach((unlisten) => unlisten())); };
  }, []);

  const activeStage = useMemo(() => {
    const latest = [...logs].reverse().find(({ line }) => line.includes("— running"));
    if (!latest) return running ? "Starting secure pipeline" : "Ready";
    return latest.line.replace(/^.*\]\s*/, "").replace(/\s+— running.*$/, "");
  }, [logs, running]);

  async function chooseMedia() {
    const selected = await open({
      multiple: false,
      directory: false,
      filters: [{ name: "Audio or video", extensions: ["mp4", "mov", "mkv", "webm", "mp3", "m4a", "wav", "flac", "aac", "ogg", "opus"] }],
    });
    if (typeof selected === "string") setMediaPath(selected);
  }

  async function chooseLyrics() {
    const selected = await open({
      multiple: false,
      directory: false,
      filters: [{ name: "Authoritative lyrics", extensions: ["txt"] }],
    });
    if (typeof selected === "string") setLyricsPath(selected);
  }

  async function startProduction() {
    setError("");
    setResult(null);
    setLogs([]);
    try {
      await invoke<number>("start_pipeline", {
        request: {
          mediaPath,
          lyricsPath,
          title: title || null,
          artist: artist || null,
          startSeconds: start ? Number(start) : null,
          endSeconds: end ? Number(end) : null,
        },
      });
      setRunning(true);
    } catch (reason) {
      setError(String(reason));
    }
  }

  async function cancelProduction() {
    try {
      await invoke("cancel_pipeline");
    } catch (reason) {
      setError(String(reason));
    }
  }

  async function chooseRotationLibrary() {
    const selected = await open({ multiple: false, directory: true });
    if (typeof selected === "string") {
      setRotationRoot(selected);
      setRotationMessage("");
    }
  }

  async function rotateLibraryMaster() {
    if (!rotationRoot) return;
    setError("");
    setRotationMessage("");
    setRotationBusy(true);
    try {
      const report = await invoke<RotationReport>("rotate_library_master", {
        libraryRoot: rotationRoot,
      });
      setRotationMessage(`${report.resumed ? "Resumed and completed" : "Completed"}: ${report.packageCount.toLocaleString()} package(s), journal archived.`);
      setStatus((current) => current ? {
        ...current,
        vaultInitialized: true,
        vaultRotationActive: false,
        vaultRotationStatus: undefined,
        vaultRotationError: undefined,
      } : current);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setRotationBusy(false);
    }
  }

  async function exportRecoveryBundle() {
    const outputPath = await save({
      defaultPath: "LyricRail-library.lrail-recovery",
      filters: [{ name: "LyricRail recovery bundle", extensions: ["lrail-recovery"] }],
    });
    if (!outputPath) return;
    setError("");
    try {
      const launch = await invoke<RecoveryToolLaunch>("launch_recovery_export", { outputPath });
      setRecoveryMessage(`Native export window opened (process ${launch.processId}).`);
    } catch (reason) {
      setError(String(reason));
    }
  }

  async function verifyRecoveryBundle() {
    const inputPath = await open({
      multiple: false,
      directory: false,
      filters: [{ name: "LyricRail recovery bundle", extensions: ["lrail-recovery"] }],
    });
    if (typeof inputPath !== "string") return;
    setError("");
    try {
      const launch = await invoke<RecoveryToolLaunch>("launch_recovery_verify", { inputPath });
      setRecoveryMessage(`Native verification window opened (process ${launch.processId}).`);
    } catch (reason) {
      setError(String(reason));
    }
  }

  async function restoreRecoveryBundle() {
    const inputPath = await open({
      multiple: false,
      directory: false,
      filters: [{ name: "LyricRail recovery bundle", extensions: ["lrail-recovery"] }],
    });
    if (typeof inputPath !== "string") return;
    const libraryRoot = await open({ multiple: false, directory: true });
    if (typeof libraryRoot !== "string") return;
    setError("");
    try {
      const launch = await invoke<RecoveryToolLaunch>("launch_recovery_restore", { inputPath, libraryRoot });
      setRecoveryMessage(`Native restore window opened (process ${launch.processId}).`);
    } catch (reason) {
      setError(String(reason));
    }
  }

  const workspaceReady = Boolean(
    workspaceVolume
    && (!workspaceVolume.enforced || workspaceVolume.state === "protected"),
  );
  const ready = Boolean(
    mediaPath
    && lyricsPath
    && !running
    && status?.pipelineAvailable
    && workspaceReady,
  );

  return (
    <div className="studio-shell">
      <aside className="studio-sidebar">
        <div className="studio-brand">
          <div className="brand-mark"><span /></div>
          <div><strong>LyricRail</strong><small>STUDIO</small></div>
        </div>
        <nav aria-label="Studio sections">
          <button className="nav-item active"><span className="nav-glyph">＋</span>New production</button>
          <button className="nav-item"><span className="nav-glyph">▤</span>Jobs</button>
          <button className="nav-item"><span className="nav-glyph">◇</span>Packages</button>
        </nav>
        <div className="sidebar-spacer" />
        <div className="vault-card">
          <div className="vault-icon"><Icon name="lock" /></div>
          <div><strong>Device vault</strong><span>{status?.vaultInitialized ? "Windows vault active" : "Initializes on export"}</span></div>
          <i className={status?.vaultInitialized ? "ok" : "idle"} />
        </div>
        <div className="sidebar-version">LyricRail Studio {status?.version ?? "0.8"}</div>
      </aside>

      <main className="studio-main">
        <header className="studio-header">
          <div>
            <p className="eyebrow">Production workspace</p>
            <h1>Build a karaoke package</h1>
            <p>Source-quality media, two audio tracks, dynamic lyrics, sealed as <code>.lrail</code>.</p>
          </div>
          <div className={`pill ${running ? "running-pill" : ""}`}><span className="status-dot" />{activeStage}</div>
        </header>

        <div className="studio-grid">
          <section className="card production-card">
            <div className="section-heading">
              <div><span>01</span><div><h2>Source material</h2><p>Choose the exact media and authoritative lyrics.</p></div></div>
              <div className="pill">Local only</div>
            </div>
            <div className="source-row">
              <button className={`source-picker ${mediaPath ? "selected" : ""}`} onClick={chooseMedia} disabled={running}>
                <div className="picker-icon"><Icon name="media" /></div>
                <span>{mediaPath ? "Media ready" : "Audio or video"}</span>
                <strong>{mediaPath ? fileName(mediaPath) : "Select source file"}</strong>
                <small>{mediaPath ? mediaPath : "MP4, MKV, FLAC, WAV, M4A and more"}</small>
              </button>
              <button className={`source-picker ${lyricsPath ? "selected" : ""}`} onClick={chooseLyrics} disabled={running}>
                <div className="picker-icon lyric"><Icon name="lyrics" /></div>
                <span>{lyricsPath ? "Lyrics ready" : "Exact lyrics"}</span>
                <strong>{lyricsPath ? fileName(lyricsPath) : "Select UTF-8 lyrics"}</strong>
                <small>{lyricsPath ? lyricsPath : "One semantic phrase per line"}</small>
              </button>
            </div>

            <div className="section-divider" />
            <div className="section-heading compact">
              <div><span>02</span><div><h2>Identity and range</h2><p>Optional overrides are written into encrypted metadata.</p></div></div>
            </div>
            <div className="field-grid">
              <label><span>Song title</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Infer from source" disabled={running} /></label>
              <label><span>Reference artist</span><input value={artist} onChange={(event) => setArtist(event.target.value)} placeholder="Infer from source" disabled={running} /></label>
              <label><span>Start (seconds)</span><input type="number" min="0" step="0.1" value={start} onChange={(event) => setStart(event.target.value)} placeholder="0" disabled={running} /></label>
              <label><span>End (seconds)</span><input type="number" min="0" step="0.1" value={end} onChange={(event) => setEnd(event.target.value)} placeholder="Full source" disabled={running} /></label>
            </div>
          </section>

          <aside className="output-column">
            <section className="card output-card">
              <p className="eyebrow">Output profile</p>
              <h2>Private master</h2>
              <div className="format-banner"><span>.lrail</span><div><strong>Authenticated package</strong><small>XChaCha20-Poly1305 · random access</small></div></div>
              <ul className="feature-list">
                <li><span className="feature-icon"><Icon name="audio" /></span><div><strong>Two audio tracks</strong><small>Karaoke + Original Reference</small></div><b>2</b></li>
                <li><span className="feature-icon cyan"><Icon name="spark" /></span><div><strong>Dynamic lyrics</strong><small>Word timing and singer roles</small></div><b>ON</b></li>
                <li><span className="feature-icon blue"><Icon name="media" /></span><div><strong>Source preserving</strong><small>Video stream-copy when safe</small></div><b>AUTO</b></li>
                <li><span className="feature-icon green"><Icon name="lock" /></span><div><strong>Device-bound key</strong><small>Never exposed to frontend</small></div><b>OS</b></li>
              </ul>
              <div className="rights-note"><Icon name="lock" /><p><strong>No rights claim is added.</strong><span>Source links and a private-use rights notice stay inside the encrypted package.</span></p></div>
              {status && !status.pipelineAvailable && <div className="runtime-note"><Icon name="folder" /><p><strong>Production runtime not connected.</strong><span>{status.runtimeError} Install or select a verified runtime with LYRICRAIL_HOME.</span></p></div>}
              {status?.pipelineAvailable && status.runtimeIntegrity === "signed-verified" && <div className="runtime-note verified"><Icon name="lock" /><p><strong>Signed runtime verified.</strong><span>{status.runtimeFileCount?.toLocaleString()} files match manifest {status.runtimeManifestSha256?.slice(0, 12)}… · key {status.runtimeKeyId?.slice(0, 12)}…</span></p></div>}
              {status?.pipelineAvailable && status.runtimeIntegrity === "development-unverified" && <div className="runtime-note development"><Icon name="folder" /><p><strong>Development runtime — not release verified.</strong><span>Allowed only by this debug build. Release Studio requires an Ed25519-signed exhaustive manifest.</span></p></div>}
              {workspaceVolume && <div className={`runtime-note ${workspaceVolume.state === "protected" ? "verified" : workspaceVolume.enforced ? "" : "development"}`}><Icon name={workspaceVolume.state === "protected" ? "lock" : "folder"} /><p><strong>{workspaceVolume.state === "protected" ? "Encrypted workspace verified." : workspaceVolume.state === "unprotected" ? "Workspace volume is not protected." : "Workspace encryption could not be verified."}</strong><span>{workspaceVolume.detail} {workspaceVolume.volume ? `Volume ${workspaceVolume.volume}. ` : ""}Clear processing path: {workspaceVolume.path}{workspaceVolume.enforced ? " Release production is blocked until this is protected." : " Debug builds report this without blocking production."}</span></p></div>}
              <div className="plaintext-note"><Icon name="lock" /><p><strong>Release-candidate protection, not commercial DRM.</strong><span>An administrator, debugger, screen recorder, or compromised operating system can capture decoded playback.</span></p></div>
              <div className="plaintext-note"><Icon name="folder" /><p><strong>Processing files are temporarily cleartext.</strong><span>After the .lrail package passes a full verification, Studio removes only this job’s cleartext workspace. This is not SSD secure erasure.</span></p></div>
              <div className="rotation-panel">
                <div className="rotation-heading"><Icon name="lock" /><div><strong>Library master rotation</strong><span>Transactional dual-wrap → key switch → new-only cleanup. Media ciphertext is copied byte-for-byte.</span></div></div>
                <button className="rotation-path" onClick={chooseRotationLibrary} disabled={running || rotationBusy}>
                  <span>{rotationRoot ? fileName(rotationRoot) : "Choose package library"}</span>
                  <small>{rotationRoot || "Select the folder containing every .lrail package bound to this device key."}</small>
                </button>
                {status?.vaultRotationStatus && <p className="rotation-progress">Resume available · {status.vaultRotationStatus.newOnlyPackages}/{status.vaultRotationStatus.packageCount} finalized</p>}
                {status?.vaultRotationError && <p className="rotation-warning">Journal needs attention: {status.vaultRotationError}</p>}
                {rotationMessage && <p className="rotation-success">{rotationMessage}</p>}
                <button className="button rotation-action" onClick={rotateLibraryMaster} disabled={!rotationRoot || running || rotationBusy}>
                  {rotationBusy ? "Rotating and verifying…" : status?.vaultRotationStatus ? "Resume key rotation" : "Rotate library key"}
                </button>
                <small className="rotation-footnote">Keep Studio open when practical. If interrupted, select the same folder and Studio resumes from its synced journal.</small>
              </div>
              <div className="recovery-panel">
                <div className="rotation-heading"><Icon name="lock" /><div><strong>Offline recovery bundle</strong><span>Export, authenticate, or restore the device key in a separate native console. The passphrase never enters this WebView.</span></div></div>
                <div className="recovery-actions">
                  <button className="button" onClick={exportRecoveryBundle} disabled={running || rotationBusy || !status?.pipelineAvailable}>Export</button>
                  <button className="button" onClick={verifyRecoveryBundle} disabled={running || rotationBusy || !status?.pipelineAvailable}>Verify</button>
                  <button className="button" onClick={restoreRecoveryBundle} disabled={running || rotationBusy || !status?.pipelineAvailable}>Restore</button>
                </div>
                {recoveryMessage && <p className="rotation-success">{recoveryMessage}</p>}
                <small className="rotation-footnote">Restore refuses an empty library, a conflicting current key, active rotation, wrong passphrase, or any package that does not authenticate.</small>
              </div>
              {running ? (
                <button className="button button-danger export-button" onClick={cancelProduction}>Stop production</button>
              ) : (
                <button className="button button-primary export-button" onClick={startProduction} disabled={!ready}><Icon name="spark" />Build private package</button>
              )}
              {!ready && !running && <p className="ready-hint">{status && !status.pipelineAvailable ? "Connect the verified production runtime to continue." : !workspaceReady ? "Move the Studio data directory to a confirmed encrypted volume to continue." : "Select both source media and lyrics to continue."}</p>}
            </section>
          </aside>
        </div>

        {(running || logs.length > 0 || error || result) && (
          <section className="card activity-card">
            <div className="activity-header"><div><h2>Production activity</h2><p>{result ? (result.success ? "Package verified and ready" : `Pipeline stopped with exit code ${result.exitCode ?? "unknown"}`) : activeStage}</p></div><span className={`result-badge ${result?.success ? "success" : error || result ? "failure" : "working"}`}>{result?.success ? "Verified" : error || result ? "Attention" : "Working"}</span></div>
            <div className="stage-rail">{stages.map((stage, index) => <div key={stage} className={activeStage.toLowerCase().includes(stage.split(" ")[0].toLowerCase()) ? "current" : result?.success ? "done" : ""}><span>{index + 1}</span><small>{stage}</small></div>)}</div>
            {error && <div className="error-banner">{error}</div>}
            <pre className="log-view">{logs.length ? logs.slice(-80).map(({ line }) => line).join("\n") : "Waiting for the pipeline…"}</pre>
          </section>
        )}
      </main>
    </div>
  );
}

export default App;
