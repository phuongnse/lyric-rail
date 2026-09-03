use std::{
    cmp::Reverse,
    collections::{BinaryHeap, HashSet},
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicUsize, Ordering},
    },
    thread,
};

use lrail_format::{Error as PackageError, RandomAccessSource, Result as PackageResult};
use sha2::{Digest, Sha256};
use tempfile::{Builder as TempFileBuilder, NamedTempFile};
use uuid::Uuid;

use crate::scheduler::{IoPriority, PriorityScheduler};

pub const CACHE_BLOCK_BYTES: u64 = 1024 * 1024;
const DEFAULT_CACHE_BYTES: u64 = 8 * 1024 * 1024 * 1024;
const MAX_CACHE_FILES: usize = 50_000;

static CACHE_SESSION_PREFIX: OnceLock<String> = OnceLock::new();

fn cache_session_prefix() -> &'static str {
    CACHE_SESSION_PREFIX
        .get_or_init(|| format!(".lrail-block-{}-{}-", std::process::id(), Uuid::new_v4()))
}

#[cfg(test)]
type BeforePublishHook = Arc<dyn Fn(&Path) + Send + Sync>;

#[derive(Debug, Clone)]
pub struct RemoteObject {
    pub cache_key: String,
    pub length: u64,
    pub version: String,
}

pub trait RangeTransport: Send + Sync {
    fn fetch_range(
        &self,
        object: &RemoteObject,
        start: u64,
        end_inclusive: u64,
    ) -> Result<Vec<u8>, String>;
}

pub type DownloadProgress = Arc<dyn Fn(u64, u64, Option<String>) + Send + Sync>;

pub struct RangeCache {
    root: PathBuf,
    maximum_bytes: u64,
    maximum_files: usize,
    transport: Arc<dyn RangeTransport>,
    scheduler: Arc<PriorityScheduler>,
    writes_since_evict: AtomicUsize,
    usage: Mutex<CacheUsage>,
    partial_prefix: String,
    background_downloads: Mutex<HashSet<String>>,
    #[cfg(test)]
    before_publish: Option<BeforePublishHook>,
}

#[derive(Debug, Clone, Copy, Default)]
struct CacheUsage {
    bytes: u64,
    files: usize,
}

impl RangeCache {
    pub fn new(
        root: PathBuf,
        transport: Arc<dyn RangeTransport>,
        scheduler: Arc<PriorityScheduler>,
    ) -> Result<Self, String> {
        fs::create_dir_all(&root)
            .map_err(|error| format!("Unable to create ciphertext cache: {error}"))?;
        let partial_prefix = cache_session_prefix().to_owned();
        let usage = evict_directory(&root, DEFAULT_CACHE_BYTES, MAX_CACHE_FILES, &partial_prefix)?;
        Ok(Self {
            root,
            maximum_bytes: DEFAULT_CACHE_BYTES,
            maximum_files: MAX_CACHE_FILES,
            transport,
            scheduler,
            writes_since_evict: AtomicUsize::new(0),
            usage: Mutex::new(usage),
            partial_prefix,
            background_downloads: Mutex::new(HashSet::new()),
            #[cfg(test)]
            before_publish: None,
        })
    }

    #[cfg(test)]
    pub fn with_limit(mut self, maximum_bytes: u64) -> Self {
        self.maximum_bytes = maximum_bytes.max(CACHE_BLOCK_BYTES);
        let _ = self.evict();
        self
    }

    #[cfg(test)]
    pub fn with_limits(mut self, maximum_bytes: u64, maximum_files: usize) -> Self {
        self.maximum_bytes = maximum_bytes.max(1);
        self.maximum_files = maximum_files.max(1);
        self.evict().unwrap();
        self
    }

    #[cfg(test)]
    pub fn with_before_publish(mut self, hook: BeforePublishHook) -> Self {
        self.before_publish = Some(hook);
        self
    }

    fn object_prefix(object: &RemoteObject) -> String {
        let mut digest = Sha256::new();
        digest.update(object.cache_key.as_bytes());
        digest.update([0]);
        digest.update(object.version.as_bytes());
        digest.update([0]);
        digest.update(object.length.to_le_bytes());
        hex::encode(digest.finalize())
    }

