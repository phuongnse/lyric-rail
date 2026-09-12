import { useRef } from "react";
import { IconButton } from "./Icon";
import { useFocusContainment } from "./focus";
import { formatModelSize, type ModelCatalogEntry, type PreferencesDraft, type PreferencesSnapshot } from "./settings";

function modelLabel(model: ModelCatalogEntry): string {
  return model.id.replace(/[-_]+/g, " ");
}

export function SettingsDialog({
  open,
  snapshot,
  draft,
  busy,
  installing,
  onClose,
  onSave,
  onChooseLibrary,
  onChooseCache,
  onResetPath,
  onInstallModels,
  onOpenActivity,
}: {
  open: boolean;
  snapshot?: PreferencesSnapshot;
  draft: PreferencesDraft;
  busy: boolean;
  installing: boolean;
  onClose: () => void;
  onSave: () => void;
  onChooseLibrary: () => void;
  onChooseCache: () => void;
  onResetPath: (key: keyof PreferencesDraft) => void;
  onInstallModels: () => void;
  onOpenActivity: () => void;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  const first = useRef<HTMLButtonElement>(null);
  useFocusContainment(open, dialog, first);
  if (!open) return null;
  const models = snapshot?.modelCatalog ?? [];
  const missing = models.filter((model) => !model.installed).length;
  return (
    <div
      className="modal-layer"
      role="dialog"
      aria-modal="true"
      aria-labelledby="settings-title"
      onClick={(event) => {
        if (event.target === event.currentTarget && !busy) {
          onClose();
        }
      }}
    >
      <div className="settings-dialog panel" ref={dialog} tabIndex={-1}>
        <header>
          <div><p className="eyebrow">Application</p><h2 id="settings-title">Settings</h2></div>
          <IconButton className="dialog-close" icon="close" label="Close Settings" onClick={onClose} />
        </header>
        <p className="settings-intro">Keep storage, model setup and long-running work in one predictable place.</p>

        <section className="settings-section" aria-labelledby="settings-storage-title">
          <div className="settings-section-heading"><div><h3 id="settings-storage-title">Storage</h3><span>Default locations for Library and application cache.</span></div></div>
          <label className="settings-path"><span>Library location</span><div><input aria-label="Library location" value={draft.libraryPath} readOnly /><button ref={first} onClick={onChooseLibrary} disabled={busy}>Choose</button><button onClick={() => onResetPath("libraryPath")} disabled={busy}>Default</button></div></label>
          <label className="settings-path"><span>Cache location</span><div><input aria-label="Cache location" value={draft.cachePath} readOnly /><button onClick={onChooseCache} disabled={busy}>Choose</button><button onClick={() => onResetPath("cachePath")} disabled={busy}>Default</button></div></label>
          <p className="settings-note">Changing a location affects future application work. Existing media and model files are never moved automatically.</p>
        </section>

        <section className="settings-section" aria-labelledby="settings-models-title">
          <div className="settings-section-heading"><div><h3 id="settings-models-title">Models</h3><span>{missing ? `${missing} model${missing === 1 ? "" : "s"} need setup.` : "All declared processing models are ready."}</span></div><span className={`settings-runtime ${snapshot?.runtimeIntegrity || "unavailable"}`}>{snapshot?.runtimeIntegrity || "runtime unavailable"}</span></div>
          <div className="model-list">
            {models.length === 0 ? <p className="settings-note">Model catalog is unavailable. Open Activity or Issues for the next step.</p> : models.map((model) => (
              <article className="model-row" key={model.id}>
                <div><strong>{modelLabel(model)}</strong><span>{model.task}</span><small>{formatModelSize(model.sizeBytes)} · {model.location}</small></div>
                <span className={`model-state ${model.installed ? "installed" : "missing"}`}>{model.installed ? "Installed" : "Not installed"}</span>
              </article>
            ))}
          </div>
          {missing > 0 && snapshot?.installAllowed && <div className="settings-model-actions"><button className="primary" onClick={onInstallModels} disabled={busy || installing}>{installing ? "Installing models…" : "Install missing models"}</button><button onClick={onOpenActivity} disabled={busy}>View Activity</button></div>}
          {missing > 0 && !snapshot?.installAllowed && <p className="settings-note">This runtime is immutable. Install a complete verified runtime pack to add missing models.</p>}
        </section>

        <footer><button onClick={onClose} disabled={busy}>Cancel</button><button className="primary" onClick={onSave} disabled={busy}>Save settings</button></footer>
      </div>
    </div>
  );
}

export default SettingsDialog;
