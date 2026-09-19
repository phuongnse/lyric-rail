export type ModelCatalogEntry = {
  id: string;
  kind: string;
  task: string;
  installed: boolean;
  sizeBytes?: number | null;
  license?: string | null;
  licenseNote?: string | null;
  location: string;
};

export type PreferencesSnapshot = {
  libraryPath: string;
  cachePath: string;
  defaultLibraryPath: string;
  defaultCachePath: string;
  runtimeIntegrity: string;
  installAllowed: boolean;
  modelCatalog: ModelCatalogEntry[];
};

export type PreferencesDraft = {
  libraryPath: string;
  cachePath: string;
};

export function formatModelSize(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return "Size reported by runtime";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}