    fn block_path(&self, object: &RemoteObject, index: u64) -> PathBuf {
        self.root.join(format!(
            "{}-{index:016x}.lrail-block",
            Self::object_prefix(object)
        ))
    }

    fn block_bounds(object: &RemoteObject, index: u64) -> Result<(u64, u64), String> {
        let start = index
            .checked_mul(CACHE_BLOCK_BYTES)
            .ok_or_else(|| "Cache block offset overflows".to_string())?;
        if start >= object.length {
            return Err("Cache block is outside the remote object".into());
        }
        Ok((
            start,
            (start + CACHE_BLOCK_BYTES - 1).min(object.length - 1),
        ))
    }

    fn read_cached_block(path: &Path, expected: usize) -> Option<Vec<u8>> {
        let metadata = fs::metadata(path).ok()?;
        if !metadata.is_file() || metadata.len() != expected as u64 {
            return None;
        }
        let mut bytes = Vec::with_capacity(expected);
        fs::File::open(path).ok()?.read_to_end(&mut bytes).ok()?;
        (bytes.len() == expected).then_some(bytes)
    }

    fn block(
        &self,
        object: &RemoteObject,
        index: u64,
        priority: IoPriority,
    ) -> Result<Vec<u8>, String> {
        let (start, end) = Self::block_bounds(object, index)?;
        let expected = usize::try_from(end - start + 1)
            .map_err(|_| "Cache block exceeds this platform".to_string())?;
        let path = self.block_path(object, index);
        if let Some(bytes) = Self::read_cached_block(&path, expected) {
            return Ok(bytes);
        }

        let _permit = self.scheduler.acquire(priority)?;
        if let Some(bytes) = Self::read_cached_block(&path, expected) {
            return Ok(bytes);
        }
        let bytes = self.transport.fetch_range(object, start, end)?;
        if bytes.len() != expected {
            return Err(format!(
                "Remote range returned {} bytes; expected {expected}",
                bytes.len()
            ));
        }
        let mut temporary = TempFileBuilder::new()
            .prefix(&self.partial_prefix)
            .suffix(".partial")
            .tempfile_in(&self.root)
            .map_err(|error| format!("Unable to create cache block: {error}"))?;
        temporary
            .write_all(&bytes)
            .and_then(|()| temporary.as_file_mut().sync_all())
            .map_err(|error| format!("Unable to write cache block: {error}"))?;
        #[cfg(test)]
        if let Some(hook) = &self.before_publish {
            hook(temporary.path());
        }
        let published = match temporary.persist_noclobber(&path) {
            Ok(_) => true,
            Err(error) if path.is_file() => {
                drop(error);
                false
            }
            Err(error) => return Err(format!("Unable to publish cache block: {}", error.error)),
        };
        let over_limit = if published {
            let mut usage = self
                .usage
                .lock()
                .map_err(|_| "Ciphertext cache accounting lock is poisoned".to_string())?;
            usage.bytes = usage.bytes.saturating_add(expected as u64);
            usage.files = usage.files.saturating_add(1);
            usage.bytes > self.maximum_bytes || usage.files > self.maximum_files
        } else {
            false
        };
        if over_limit || self.writes_since_evict.fetch_add(1, Ordering::Relaxed) % 64 == 63 {
            self.evict()?;
        }
        Ok(bytes)
    }

