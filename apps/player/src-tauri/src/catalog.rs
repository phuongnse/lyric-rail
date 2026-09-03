use std::{
    cmp::Reverse,
    collections::{HashMap, HashSet},
    fs,
    io::Write,
    path::{Path, PathBuf},
};

use keyring::v1::{Entry, Error as KeyringError};
use lrail_format::{LockedSecret, open_library_record, seal_library_record};
use serde::{Deserialize, Serialize};
use tempfile::NamedTempFile;
use unicode_normalization::{UnicodeNormalization, char::is_combining_mark};
use uuid::Uuid;
use zeroize::Zeroizing;

const CATALOG_SCHEMA: u16 = 3;
const CATALOG_DOMAIN: &str = "player-catalog-v1";
const CATALOG_FILE: &str = "library.catalog.lrail-private";
const CATALOG_KEY_SERVICE: &str = "com.lyricrail.private-state";
const CATALOG_KEY_ACCOUNT: &str = "player-catalog-v1";
const MAX_CATALOG_BYTES: u64 = 64 * 1024 * 1024 + 1024;
const MAX_INDEXED_LYRIC_TOTAL_BYTES: usize = 32 * 1024 * 1024;
const MAX_SEARCH_RESULTS: usize = 200;
const MAX_CATALOG_ITEMS: usize = 100_000;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ItemStatus {
    Ready,
    Queued,
    Processing,
    WaitingForLyrics,
    SetupRequired,
    Failed,
    Offline,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ProcessingEvidenceStatus {
    Queued,
    Running,
    Succeeded,
    Failed,
    Cancelled,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProcessingTaskEvidence {
    pub job_id: Option<String>,
    pub status: ProcessingEvidenceStatus,
    pub progress_percent: f32,
    pub stage_key: Option<String>,
    pub stage_title: Option<String>,
    pub stage_progress_percent: Option<f32>,
    pub started_at_millis: u64,
    pub updated_at_millis: u64,
    pub finished_at_millis: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum ItemLocation {
    LocalPackage {
        source_id: Option<String>,
        path: PathBuf,
        #[serde(default = "available_by_default")]
        available: bool,
    },
    LocalMedia {
        source_id: Option<String>,
        path: PathBuf,
        lyrics_path: Option<PathBuf>,
        #[serde(default)]
        origin: MediaOrigin,
        #[serde(default)]
        trim_start_millis: Option<u64>,
        #[serde(default)]
        trim_end_millis: Option<u64>,
        #[serde(default = "available_by_default")]
        available: bool,
    },
    GoogleDrive {
        source_id: String,
        #[serde(default)]
        root_id: String,
        file_id: String,
        name: String,
        size: u64,
        version: String,
        modified_time: Option<String>,
        md5_checksum: Option<String>,
        #[serde(default = "available_by_default")]
        available: bool,
    },
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum MediaOrigin {
    #[default]
    Disk,
    Url,
}

const fn available_by_default() -> bool {
    true
}

impl ItemLocation {
    pub fn is_local_package(&self) -> bool {
        matches!(self, Self::LocalPackage { .. })
    }

    pub fn is_available(&self) -> bool {
        match self {
            Self::LocalPackage { available, .. }
            | Self::LocalMedia { available, .. }
            | Self::GoogleDrive { available, .. } => *available,
        }
    }

    pub fn is_available_package(&self) -> bool {
        self.is_available() && matches!(self, Self::LocalPackage { .. } | Self::GoogleDrive { .. })
    }

    pub fn source_label(&self) -> &'static str {
        match self {
            Self::LocalPackage { .. } => "Disk",
            Self::LocalMedia { origin, .. } => match origin {
                MediaOrigin::Disk => "Disk",
                MediaOrigin::Url => "URL",
            },
            Self::GoogleDrive { .. } => "Drive",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CatalogItem {
    pub id: String,
    pub package_id: Option<String>,
    pub title: String,
    pub artist: Option<String>,
    pub composer: Option<String>,
    pub first_lyric_line: Option<String>,
    #[serde(default)]
    pub lyric_text: String,
    pub status: ItemStatus,
    #[serde(default)]
    pub progress_percent: f32,
    #[serde(default)]
    pub status_message: Option<String>,
    #[serde(default)]
    pub processing_job_id: Option<String>,
    #[serde(default)]
    pub processing_task_evidence: Option<ProcessingTaskEvidence>,
    #[serde(default)]
    pub has_thumbnail: bool,
    #[serde(default)]
    pub locations: Vec<ItemLocation>,
}

impl CatalogItem {
    pub fn source_labels(&self) -> Vec<&'static str> {
        let mut labels = self
            .locations
            .iter()
            .map(ItemLocation::source_label)
            .collect::<Vec<_>>();
        labels.sort_unstable();
        labels.dedup();
        labels
    }

    pub fn preferred_location(&self) -> Option<&ItemLocation> {
        self.locations
            .iter()
            .find(|location| location.is_local_package() && location.is_available())
            .or_else(|| {
                self.locations
                    .iter()
                    .find(|location| location.is_available_package())
            })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalSource {
    pub id: String,
    pub path: PathBuf,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DriveSource {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub roots: Vec<DriveRoot>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct DriveRoot {
    pub file_id: String,
    pub name: String,
    pub is_folder: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CatalogDocument {
    schema_version: u16,
    #[serde(default)]
    local_sources: Vec<LocalSource>,
    #[serde(default)]
    drive_sources: Vec<DriveSource>,
    #[serde(default)]
    items: Vec<CatalogItem>,
}

impl Default for CatalogDocument {
    fn default() -> Self {
        Self {
            schema_version: CATALOG_SCHEMA,
            local_sources: Vec::new(),
            drive_sources: Vec::new(),
            items: Vec::new(),
        }
    }
}

fn migrate_catalog_document(mut document: CatalogDocument) -> Result<CatalogDocument, String> {
    match document.schema_version {
        1 | 2 => document.schema_version = CATALOG_SCHEMA,
        CATALOG_SCHEMA => {}
        version => return Err(format!("Unsupported private catalog schema {version}")),
    }
    if document.items.len() > MAX_CATALOG_ITEMS {
        return Err("Private catalog exceeds its item-count bound".into());
    }
    Ok(document)
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CatalogSnapshot {
    pub items: Vec<CatalogItemView>,
    pub local_sources: Vec<LocalSource>,
    pub drive_sources: Vec<DriveSource>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CatalogItemView {
    pub id: String,
    pub package_id: Option<String>,
    pub title: String,
    pub artist: Option<String>,
    pub composer: Option<String>,
    pub first_lyric_line: Option<String>,
    pub status: ItemStatus,
    pub progress_percent: f32,
    pub status_message: Option<String>,
    pub has_thumbnail: bool,
    pub can_process: bool,
    pub sources: Vec<String>,
}

impl From<&CatalogItem> for CatalogItemView {
    fn from(item: &CatalogItem) -> Self {
        Self {
            id: item.id.clone(),
            package_id: item.package_id.clone(),
            title: item.title.clone(),
            artist: item.artist.clone(),
            composer: item.composer.clone(),
            first_lyric_line: item.first_lyric_line.clone(),
            status: item.status.clone(),
            progress_percent: item.progress_percent,
            status_message: item.status_message.clone(),
            has_thumbnail: item.has_thumbnail,
            can_process: item
                .locations
                .iter()
                .any(|location| matches!(location, ItemLocation::LocalMedia { .. })),
            sources: item
                .source_labels()
                .into_iter()
                .map(str::to_owned)
                .collect(),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SearchResult {
    #[serde(flatten)]
    pub item: CatalogItemView,
    pub lyric_snippet: Option<String>,
}

#[derive(Debug, Clone)]
struct SearchRecord {
    title: String,
    artist: String,
    composer: String,
    lyrics: String,
}

pub struct Catalog {
    path: PathBuf,
    document: CatalogDocument,
    search: HashMap<String, SearchRecord>,
    item_lookup: HashMap<String, usize>,
    package_lookup: HashMap<String, usize>,
    location_lookup: HashMap<LocationKey, usize>,
    indexed_lyric_bytes: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
enum LocationKey {
    LocalPackage(PathBuf),
    LocalMedia(PathBuf),
    GoogleDrive(String, String, String),
}

fn catalog_master() -> Result<LockedSecret<32>, String> {
    let entry = Entry::new(CATALOG_KEY_SERVICE, CATALOG_KEY_ACCOUNT)
        .map_err(|error| format!("Unable to open catalog credential: {error}"))?;
    match entry.get_secret() {
        Ok(secret) => {
            let secret = Zeroizing::new(secret);
            LockedSecret::from_slice(&secret)
                .map_err(|error| format!("Stored catalog key is invalid: {error}"))
        }
        Err(KeyringError::NoEntry) => {
            let key = LockedSecret::<32>::random().map_err(|error| error.to_string())?;
            entry
                .set_secret(key.as_ref())
                .map_err(|error| format!("Unable to store catalog key: {error}"))?;
            Ok(key)
        }
        Err(error) => Err(format!("Unable to read catalog credential: {error}")),
    }
}

impl Catalog {
    pub fn load(data_directory: &Path) -> Result<Self, String> {
        fs::create_dir_all(data_directory)
            .map_err(|error| format!("Unable to create Player data directory: {error}"))?;
        let path = data_directory.join(CATALOG_FILE);
        let document = if path.exists() {
            let metadata = fs::metadata(&path)
                .map_err(|error| format!("Unable to inspect private catalog: {error}"))?;
            if !metadata.is_file() || metadata.len() > MAX_CATALOG_BYTES {
                return Err("Private catalog is not a bounded regular file".into());
            }
            let encoded = fs::read(&path)
                .map_err(|error| format!("Unable to read private catalog: {error}"))?;
            let master = catalog_master()?;
            let plaintext = open_library_record(&master, CATALOG_DOMAIN, &encoded)
                .map_err(|error| format!("Private catalog authentication failed: {error}"))?;
            let document: CatalogDocument = serde_json::from_slice(&plaintext)
                .map_err(|error| format!("Private catalog is invalid: {error}"))?;
            migrate_catalog_document(document)?
        } else {
            CatalogDocument::default()
        };
        let mut document = document;
        for item in &mut document.items {
            item.status_message = item
                .status_message
                .take()
                .map(|message| crate::tasks::redact_diagnostic_text(&message));
            if matches!(item.status, ItemStatus::Queued | ItemStatus::Processing) {
                item.status = ItemStatus::Failed;
                item.progress_percent = 0.0;
                item.status_message =
                    Some("The previous processing session ended; choose Retry when ready".into());
            }
        }
        let mut catalog = Self {
            path,
            document,
            search: HashMap::new(),
            item_lookup: HashMap::new(),
            package_lookup: HashMap::new(),
            location_lookup: HashMap::new(),
            indexed_lyric_bytes: 0,
        };
        catalog.rebuild_indexes();
        if catalog.indexed_lyric_bytes > MAX_INDEXED_LYRIC_TOTAL_BYTES {
            return Err("Private catalog lyric text exceeds the 32 MiB limit".into());
        }
        Ok(catalog)
    }

    pub fn snapshot(&self) -> CatalogSnapshot {
        CatalogSnapshot {
            items: self
                .document
                .items
                .iter()
                .map(CatalogItemView::from)
                .collect(),
            local_sources: self.document.local_sources.clone(),
            drive_sources: self.document.drive_sources.clone(),
        }
    }

    pub fn save(&self) -> Result<(), String> {
        let plaintext = serde_json::to_vec(&self.document)
            .map_err(|error| format!("Unable to encode private catalog: {error}"))?;
        if plaintext.len() as u64 > MAX_CATALOG_BYTES.saturating_sub(1024) {
            return Err("Private catalog exceeds its 64 MiB storage bound".into());
        }
        let master = catalog_master()?;
        let encoded = seal_library_record(&master, CATALOG_DOMAIN, &plaintext)
            .map_err(|error| format!("Unable to encrypt private catalog: {error}"))?;
        let parent = self
            .path
            .parent()
            .ok_or_else(|| "Private catalog has no parent directory".to_string())?;
        let mut temporary = NamedTempFile::new_in(parent)
            .map_err(|error| format!("Unable to create private catalog replacement: {error}"))?;
        temporary
            .write_all(&encoded)
            .and_then(|()| temporary.as_file_mut().sync_all())
            .map_err(|error| format!("Unable to write private catalog replacement: {error}"))?;
        temporary
            .persist(&self.path)
            .map_err(|error| format!("Unable to publish private catalog: {}", error.error))?;
        Ok(())
    }

    pub fn local_sources(&self) -> &[LocalSource] {
        &self.document.local_sources
    }

    pub fn items(&self) -> &[CatalogItem] {
        &self.document.items
    }

    pub fn drive_sources(&self) -> &[DriveSource] {
        &self.document.drive_sources
    }

    pub fn add_local_source(&mut self, path: PathBuf) -> String {
        if let Some(existing) = self
            .document
            .local_sources
            .iter()
            .find(|source| source.path == path)
        {
            return existing.id.clone();
        }
        let id = Uuid::new_v4().to_string();
        self.document.local_sources.push(LocalSource {
            id: id.clone(),
            path,
        });
        id
    }

    pub fn add_drive_source(&mut self, id: String, name: String, roots: Vec<DriveRoot>) {
        if let Some(existing) = self
            .document
            .drive_sources
            .iter_mut()
            .find(|source| source.id == id)
        {
            existing.name = name;
            existing.roots = roots;
            return;
        }
        self.document
            .drive_sources
            .push(DriveSource { id, name, roots });
    }

    pub fn remove_source(&mut self, source_id: &str) -> bool {
        let before = self.document.local_sources.len() + self.document.drive_sources.len();
        self.document
            .local_sources
            .retain(|source| source.id != source_id);
        self.document
            .drive_sources
            .retain(|source| source.id != source_id);
        let valid_local = self
            .document
            .local_sources
            .iter()
            .map(|source| source.id.as_str())
            .collect::<HashSet<_>>();
        let valid_drive = self
            .document
            .drive_sources
            .iter()
            .map(|source| source.id.as_str())
            .collect::<HashSet<_>>();
        self.document.items.retain_mut(|item| {
            item.locations.retain(|location| match location {
                ItemLocation::LocalPackage { source_id, .. }
                | ItemLocation::LocalMedia { source_id, .. } => source_id
                    .as_deref()
                    .is_none_or(|source_id| valid_local.contains(source_id)),
                ItemLocation::GoogleDrive { source_id, .. } => {
                    valid_drive.contains(source_id.as_str())
                }
            });
            !item.locations.is_empty()
        });
        self.rebuild_indexes();
        before != self.document.local_sources.len() + self.document.drive_sources.len()
    }

    pub fn reconcile_local_source(&mut self, source_id: &str, live_paths: &HashSet<PathBuf>) {
        for item in &mut self.document.items {
            for location in &mut item.locations {
                match location {
                    ItemLocation::LocalPackage {
                        source_id: current,
                        path,
                        available,
                    }
                    | ItemLocation::LocalMedia {
                        source_id: current,
                        path,
                        available,
                        ..
                    } if current.as_deref() == Some(source_id) => {
                        *available = live_paths.contains(path);
                    }
                    _ => {}
                }
            }
            refresh_availability_status(item);
        }
    }

    pub fn reconcile_drive_root(
        &mut self,
        source_id: &str,
        root_id: &str,
        live_file_ids: &HashSet<String>,
    ) {
        for item in &mut self.document.items {
            for location in &mut item.locations {
                if let ItemLocation::GoogleDrive {
                    source_id: current_source,
                    root_id: current_root,
                    file_id,
                    available,
                    ..
                } = location
                    && current_source == source_id
                    && current_root == root_id
                {
                    *available = live_file_ids.contains(file_id);
                }
            }
            refresh_availability_status(item);
        }
    }

    pub fn set_drive_source_availability<F>(&mut self, source_id: &str, mut available: F)
    where
        F: FnMut(&ItemLocation) -> bool,
    {
        for item in &mut self.document.items {
            for location in &mut item.locations {
                let belongs = matches!(
                    location,
                    ItemLocation::GoogleDrive {
                        source_id: current,
                        ..
                    } if current == source_id
                );
                if belongs {
                    let value = available(location);
                    if let ItemLocation::GoogleDrive { available, .. } = location {
                        *available = value;
                    }
                }
            }
            refresh_availability_status(item);
        }
    }

    pub fn set_drive_root_availability<F>(
        &mut self,
        source_id: &str,
        root_id: &str,
        mut available: F,
    ) where
        F: FnMut(&ItemLocation) -> bool,
    {
        for item in &mut self.document.items {
            for location in &mut item.locations {
                let belongs = matches!(
                    location,
                    ItemLocation::GoogleDrive {
                        source_id: current_source,
                        root_id: current_root,
                        ..
                    } if current_source == source_id && current_root == root_id
                );
                if belongs {
                    let value = available(location);
                    if let ItemLocation::GoogleDrive { available, .. } = location {
                        *available = value;
                    }
                }
            }
            refresh_availability_status(item);
        }
    }

    pub fn item(&self, id: &str) -> Option<&CatalogItem> {
        self.item_lookup
            .get(id)
            .and_then(|index| self.document.items.get(*index))
    }

    pub fn item_mut(&mut self, id: &str) -> Option<&mut CatalogItem> {
        let index = *self.item_lookup.get(id)?;
        self.document.items.get_mut(index)
    }

    pub fn setup_required_items(&self) -> Vec<CatalogItem> {
        self.document
            .items
            .iter()
            .filter(|item| item.status == ItemStatus::SetupRequired)
            .cloned()
            .collect()
    }

    pub fn queue_setup_required_after_verification(&mut self) -> Vec<CatalogItem> {
        let mut queued = Vec::new();
        for item in &mut self.document.items {
            if item.status == ItemStatus::SetupRequired {
                item.status = ItemStatus::Queued;
                item.progress_percent = 0.0;
                item.status_message = Some("Models verified; waiting for the local worker".into());
                queued.push(item.clone());
            }
        }
        queued
    }

    pub fn migrate_legacy_runtime_failures_to_setup_required(&mut self) -> usize {
        let mut changed = 0;
        for item in &mut self.document.items {
            if item.status == ItemStatus::Failed
                && item.status_message.as_deref()
                    == Some("Processing runtime failed its startup checks")
            {
                item.status = ItemStatus::SetupRequired;
                item.status_message = Some(
                    "Processing models are not installed; open Issues to resolve setup".into(),
                );
                changed += 1;
            }
        }
        changed
    }

    pub fn set_progress(
        &mut self,
        id: &str,
        status: ItemStatus,
        progress_percent: f32,
        message: Option<String>,
    ) -> bool {
        let Some(item) = self.item_mut(id) else {
            return false;
        };
        item.status = status;
        item.progress_percent = progress_percent.clamp(0.0, 100.0);
        item.status_message = message.map(|value| crate::tasks::redact_diagnostic_text(&value));
        true
    }

    pub fn set_processing_job_id(&mut self, id: &str, job_id: Option<String>) -> bool {
        let Some(item) = self.item_mut(id) else {
            return false;
        };
        if item.processing_job_id == job_id {
            return false;
        }
        item.processing_job_id = job_id;
        true
    }

    pub fn set_processing_task_evidence(
        &mut self,
        id: &str,
        evidence: ProcessingTaskEvidence,
    ) -> bool {
        let Some(item) = self.item_mut(id) else {
            return false;
        };
        item.processing_task_evidence = Some(evidence);
        true
    }

    pub fn provide_lyrics(
        &mut self,
        id: &str,
        lyrics_path: PathBuf,
        text: String,
    ) -> Result<(), String> {
        let index = *self
            .item_lookup
            .get(id)
            .ok_or_else(|| "Library item no longer exists".to_string())?;
        let old_bytes = self.document.items[index].lyric_text.len();
        let existing_bytes = self.indexed_lyric_bytes.saturating_sub(old_bytes);
        if existing_bytes.saturating_add(text.len()) > MAX_INDEXED_LYRIC_TOTAL_BYTES {
            return Err("The private lyric search catalog reached its 32 MiB text limit".into());
        }
        let item = &mut self.document.items[index];
        let location = item
            .locations
            .iter_mut()
            .find_map(|location| match location {
                ItemLocation::LocalMedia { lyrics_path, .. } => Some(lyrics_path),
                _ => None,
            })
            .ok_or_else(|| "Only local media can receive lyrics".to_string())?;
        *location = Some(lyrics_path);
        item.lyric_text = text;
        item.first_lyric_line = item
            .lyric_text
            .lines()
            .find(|line| !line.trim().is_empty())
            .map(str::to_owned);
        item.status = ItemStatus::Queued;
        item.progress_percent = 0.0;
        item.status_message = None;
        item.processing_job_id = None;
        item.processing_task_evidence = None;
        self.indexed_lyric_bytes = existing_bytes + item.lyric_text.len();
        self.search
            .insert(item.id.clone(), SearchRecord::from(&*item));
        Ok(())
    }

    pub fn complete_processing(
        &mut self,
        id: &str,
        mut package: CatalogItem,
    ) -> Result<bool, String> {
        let Some(index) = self.item_lookup.get(id).copied() else {
            return self.upsert(package).map(|_| false);
        };
        let revised_total = self
            .indexed_lyric_bytes
            .saturating_sub(self.document.items[index].lyric_text.len())
            .saturating_add(package.lyric_text.len());
        if revised_total > MAX_INDEXED_LYRIC_TOTAL_BYTES {
            return Err("The private lyric search catalog reached its 32 MiB text limit".into());
        }
        let original_id = self.document.items[index].id.clone();
        let mut locations = self.document.items[index].locations.clone();
        for location in package.locations.drain(..) {
            if let Some(existing) = locations
                .iter_mut()
                .find(|current| same_location(current, &location))
            {
                *existing = location;
            } else {
                locations.push(location);
            }
        }
        package.id = original_id;
        package.locations = locations;
        self.replace_at(index, package);
        Ok(true)
    }

    pub fn upsert(&mut self, mut incoming: CatalogItem) -> Result<String, String> {
        incoming.status_message = incoming
            .status_message
            .take()
            .map(|message| crate::tasks::redact_diagnostic_text(&message));
        let match_index = incoming
            .package_id
            .as_deref()
            .and_then(|package_id| self.package_lookup.get(package_id).copied())
            .or_else(|| {
                incoming
                    .locations
                    .iter()
                    .find_map(|location| self.location_lookup.get(&location_key(location)).copied())
            });
        if let Some(index) = match_index
            && has_matching_local_media(&self.document.items[index], &incoming)
        {
            if is_trim_metadata_downgrade(&self.document.items[index], &incoming) {
                incoming.title.clone_from(&self.document.items[index].title);
            }
            preserve_local_media_content(&self.document.items[index], &mut incoming);
        }
        let old_lyric_bytes = match_index
            .and_then(|index| self.document.items.get(index))
            .map(|item| item.lyric_text.len())
            .unwrap_or(0);
        let lyric_bytes_without_match = self.indexed_lyric_bytes.saturating_sub(old_lyric_bytes);
        let keeps_existing = match_index.is_some_and(|index| {
            (self.document.items[index].package_id.is_some() && incoming.package_id.is_none())
                || self.document.items[index].status == ItemStatus::Processing
        });
        let revised_lyric_bytes = if keeps_existing {
            old_lyric_bytes
        } else {
            incoming.lyric_text.len()
        };
        if lyric_bytes_without_match.saturating_add(revised_lyric_bytes)
            > MAX_INDEXED_LYRIC_TOTAL_BYTES
        {
            return Err("The private lyric search catalog reached its 32 MiB text limit".into());
        }
        if let Some(index) = match_index {
            let mut merged_locations = self.document.items[index].locations.clone();
            for location in incoming.locations.drain(..) {
                if let Some(index) = merged_locations
                    .iter()
                    .position(|current| same_location(current, &location))
                {
                    merged_locations[index] = merge_location(&merged_locations[index], location);
                } else {
                    merged_locations.push(location);
                }
            }
            if self.document.items[index].package_id.is_some() && incoming.package_id.is_none() {
                let existing = &mut self.document.items[index];
                existing.locations = merged_locations;
                let id = existing.id.clone();
                refresh_availability_status(existing);
                self.refresh_location_indexes(index);
                return Ok(id);
            }
            if self.document.items[index].status != ItemStatus::Processing {
                incoming.locations = merged_locations;
                incoming.id = self.document.items[index].id.clone();
                if incoming.processing_job_id.is_none() {
                    incoming.processing_job_id =
                        self.document.items[index].processing_job_id.clone();
                }
                if incoming.processing_task_evidence.is_none() {
                    incoming.processing_task_evidence =
                        self.document.items[index].processing_task_evidence.clone();
                }
                if self.document.items[index].status == ItemStatus::SetupRequired
                    && incoming.status == ItemStatus::Queued
                {
                    incoming.status = ItemStatus::SetupRequired;
                    incoming.status_message = self.document.items[index].status_message.clone();
                } else if incoming.processing_job_id.is_some()
                    && matches!(
                        self.document.items[index].status,
                        ItemStatus::Failed | ItemStatus::SetupRequired
                    )
                    && incoming.status == ItemStatus::Queued
                {
                    incoming.status = self.document.items[index].status.clone();
                    incoming.status_message = self.document.items[index].status_message.clone();
                }
                self.replace_at(index, incoming);
            } else {
                self.document.items[index].locations = merged_locations;
                self.refresh_location_indexes(index);
            }
        } else {
            let index = self.document.items.len();
            self.document.items.push(incoming);
            self.index_item(index);
        }
        Ok(match_index
            .and_then(|index| self.document.items.get(index))
            .or_else(|| self.document.items.last())
            .map(|item| item.id.clone())
            .expect("upsert always leaves one item"))
    }

    pub fn validate_upserts<'a>(
        &self,
        items: impl IntoIterator<Item = &'a CatalogItem>,
    ) -> Result<(), String> {
        let mut total = self.indexed_lyric_bytes;
        let mut lengths = self
            .document
            .items
            .iter()
            .map(|item| item.lyric_text.len())
            .collect::<Vec<_>>();
        let mut package_present = self
            .document
            .items
            .iter()
            .map(|item| item.package_id.is_some())
            .collect::<Vec<_>>();
        let mut processing = self
            .document
            .items
            .iter()
            .map(|item| item.status == ItemStatus::Processing)
            .collect::<Vec<_>>();
        let mut packages = self.package_lookup.clone();
        let mut locations = self.location_lookup.clone();
        for incoming in items {
            let matched = incoming
                .package_id
                .as_deref()
                .and_then(|package_id| packages.get(package_id).copied())
                .or_else(|| {
                    incoming
                        .locations
                        .iter()
                        .find_map(|location| locations.get(&location_key(location)).copied())
                });
            let index = matched.unwrap_or(lengths.len());
            if matched.is_none() {
                if lengths.len() >= MAX_CATALOG_ITEMS {
                    return Err(format!(
                        "The private catalog reached its {MAX_CATALOG_ITEMS}-item limit"
                    ));
                }
                lengths.push(0);
                package_present.push(false);
                processing.push(false);
            }
            let keeps_existing =
                (package_present[index] && incoming.package_id.is_none()) || processing[index];
            let revised_length = if keeps_existing {
                lengths[index]
            } else {
                incoming.lyric_text.len()
            };
            total = total
                .saturating_sub(lengths[index])
                .saturating_add(revised_length);
            if total > MAX_INDEXED_LYRIC_TOTAL_BYTES {
                return Err(
                    "The private lyric search catalog reached its 32 MiB text limit".into(),
                );
            }
            lengths[index] = revised_length;
            if let Some(package_id) = &incoming.package_id {
                packages.insert(package_id.clone(), index);
                package_present[index] = true;
            }
            for location in &incoming.locations {
                locations.insert(location_key(location), index);
            }
        }
        Ok(())
    }

    pub fn upsert_many(&mut self, items: Vec<CatalogItem>) -> Result<Vec<String>, String> {
        self.validate_upserts(items.iter())?;
        items.into_iter().map(|item| self.upsert(item)).collect()
    }

    pub fn search(&self, query: &str) -> Vec<SearchResult> {
        let query = normalize(query);
        if query.is_empty() {
            return self
                .document
                .items
                .iter()
                .take(MAX_SEARCH_RESULTS)
                .map(|item| SearchResult {
                    item: CatalogItemView::from(item),
                    lyric_snippet: None,
                })
                .collect();
        }
        let mut matches = self
            .document
            .items
            .iter()
            .filter_map(|item| {
                let record = self.search.get(&item.id)?;
                let (score, lyric_match) = if record.title == query {
                    (500_u16, false)
                } else if record.title.starts_with(&query) {
                    (450, false)
                } else if record.title.contains(&query) {
                    (400, false)
                } else if record.artist.contains(&query) {
                    (300, false)
                } else if record.composer.contains(&query) {
                    (250, false)
                } else if record.lyrics.contains(&query) {
                    (100, true)
                } else {
                    return None;
                };
                Some((
                    Reverse(score),
                    item.title.clone(),
                    SearchResult {
                        item: CatalogItemView::from(item),
                        lyric_snippet: lyric_match.then(|| lyric_snippet(&item.lyric_text, &query)),
                    },
                ))
            })
            .collect::<Vec<_>>();
        matches.sort_by(|left, right| left.0.cmp(&right.0).then_with(|| left.1.cmp(&right.1)));
        matches
            .into_iter()
            .take(MAX_SEARCH_RESULTS)
            .map(|(_, _, result)| result)
            .collect()
    }

    fn replace_at(&mut self, index: usize, item: CatalogItem) {
        let old = std::mem::replace(&mut self.document.items[index], item);
        self.indexed_lyric_bytes = self
            .indexed_lyric_bytes
            .saturating_sub(old.lyric_text.len());
        self.search.remove(&old.id);
        if let Some(package_id) = old.package_id {
            self.package_lookup.remove(&package_id);
        }
        for location in &old.locations {
            self.location_lookup.remove(&location_key(location));
        }
        self.item_lookup.remove(&old.id);
        self.index_item(index);
    }

    fn index_item(&mut self, index: usize) {
        let item = &self.document.items[index];
        self.item_lookup.insert(item.id.clone(), index);
        if let Some(package_id) = &item.package_id {
            self.package_lookup.insert(package_id.clone(), index);
        }
        for location in &item.locations {
            self.location_lookup.insert(location_key(location), index);
        }
        self.search
            .insert(item.id.clone(), SearchRecord::from(item));
        self.indexed_lyric_bytes = self
            .indexed_lyric_bytes
            .saturating_add(item.lyric_text.len());
    }

    fn refresh_location_indexes(&mut self, index: usize) {
        self.location_lookup.retain(|_, current| *current != index);
        for location in &self.document.items[index].locations {
            self.location_lookup.insert(location_key(location), index);
        }
    }

    fn rebuild_indexes(&mut self) {
        self.search.clear();
        self.item_lookup.clear();
        self.package_lookup.clear();
        self.location_lookup.clear();
        self.indexed_lyric_bytes = 0;
        for index in 0..self.document.items.len() {
            self.index_item(index);
        }
    }
}

impl From<&CatalogItem> for SearchRecord {
    fn from(item: &CatalogItem) -> Self {
        Self {
            title: normalize(&item.title),
            artist: normalize(item.artist.as_deref().unwrap_or("")),
            composer: normalize(item.composer.as_deref().unwrap_or("")),
            lyrics: normalize(&item.lyric_text),
        }
    }
}

fn location_key(location: &ItemLocation) -> LocationKey {
    match location {
        ItemLocation::LocalPackage { path, .. } => LocationKey::LocalPackage(path.clone()),
        ItemLocation::LocalMedia { path, .. } => LocationKey::LocalMedia(path.clone()),
        ItemLocation::GoogleDrive {
            source_id,
            root_id,
            file_id,
            ..
        } => LocationKey::GoogleDrive(source_id.clone(), root_id.clone(), file_id.clone()),
    }
}

fn refresh_availability_status(item: &mut CatalogItem) {
    let any_available = item.locations.iter().any(ItemLocation::is_available);
    let playable_available = item
        .locations
        .iter()
        .any(ItemLocation::is_available_package);
    if !any_available || (item.package_id.is_some() && !playable_available) {
        item.status = ItemStatus::Offline;
        item.progress_percent = 0.0;
        item.status_message = Some("No selected source is currently available".into());
    } else if item.package_id.is_some() && item.status == ItemStatus::Offline {
        item.status = ItemStatus::Ready;
        item.progress_percent = 100.0;
        item.status_message = None;
    }
}

fn same_location(left: &ItemLocation, right: &ItemLocation) -> bool {
    match (left, right) {
        (
            ItemLocation::LocalPackage { path: left, .. },
            ItemLocation::LocalPackage { path: right, .. },
        )
        | (
            ItemLocation::LocalMedia { path: left, .. },
            ItemLocation::LocalMedia { path: right, .. },
        ) => left == right,
        (
            ItemLocation::GoogleDrive {
                source_id: left_source,
                root_id: left_root,
                file_id: left_file,
                ..
            },
            ItemLocation::GoogleDrive {
                source_id: right_source,
                root_id: right_root,
                file_id: right_file,
                ..
            },
        ) => left_source == right_source && left_root == right_root && left_file == right_file,
        _ => false,
    }
}

fn is_trim_metadata_downgrade(existing: &CatalogItem, incoming: &CatalogItem) -> bool {
    existing.locations.iter().any(|location| {
        let ItemLocation::LocalMedia {
            path: existing_path,
            trim_start_millis: Some(_),
            trim_end_millis: Some(_),
            ..
        } = location
        else {
            return false;
        };
        incoming.locations.iter().any(|candidate| {
            matches!(
                candidate,
                ItemLocation::LocalMedia {
                    path,
                    trim_start_millis: None,
                    trim_end_millis: None,
                    ..
                } if path == existing_path
            )
        })
    })
}

fn has_matching_local_media(existing: &CatalogItem, incoming: &CatalogItem) -> bool {
    existing.locations.iter().any(|location| {
        let ItemLocation::LocalMedia {
            path: existing_path,
            ..
        } = location
        else {
            return false;
        };
        incoming.locations.iter().any(|candidate| {
            matches!(candidate, ItemLocation::LocalMedia { path, .. } if path == existing_path)
        })
    })
}

fn preserve_local_media_content(existing: &CatalogItem, incoming: &mut CatalogItem) {
    if incoming.artist.is_none() {
        incoming.artist.clone_from(&existing.artist);
    }
    if incoming.composer.is_none() {
        incoming.composer.clone_from(&existing.composer);
    }
    if incoming.lyric_text.is_empty() {
        incoming
            .first_lyric_line
            .clone_from(&existing.first_lyric_line);
        incoming.lyric_text.clone_from(&existing.lyric_text);
        incoming.status = existing.status.clone();
        incoming.progress_percent = existing.progress_percent;
        incoming.status_message.clone_from(&existing.status_message);
        incoming
            .processing_task_evidence
            .clone_from(&existing.processing_task_evidence);
    }
}

fn merge_location(existing: &ItemLocation, incoming: ItemLocation) -> ItemLocation {
    let ItemLocation::LocalMedia {
        source_id,
        path,
        lyrics_path,
        origin,
        trim_start_millis,
        trim_end_millis,
        available,
    } = incoming
    else {
        return incoming;
    };
    let ItemLocation::LocalMedia {
        lyrics_path: existing_lyrics,
        origin: existing_origin,
        trim_start_millis: existing_start,
        trim_end_millis: existing_end,
        available: existing_available,
        ..
    } = existing
    else {
        return ItemLocation::LocalMedia {
            source_id,
            path,
            lyrics_path,
            origin,
            trim_start_millis,
            trim_end_millis,
            available,
        };
    };
    let incoming_has_trim = trim_start_millis.is_some() && trim_end_millis.is_some();
    let existing_has_trim = existing_start.is_some() && existing_end.is_some();
    if !incoming_has_trim
        && !existing_has_trim
        && origin != MediaOrigin::Url
        && *existing_origin != MediaOrigin::Url
    {
        return ItemLocation::LocalMedia {
            source_id,
            path,
            lyrics_path,
            origin,
            trim_start_millis,
            trim_end_millis,
            available,
        };
    }
    let preserve_existing_metadata = (existing_has_trim && !incoming_has_trim)
        || (*existing_origin == MediaOrigin::Url && origin != MediaOrigin::Url);
    ItemLocation::LocalMedia {
        source_id: if preserve_existing_metadata {
            None
        } else {
            source_id
        },
        path,
        lyrics_path: lyrics_path.or_else(|| existing_lyrics.clone()),
        origin: if preserve_existing_metadata {
            existing_origin.clone()
        } else {
            origin
        },
        trim_start_millis: if preserve_existing_metadata {
            *existing_start
        } else {
            trim_start_millis
        },
        trim_end_millis: if preserve_existing_metadata {
            *existing_end
        } else {
            trim_end_millis
        },
        available: available || *existing_available,
    }
}

pub fn normalize(value: &str) -> String {
    value
        .nfkd()
        .filter(|character| !is_combining_mark(*character))
        .map(|character| match character {
            'đ' | 'Đ' => 'd',
            other => other,
        })
        .flat_map(char::to_lowercase)
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

fn lyric_snippet(original: &str, normalized_query: &str) -> String {
    let lines = original.lines().collect::<Vec<_>>();
    let line = lines
        .iter()
        .find(|line| normalize(line).contains(normalized_query))
        .copied()
        .unwrap_or(original);
    let mut characters = line.chars();
    let snippet = characters.by_ref().take(180).collect::<String>();
    if characters.next().is_some() {
        format!("{snippet}…")
    } else {
        snippet
    }
}

#[cfg(test)]
mod tests {
    use super::{
        CATALOG_SCHEMA, Catalog, CatalogDocument, CatalogItem, ItemLocation, ItemStatus,
        MediaOrigin, migrate_catalog_document, normalize,
    };
    use std::{
        collections::{HashMap, HashSet},
        path::PathBuf,
    };

    fn item(id: usize) -> CatalogItem {
        CatalogItem {
            id: id.to_string(),
            package_id: Some(format!("package-{id}")),
            title: if id == 7 {
                "Diễm xưa".into()
            } else {
                format!("Bài hát {id}")
            },
            artist: Some("Khánh Ly".into()),
            composer: Some("Trịnh Công Sơn".into()),
            first_lyric_line: Some("Mưa vẫn hay mưa".into()),
            lyric_text: "Mưa vẫn hay mưa trên tầng tháp cổ".into(),
            status: ItemStatus::Ready,
            progress_percent: 100.0,
            status_message: None,
            processing_job_id: None,
            processing_task_evidence: None,
            has_thumbnail: true,
            locations: vec![ItemLocation::LocalPackage {
                source_id: None,
                path: PathBuf::from(format!("/{id}.lrail")),
                available: true,
            }],
        }
    }

    fn catalog(count: usize) -> Catalog {
        let mut catalog = Catalog {
            path: PathBuf::from("unused"),
            document: CatalogDocument {
                schema_version: CATALOG_SCHEMA,
                local_sources: Vec::new(),
                drive_sources: Vec::new(),
                items: (0..count).map(item).collect(),
            },
            search: HashMap::new(),
            item_lookup: HashMap::new(),
            package_lookup: HashMap::new(),
            location_lookup: HashMap::new(),
            indexed_lyric_bytes: 0,
        };
        catalog.rebuild_indexes();
        catalog
    }

    #[test]
    fn vietnamese_search_is_diacritic_insensitive_and_ranked() {
        let catalog = catalog(20);
        let title = catalog.search("diem xua");
        assert_eq!(title[0].item.title, "Diễm xưa");
        let composer = catalog.search("trinh cong son");
        assert_eq!(composer.len(), 20);
        let lyric = catalog.search("tang thap co");
        assert!(
            lyric[0]
                .lyric_snippet
                .as_deref()
                .unwrap()
                .contains("tháp cổ")
        );
        assert_eq!(normalize("  ĐIỄM   XƯA "), "diem xua");
    }

    #[test]
    fn a_large_catalog_is_bounded_and_deduplicates_package_locations() {
        let mut catalog = catalog(10_000);
        assert_eq!(catalog.search("bai hat").len(), 200);
        let mut duplicate = item(7);
        duplicate.locations = vec![ItemLocation::GoogleDrive {
            source_id: "drive-source".into(),
            root_id: "drive-7".into(),
            file_id: "drive-7".into(),
            name: "7.lrail".into(),
            size: 42,
            version: "1".into(),
            modified_time: None,
            md5_checksum: None,
            available: true,
        }];
        catalog.upsert(duplicate).unwrap();
        assert_eq!(catalog.document.items.len(), 10_000);
        assert_eq!(catalog.item("7").unwrap().locations.len(), 2);
        assert!(
            catalog
                .item("7")
                .unwrap()
                .preferred_location()
                .unwrap()
                .is_local_package()
        );
    }

    #[test]
    fn bulk_upsert_updates_only_incremental_indexes_and_preserves_replacement_budget() {
        let mut catalog = catalog(0);
        let ids = catalog
            .upsert_many((0..20_000).map(item).collect())
            .unwrap();
        assert_eq!(ids.len(), 20_000);
        assert_eq!(catalog.document.items.len(), 20_000);
        assert_eq!(catalog.item_lookup.len(), 20_000);
        assert_eq!(catalog.package_lookup.len(), 20_000);
        assert_eq!(catalog.search("bai hat 19999")[0].item.id, "19999");

        let before = catalog.indexed_lyric_bytes;
        let mut replacement = item(19_999);
        replacement.lyric_text = "Một câu thay thế có thể tìm được".into();
        catalog.upsert(replacement.clone()).unwrap();
        assert_eq!(catalog.document.items.len(), 20_000);
        assert_eq!(
            catalog.indexed_lyric_bytes,
            before - item(19_999).lyric_text.len() + replacement.lyric_text.len()
        );
        assert_eq!(catalog.search("thay the")[0].item.id, "19999");
    }

    #[test]
    fn unavailable_local_location_keeps_the_row_and_prefers_available_drive() {
        let mut catalog = catalog(1);
        let mut duplicate = item(0);
        duplicate.locations = vec![ItemLocation::GoogleDrive {
            source_id: "drive".into(),
            root_id: "folder".into(),
            file_id: "remote".into(),
            name: "song.lrail".into(),
            size: 10,
            version: "1".into(),
            modified_time: None,
            md5_checksum: None,
            available: true,
        }];
        catalog.upsert(duplicate).unwrap();
        if let ItemLocation::LocalPackage { source_id, .. } =
            &mut catalog.document.items[0].locations[0]
        {
            *source_id = Some("disk".into());
        }
        catalog.reconcile_local_source("disk", &HashSet::new());
        let current = catalog.item("0").unwrap();
        assert_eq!(current.status, ItemStatus::Ready);
        assert!(matches!(
            current.preferred_location(),
            Some(ItemLocation::GoogleDrive { .. })
        ));

        catalog.set_drive_source_availability("drive", |_| false);
        assert_eq!(catalog.item("0").unwrap().status, ItemStatus::Offline);
        assert_eq!(catalog.document.items.len(), 1);
    }

    #[test]
    fn legacy_local_media_defaults_to_disk_without_a_trim() {
        let legacy = serde_json::json!({
            "kind": "local-media",
            "source_id": null,
            "path": "song.mp4",
            "lyrics_path": null,
            "available": true
        });
        let location: ItemLocation = serde_json::from_value(legacy).unwrap();
        assert!(matches!(
            &location,
            ItemLocation::LocalMedia {
                origin: MediaOrigin::Disk,
                trim_start_millis: None,
                trim_end_millis: None,
                ..
            }
        ));
        assert_eq!(location.source_label(), "Disk");
    }

    #[test]
    fn disk_rescan_cannot_downgrade_local_clip_title_or_trim() {
        let path = PathBuf::from("selected-media/source.mp4");
        let mut clipped_item = item(0);
        clipped_item.package_id = None;
        clipped_item.title = "Chosen clip title".into();
        clipped_item.status = ItemStatus::WaitingForLyrics;
        clipped_item.lyric_text.clear();
        clipped_item.first_lyric_line = None;
        clipped_item.locations = vec![ItemLocation::LocalMedia {
            source_id: None,
            path: path.clone(),
            lyrics_path: None,
            origin: MediaOrigin::Disk,
            trim_start_millis: Some(12_345),
            trim_end_millis: Some(67_890),
            available: true,
        }];
        let mut catalog = catalog(0);
        let id = catalog.upsert(clipped_item).unwrap();

        let mut disk_item = item(1);
        disk_item.package_id = None;
        disk_item.title = "source".into();
        disk_item.status = ItemStatus::WaitingForLyrics;
        disk_item.lyric_text.clear();
        disk_item.first_lyric_line = None;
        disk_item.locations = vec![ItemLocation::LocalMedia {
            source_id: Some("selected-folder".into()),
            path: path.clone(),
            lyrics_path: None,
            origin: MediaOrigin::Disk,
            trim_start_millis: None,
            trim_end_millis: None,
            available: true,
        }];
        catalog.upsert(disk_item).unwrap();
        let current = catalog.item(&id).unwrap();
        assert_eq!(current.title, "Chosen clip title");
        assert!(matches!(
            &current.locations[0],
            ItemLocation::LocalMedia {
                source_id: None,
                origin: MediaOrigin::Disk,
                trim_start_millis: Some(12_345),
                trim_end_millis: Some(67_890),
                ..
            }
        ));

        let mut with_lyrics = current.clone();
        with_lyrics.title = "source".into();
        with_lyrics.lyric_text = "Exact lyric".into();
        with_lyrics.first_lyric_line = Some("Exact lyric".into());
        with_lyrics.status = ItemStatus::Queued;
        if let ItemLocation::LocalMedia {
            source_id,
            lyrics_path,
            origin,
            trim_start_millis,
            trim_end_millis,
            ..
        } = &mut with_lyrics.locations[0]
        {
            *source_id = Some("selected-folder".into());
            *lyrics_path = Some(PathBuf::from("selected-media/source.txt"));
            *origin = MediaOrigin::Disk;
            *trim_start_millis = None;
            *trim_end_millis = None;
        }
        catalog.upsert(with_lyrics).unwrap();
        let current = catalog.item(&id).unwrap();
        assert_eq!(current.title, "Chosen clip title");
        assert_eq!(current.lyric_text, "Exact lyric");
        assert_eq!(current.status, ItemStatus::Queued);
        assert!(matches!(
            &current.locations[0],
            ItemLocation::LocalMedia {
                source_id: None,
                origin: MediaOrigin::Disk,
                trim_start_millis: Some(12_345),
                trim_end_millis: Some(67_890),
                lyrics_path: Some(_),
                ..
            }
        ));

        let mut rescan = current.clone();
        catalog.set_progress(
            &id,
            ItemStatus::SetupRequired,
            0.0,
            Some("Processing setup required".into()),
        );
        rescan.status = ItemStatus::Queued;
        rescan.status_message = None;
        catalog.upsert(rescan).unwrap();
        assert_eq!(catalog.item(&id).unwrap().status, ItemStatus::SetupRequired);
    }

    #[test]
    fn selecting_a_clip_keeps_existing_authoritative_lyrics() {
        let path = PathBuf::from("selected-media/with-pasted-lyrics.mp4");
        let mut existing = item(0);
        existing.package_id = None;
        existing.title = "with-pasted-lyrics".into();
        existing.lyric_text = "Exact confirmed lyric".into();
        existing.first_lyric_line = Some("Exact confirmed lyric".into());
        existing.status = ItemStatus::Queued;
        existing.locations = vec![ItemLocation::LocalMedia {
            source_id: Some("selected-folder".into()),
            path: path.clone(),
            lyrics_path: Some(PathBuf::from("private/confirmed.txt")),
            origin: MediaOrigin::Disk,
            trim_start_millis: None,
            trim_end_millis: None,
            available: true,
        }];
        let mut catalog = catalog(0);
        let id = catalog.upsert(existing).unwrap();

        let mut clipped = item(1);
        clipped.package_id = None;
        clipped.title = "Chosen clip title".into();
        clipped.lyric_text.clear();
        clipped.first_lyric_line = None;
        clipped.status = ItemStatus::WaitingForLyrics;
        clipped.locations = vec![ItemLocation::LocalMedia {
            source_id: None,
            path,
            lyrics_path: None,
            origin: MediaOrigin::Disk,
            trim_start_millis: Some(1_000),
            trim_end_millis: Some(2_000),
            available: true,
        }];
        catalog.upsert(clipped).unwrap();

        let current = catalog.item(&id).unwrap();
        assert_eq!(current.title, "Chosen clip title");
        assert_eq!(current.lyric_text, "Exact confirmed lyric");
        assert_eq!(current.status, ItemStatus::Queued);
        assert!(matches!(
            &current.locations[0],
            ItemLocation::LocalMedia {
                source_id: None,
                lyrics_path: Some(_),
                trim_start_millis: Some(1_000),
                trim_end_millis: Some(2_000),
                ..
            }
        ));
    }

    #[test]
    fn prior_catalog_schemas_migrate_but_future_schema_fails_closed() {
        for schema_version in [1, 2] {
            let document = CatalogDocument {
                schema_version,
                ..Default::default()
            };
            assert_eq!(
                migrate_catalog_document(document).unwrap().schema_version,
                CATALOG_SCHEMA
            );
        }
        let future = CatalogDocument {
            schema_version: CATALOG_SCHEMA + 1,
            ..Default::default()
        };
        assert!(migrate_catalog_document(future).is_err());
    }

    #[test]
    fn legacy_generic_runtime_failure_becomes_actionable_setup_state() {
        let mut catalog = catalog(1);
        catalog.document.items[0].status = ItemStatus::Failed;
        catalog.document.items[0].status_message =
            Some("Processing runtime failed its startup checks".into());
        assert_eq!(
            catalog.migrate_legacy_runtime_failures_to_setup_required(),
            1
        );
        assert_eq!(catalog.document.items[0].status, ItemStatus::SetupRequired);
        assert!(
            catalog.document.items[0]
                .status_message
                .as_deref()
                .unwrap()
                .contains("open Issues")
        );
        let queued = catalog.queue_setup_required_after_verification();
        assert_eq!(queued.len(), 1);
        assert_eq!(queued[0].status, ItemStatus::Queued);
        assert_eq!(catalog.document.items[0].status, ItemStatus::Queued);
    }
}