    pub fn read_exact(
        &self,
        object: &RemoteObject,
        offset: u64,
        output: &mut [u8],
        priority: IoPriority,
    ) -> Result<(), String> {
        let end = offset
            .checked_add(output.len() as u64)
            .ok_or_else(|| "Remote read overflows".to_string())?;
        if end > object.length {
            return Err("Remote read exceeds the object length".into());
        }
        if output.is_empty() {
            return Ok(());
        }
        let first = offset / CACHE_BLOCK_BYTES;
        let last = (end - 1) / CACHE_BLOCK_BYTES;
        let mut written = 0_usize;
        for index in first..=last {
            let block = self.block(object, index, priority)?;
            let block_start = index * CACHE_BLOCK_BYTES;
            let copy_start = offset.saturating_sub(block_start) as usize;
            let copy_end = ((end.min(block_start + block.len() as u64)) - block_start) as usize;
            let slice = &block[copy_start..copy_end];
            output[written..written + slice.len()].copy_from_slice(slice);
            written += slice.len();
        }
        if written != output.len() {
            return Err("Remote read was not fully backed by cache blocks".into());
        }
        Ok(())
    }

    pub fn download_in_background_with_progress(
        self: &Arc<Self>,
        object: RemoteObject,
        on_progress: DownloadProgress,
    ) -> Result<bool, String> {
        let key = Self::object_prefix(&object);
        {
            let mut downloads = self
                .background_downloads
                .lock()
                .map_err(|_| "Background cache download state is poisoned".to_string())?;
            if !downloads.insert(key.clone()) {
                return Ok(false);
            }
        }
        let cache = self.clone();
        thread::spawn(move || {
            let _guard = BackgroundDownloadGuard {
                cache: cache.clone(),
                key,
            };
            let blocks = object.length.div_ceil(CACHE_BLOCK_BYTES);
            if blocks == 0 {
                on_progress(0, 0, None);
                return;
            }
            for index in 0..blocks {
                match cache.block(&object, index, IoPriority::Background) {
                    Ok(block) => {
                        let completed =
                            ((index * CACHE_BLOCK_BYTES) + block.len() as u64).min(object.length);
                        on_progress(completed, object.length, None);
                    }
                    Err(error) => {
                        on_progress(index * CACHE_BLOCK_BYTES, object.length, Some(error));
                        return;
                    }
                }
            }
        });
        Ok(true)
    }

    pub fn is_complete(&self, object: &RemoteObject) -> bool {
        Self::is_complete_at(&self.root, object)
    }

    pub fn is_complete_at(root: &Path, object: &RemoteObject) -> bool {
        (0..object.length.div_ceil(CACHE_BLOCK_BYTES)).all(|index| {
            let Ok((start, end)) = Self::block_bounds(object, index) else {
                return false;
            };
            let path = root.join(format!(
                "{}-{index:016x}.lrail-block",
                Self::object_prefix(object)
            ));
            fs::metadata(path)
                .is_ok_and(|metadata| metadata.is_file() && metadata.len() == end - start + 1)
        })
    }

    pub fn materialize(&self, object: &RemoteObject, output: &Path) -> Result<(), String> {
        if output.exists() {
            return Err("Refusing to overwrite a recovery package".into());
        }
        let parent = output
            .parent()
            .ok_or_else(|| "Recovery package has no parent directory".to_string())?;
        fs::create_dir_all(parent)
            .map_err(|error| format!("Unable to create recovery cache: {error}"))?;
        let mut temporary = NamedTempFile::new_in(parent)
            .map_err(|error| format!("Unable to create recovery package: {error}"))?;
        for index in 0..object.length.div_ceil(CACHE_BLOCK_BYTES) {
            let block = self.block(object, index, IoPriority::Background)?;
            temporary
                .write_all(&block)
                .map_err(|error| format!("Unable to write recovery package: {error}"))?;
        }
        if temporary
            .as_file()
            .metadata()
            .map_err(|error| error.to_string())?
            .len()
            != object.length
        {
            return Err("Recovery package length does not match Drive metadata".into());
        }
        temporary
            .as_file_mut()
            .sync_all()
            .map_err(|error| format!("Unable to flush recovery package: {error}"))?;
        temporary
            .persist_noclobber(output)
            .map_err(|error| format!("Unable to publish recovery package: {}", error.error))?;
        Ok(())
    }

    fn evict(&self) -> Result<(), String> {
        let usage = evict_directory(
            &self.root,
            self.maximum_bytes,
            self.maximum_files,
            &self.partial_prefix,
        )?;
        *self
            .usage
            .lock()
            .map_err(|_| "Ciphertext cache accounting lock is poisoned".to_string())? = usage;
        Ok(())
    }
}

struct BackgroundDownloadGuard {
    cache: Arc<RangeCache>,
    key: String,
}

impl Drop for BackgroundDownloadGuard {
    fn drop(&mut self) {
        if let Ok(mut downloads) = self.cache.background_downloads.lock() {
            downloads.remove(&self.key);
        }
    }
}

fn evict_directory(
    root: &Path,
    maximum_bytes: u64,
    maximum_files: usize,
    active_partial_prefix: &str,
) -> Result<CacheUsage, String> {
    let mut newest = BinaryHeap::<Reverse<(std::time::SystemTime, PathBuf, u64)>>::new();
    let mut usage = CacheUsage::default();
    for entry in fs::read_dir(root).map_err(|error| format!("Unable to inspect cache: {error}"))? {
        let entry = entry.map_err(|error| format!("Unable to inspect cache entry: {error}"))?;
        let path = entry.path();
        if let Some(name) = path.file_name().and_then(|value| value.to_str())
            && name.starts_with(".lrail-block-")
            && name.ends_with(".partial")
        {
            if !name.starts_with(active_partial_prefix) {
                fs::remove_file(&path)
                    .map_err(|error| format!("Unable to remove stale cache partial: {error}"))?;
            }
            continue;
        }
        if path.extension().and_then(|value| value.to_str()) != Some("lrail-block") {
            continue;
        }
        let metadata = entry
            .metadata()
            .map_err(|error| format!("Unable to inspect cache block: {error}"))?;
        if !metadata.is_file() {
            continue;
        }
        let candidate = Reverse((
            metadata
                .modified()
                .unwrap_or(std::time::SystemTime::UNIX_EPOCH),
            path,
            metadata.len(),
        ));
        newest.push(candidate);
        usage.bytes = usage.bytes.saturating_add(metadata.len());
        usage.files = usage.files.saturating_add(1);
        if newest.len() > maximum_files {
            let Reverse((_, path, bytes)) = newest.pop().expect("heap is non-empty");
            fs::remove_file(&path)
                .map_err(|error| format!("Unable to enforce cache file limit: {error}"))?;
            usage.bytes = usage.bytes.saturating_sub(bytes);
            usage.files = usage.files.saturating_sub(1);
        }
    }

    let mut retained = newest
        .into_iter()
        .map(|Reverse(entry)| entry)
        .collect::<Vec<_>>();
    retained.sort_by(|left, right| left.0.cmp(&right.0).then_with(|| left.1.cmp(&right.1)));
    for (_, path, bytes) in retained {
        if usage.bytes <= maximum_bytes {
            break;
        }
        fs::remove_file(&path)
            .map_err(|error| format!("Unable to enforce cache byte limit: {error}"))?;
        usage.bytes = usage.bytes.saturating_sub(bytes);
        usage.files = usage.files.saturating_sub(1);
    }
    Ok(usage)
}

pub struct CachedRandomAccessSource {
    label: String,
    object: RemoteObject,
    cache: Arc<RangeCache>,
    priority: IoPriority,
}

impl CachedRandomAccessSource {
    pub fn new(
        label: String,
        object: RemoteObject,
        cache: Arc<RangeCache>,
        priority: IoPriority,
    ) -> Self {
        Self {
            label,
            object,
            cache,
            priority,
        }
    }
}

impl RandomAccessSource for CachedRandomAccessSource {
    fn len(&self) -> PackageResult<u64> {
        Ok(self.object.length)
    }

    fn read_exact_at(&mut self, offset: u64, output: &mut [u8]) -> PackageResult<()> {
        self.cache
            .read_exact(&self.object, offset, output, self.priority)
            .map_err(|error| PackageError::Io(std::io::Error::other(error)))
    }

    fn label(&self) -> &str {
        &self.label
    }
}

#[cfg(test)]
mod tests {
    use super::{CACHE_BLOCK_BYTES, RangeCache, RangeTransport, RemoteObject};
    use crate::scheduler::{IoPriority, PriorityScheduler};
    use std::{
        path::PathBuf,
        sync::{
            Arc, Barrier, Mutex,
            atomic::{AtomicUsize, Ordering},
            mpsc,
        },
        thread,
        time::Duration,
    };

    struct FixtureTransport {
        bytes: Vec<u8>,
        ranges: Mutex<Vec<(u64, u64)>>,
    }

    impl RangeTransport for FixtureTransport {
        fn fetch_range(
            &self,
            _object: &RemoteObject,
            start: u64,
            end_inclusive: u64,
        ) -> Result<Vec<u8>, String> {
            self.ranges.lock().unwrap().push((start, end_inclusive));
            Ok(self.bytes[start as usize..=end_inclusive as usize].to_vec())
        }
    }

    struct ShortTransport;

    impl RangeTransport for ShortTransport {
        fn fetch_range(
            &self,
            _object: &RemoteObject,
            _start: u64,
            _end_inclusive: u64,
        ) -> Result<Vec<u8>, String> {
            Ok(vec![0; 1])
        }
    }

    struct BlockingTransport {
        bytes: Vec<u8>,
        entered: Arc<Barrier>,
        release: Arc<Barrier>,
        calls: Arc<AtomicUsize>,
    }

    impl RangeTransport for BlockingTransport {
        fn fetch_range(
            &self,
            _object: &RemoteObject,
            start: u64,
            end_inclusive: u64,
        ) -> Result<Vec<u8>, String> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            self.entered.wait();
            self.release.wait();
            Ok(self.bytes[start as usize..=end_inclusive as usize].to_vec())
        }
    }

    #[test]
    fn playback_reads_only_needed_blocks_before_complete_download() {
        let bytes = (0..(CACHE_BLOCK_BYTES * 3 + 17))
            .map(|index| (index % 251) as u8)
            .collect::<Vec<_>>();
        let transport = Arc::new(FixtureTransport {
            bytes: bytes.clone(),
            ranges: Mutex::new(Vec::new()),
        });
        let directory = tempfile::tempdir().unwrap();
        let cache = RangeCache::new(
            directory.path().to_path_buf(),
            transport.clone(),
            Arc::new(PriorityScheduler::default()),
        )
        .unwrap()
        .with_limit(CACHE_BLOCK_BYTES * 8);
        let object = RemoteObject {
            cache_key: "fixture".into(),
            length: bytes.len() as u64,
            version: "1".into(),
        };
        let mut output = vec![0; 4096];
        cache
            .read_exact(
                &object,
                CACHE_BLOCK_BYTES + 100,
                &mut output,
                IoPriority::Playback,
            )
            .unwrap();
        assert_eq!(
            output,
            bytes[(CACHE_BLOCK_BYTES + 100) as usize..(CACHE_BLOCK_BYTES + 100 + 4096) as usize]
        );
        assert_eq!(transport.ranges.lock().unwrap().len(), 1);
        assert!(!cache.is_complete(&object));

        let mut complete = vec![0; bytes.len()];
        cache
            .read_exact(&object, 0, &mut complete, IoPriority::Background)
            .unwrap();
        assert_eq!(complete, bytes);
        assert!(cache.is_complete(&object));
        let requests_after_fill = transport.ranges.lock().unwrap().len();
        let mut offline = vec![0; bytes.len()];
        cache
            .read_exact(&object, 0, &mut offline, IoPriority::Playback)
            .unwrap();
        assert_eq!(offline, bytes);
        assert_eq!(transport.ranges.lock().unwrap().len(), requests_after_fill);
    }

    #[test]
    fn background_download_reports_monotonic_real_byte_units_and_terminal_errors() {
        let bytes = vec![0x42; (CACHE_BLOCK_BYTES * 2 + 17) as usize];
        let directory = tempfile::tempdir().unwrap();
        let cache = Arc::new(
            RangeCache::new(
                directory.path().to_path_buf(),
                Arc::new(FixtureTransport {
                    bytes: bytes.clone(),
                    ranges: Mutex::new(Vec::new()),
                }),
                Arc::new(PriorityScheduler::default()),
            )
            .unwrap(),
        );
        let object = RemoteObject {
            cache_key: "progress-fixture".into(),
            length: bytes.len() as u64,
            version: "1".into(),
        };
        let (sender, receiver) = mpsc::channel();
        cache
            .download_in_background_with_progress(
                object.clone(),
                Arc::new(move |completed, total, error| {
                    sender.send((completed, total, error)).unwrap();
                }),
            )
            .unwrap();
        let mut updates = Vec::new();
        loop {
            let update = receiver.recv_timeout(Duration::from_secs(5)).unwrap();
            let complete = update.0 == object.length;
            updates.push(update);
            if complete {
                break;
            }
        }
        assert!(updates.windows(2).all(|pair| pair[0].0 < pair[1].0));
        assert!(
            updates
                .iter()
                .all(|update| update.1 == object.length && update.2.is_none())
        );
        assert_eq!(updates.last().unwrap().0, object.length);
        assert!(cache.is_complete(&object));

        let error_directory = tempfile::tempdir().unwrap();
        let error_cache = Arc::new(
            RangeCache::new(
                error_directory.path().to_path_buf(),
                Arc::new(ShortTransport),
                Arc::new(PriorityScheduler::default()),
            )
            .unwrap(),
        );
        let (sender, receiver) = mpsc::channel();
        error_cache
            .download_in_background_with_progress(
                RemoteObject {
                    cache_key: "progress-error".into(),
                    length: CACHE_BLOCK_BYTES,
                    version: "1".into(),
                },
                Arc::new(move |completed, total, error| {
                    sender.send((completed, total, error)).unwrap();
                }),
            )
            .unwrap();
        let failed = receiver.recv_timeout(Duration::from_secs(5)).unwrap();
        assert_eq!(failed.0, 0);
        assert_eq!(failed.1, CACHE_BLOCK_BYTES);
        assert!(failed.2.is_some());
    }

    #[test]
    fn repeated_background_download_of_one_object_reuses_the_inflight_transfer() {
        let entered = Arc::new(Barrier::new(2));
        let release = Arc::new(Barrier::new(2));
        let calls = Arc::new(AtomicUsize::new(0));
        let bytes = vec![0x31; CACHE_BLOCK_BYTES as usize];
        let directory = tempfile::tempdir().unwrap();
        let cache = Arc::new(
            RangeCache::new(
                directory.path().to_path_buf(),
                Arc::new(BlockingTransport {
                    bytes,
                    entered: entered.clone(),
                    release: release.clone(),
                    calls: calls.clone(),
                }),
                Arc::new(PriorityScheduler::default()),
            )
            .unwrap(),
        );
        let object = RemoteObject {
            cache_key: "same-transfer".into(),
            length: CACHE_BLOCK_BYTES,
            version: "1".into(),
        };
        let (sender, receiver) = mpsc::channel();
        assert!(
            cache
                .download_in_background_with_progress(
                    object.clone(),
                    Arc::new(move |completed, _, error| {
                        sender.send((completed, error)).unwrap();
                    }),
                )
                .unwrap()
        );
        entered.wait();
        assert!(
            !cache
                .download_in_background_with_progress(object, Arc::new(|_, _, _| {}))
                .unwrap()
        );
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        release.wait();
        let terminal = receiver.recv_timeout(Duration::from_secs(5)).unwrap();
        assert_eq!(terminal, (CACHE_BLOCK_BYTES, None));
    }

    #[test]
    fn corrupt_or_short_ranges_are_rejected_without_partial_output() {
        let directory = tempfile::tempdir().unwrap();
        let cache = RangeCache::new(
            directory.path().to_path_buf(),
            Arc::new(ShortTransport),
            Arc::new(PriorityScheduler::default()),
        )
        .unwrap();
        let object = RemoteObject {
            cache_key: "short".into(),
            length: CACHE_BLOCK_BYTES,
            version: "1".into(),
        };
        let mut output = vec![0x55; 32];
        assert!(
            cache
                .read_exact(&object, 0, &mut output, IoPriority::Playback)
                .is_err()
        );
        assert_eq!(output, vec![0x55; 32]);
    }

    #[test]
    fn eviction_enforces_both_byte_and_file_limits_over_the_complete_directory() {
        let directory = tempfile::tempdir().unwrap();
        for index in 0..7 {
            std::fs::write(
                directory
                    .path()
                    .join(format!("fixture-{index}.lrail-block")),
                vec![index as u8; 32],
            )
            .unwrap();
        }
        let cache = RangeCache::new(
            directory.path().to_path_buf(),
            Arc::new(ShortTransport),
            Arc::new(PriorityScheduler::default()),
        )
        .unwrap()
        .with_limits(80, 2);
        let blocks = std::fs::read_dir(directory.path())
            .unwrap()
            .filter_map(Result::ok)
            .filter(|entry| {
                entry.path().extension().and_then(|value| value.to_str()) == Some("lrail-block")
            })
            .collect::<Vec<_>>();
        let bytes = blocks
            .iter()
            .map(|entry| entry.metadata().unwrap().len())
            .sum::<u64>();
        assert!(blocks.len() <= 2);
        assert!(bytes <= 80);
        drop(cache);
    }

    #[test]
    fn object_version_change_uses_a_disjoint_cache_namespace() {
        let bytes = vec![0x42; CACHE_BLOCK_BYTES as usize];
        let transport = Arc::new(FixtureTransport {
            bytes,
            ranges: Mutex::new(Vec::new()),
        });
        let directory = tempfile::tempdir().unwrap();
        let cache = RangeCache::new(
            directory.path().to_path_buf(),
            transport.clone(),
            Arc::new(PriorityScheduler::default()),
        )
        .unwrap();
        let mut output = [0_u8; 32];
        for version in ["1", "2"] {
            cache
                .read_exact(
                    &RemoteObject {
                        cache_key: "same-file".into(),
                        length: CACHE_BLOCK_BYTES,
                        version: version.into(),
                    },
                    0,
                    &mut output,
                    IoPriority::Playback,
                )
                .unwrap();
        }
        assert_eq!(transport.ranges.lock().unwrap().len(), 2);
    }

    #[test]
    fn eviction_never_deletes_a_live_partial_writer() {
        let directory = tempfile::tempdir().unwrap();
        let stale = directory.path().join(".lrail-block-old.partial");
        std::fs::write(&stale, b"stale").unwrap();
        let entered = Arc::new(Barrier::new(2));
        let release = Arc::new(Barrier::new(2));
        let live_path = Arc::new(Mutex::new(None::<PathBuf>));
        let hook = {
            let entered = entered.clone();
            let release = release.clone();
            let live_path = live_path.clone();
            Arc::new(move |path: &std::path::Path| {
                *live_path.lock().unwrap() = Some(path.to_path_buf());
                entered.wait();
                release.wait();
            })
        };
        let cache = Arc::new(
            RangeCache::new(
                directory.path().to_path_buf(),
                Arc::new(FixtureTransport {
                    bytes: vec![0x5a; CACHE_BLOCK_BYTES as usize],
                    ranges: Mutex::new(Vec::new()),
                }),
                Arc::new(PriorityScheduler::default()),
            )
            .unwrap()
            .with_before_publish(hook),
        );
        assert!(!stale.exists());
        let object = RemoteObject {
            cache_key: "live-partial".into(),
            length: CACHE_BLOCK_BYTES,
            version: "1".into(),
        };
        let writer = {
            let cache = cache.clone();
            let object = object.clone();
            thread::spawn(move || {
                let mut output = [0_u8; 32];
                cache
                    .read_exact(&object, 0, &mut output, IoPriority::Playback)
                    .unwrap();
                output
            })
        };
        entered.wait();
        let partial = live_path.lock().unwrap().clone().unwrap();
        assert!(partial.is_file());
        cache.evict().unwrap();
        assert!(partial.is_file());
        release.wait();
        assert_eq!(writer.join().unwrap(), [0x5a; 32]);
        assert!(cache.is_complete(&object));
    }
}
