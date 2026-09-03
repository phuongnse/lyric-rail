use std::{
    collections::{HashMap, HashSet, VecDeque},
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant},
};

use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use keyring::v1::{Entry, Error as KeyringError};
use rand::{RngCore, rngs::OsRng};
use reqwest::{
    StatusCode,
    blocking::{Client, RequestBuilder, Response},
    header,
};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tauri::AppHandle;
use tauri_plugin_opener::OpenerExt;
use url::Url;
use zeroize::{Zeroize, Zeroizing};

use crate::{
    catalog::DriveRoot,
    range_cache::{CACHE_BLOCK_BYTES, RangeTransport, RemoteObject},
};

const DRIVE_SCOPE: &str = "https://www.googleapis.com/auth/drive.file";
const TOKEN_SERVICE: &str = "com.lyricrail.google-drive";
const MAX_CALLBACK_BYTES: usize = 16 * 1024;
const MAX_PICKED_IDS: usize = 2_000;
const MAX_FOLDER_DEPTH: usize = 16;
const MAX_DRIVE_FILES: usize = 20_000;
const MAX_DRIVE_PAGES: usize = 1_000;
const MAX_PAGE_TOKEN_BYTES: usize = 4 * 1024;
const MAX_DRIVE_OBJECT_BYTES: u64 = 256 * 1024 * 1024 * 1024;
const MAX_TOKEN_RESPONSE_BYTES: u64 = 64 * 1024;
const MAX_METADATA_RESPONSE_BYTES: u64 = 4 * 1024 * 1024;

fn bounded_json<T: DeserializeOwned>(
    response: Response,
    label: &str,
    maximum: u64,
) -> Result<T, String> {
    if response
        .content_length()
        .is_some_and(|length| length > maximum)
    {
        return Err(format!("{label} exceeds its response bound"));
    }
    let mut bytes = Vec::new();
    response
        .take(maximum + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| format!("Unable to read {label}: {error}"))?;
    if bytes.len() as u64 > maximum {
        return Err(format!("{label} exceeds its response bound"));
    }
    serde_json::from_slice(&bytes).map_err(|error| format!("{label} is invalid: {error}"))
}

#[derive(Clone)]
pub struct GoogleConfig {
    client_id: String,
    client_secret: Zeroizing<String>,
    endpoints: GoogleEndpoints,
}

#[derive(Clone)]
struct GoogleEndpoints {
    authorization: String,
    token: String,
    revoke: String,
    drive_api: String,
}

impl Default for GoogleEndpoints {
    fn default() -> Self {
        Self {
            authorization: "https://accounts.google.com/o/oauth2/v2/auth".into(),
            token: "https://oauth2.googleapis.com/token".into(),
            revoke: "https://oauth2.googleapis.com/revoke".into(),
            drive_api: "https://www.googleapis.com/drive/v3".into(),
        }
    }
}

impl GoogleConfig {
    pub fn from_environment() -> Result<Self, String> {
        let client_id = std::env::var("LYRICRAIL_GOOGLE_CLIENT_ID").map_err(|_| {
            "Google Drive is not configured. Set LYRICRAIL_GOOGLE_CLIENT_ID.".to_string()
        })?;
        if client_id.trim().is_empty()
            || client_id.len() > 512
            || client_id.contains(['\r', '\n', '\0'])
        {
            return Err("Google OAuth client ID is invalid".into());
        }
        let client_secret = std::env::var("LYRICRAIL_GOOGLE_CLIENT_SECRET").unwrap_or_default();
        if client_secret.len() > 1024 || client_secret.contains(['\r', '\n', '\0']) {
            return Err("Google OAuth client secret is invalid".into());
        }
        Ok(Self {
            client_id,
            client_secret: Zeroizing::new(client_secret),
            endpoints: GoogleEndpoints::default(),
        })
    }

    fn token_account(&self) -> String {
        let mut digest = Sha256::new();
        digest.update(self.client_id.as_bytes());
        format!("drive-refresh-v1-{}", &hex::encode(digest.finalize())[..24])
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DriveFile {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub mime_type: String,
    #[serde(default, deserialize_with = "deserialize_u64_string")]
    pub size: u64,
    #[serde(default)]
    pub version: String,
    #[serde(default)]
    pub modified_time: Option<String>,
    #[serde(default)]
    pub md5_checksum: Option<String>,
    #[serde(default)]
    pub capabilities: DriveCapabilities,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DriveCapabilities {
    #[serde(default)]
    pub can_download: bool,
}

fn deserialize_u64_string<'de, D>(deserializer: D) -> Result<u64, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let value = Option::<String>::deserialize(deserializer)?;
    value
        .as_deref()
        .unwrap_or("0")
        .parse()
        .map_err(serde::de::Error::custom)
}

impl DriveFile {
    fn validate(&self) -> Result<(), String> {
        if !valid_drive_id(&self.id)
            || self.name.is_empty()
            || self.name.len() > 1024
            || self.name.contains(['\r', '\n', '\0'])
            || self.mime_type.len() > 256
            || self.version.len() > 256
            || self
                .modified_time
                .as_ref()
                .is_some_and(|value| value.len() > 128)
            || self
                .md5_checksum
                .as_ref()
                .is_some_and(|value| value.len() > 128)
        {
            return Err("Drive returned invalid bounded file metadata".into());
        }
        Ok(())
    }

    pub fn is_folder(&self) -> bool {
        self.mime_type == "application/vnd.google-apps.folder"
    }

    pub fn is_lrail(&self) -> bool {
        !self.is_folder()
            && self
                .name
                .rsplit_once('.')
                .is_some_and(|(_, extension)| extension.eq_ignore_ascii_case("lrail"))
            && self.size > 0
            && self.size <= MAX_DRIVE_OBJECT_BYTES
            && !self.version.is_empty()
            && self.capabilities.can_download
    }

    pub fn remote_object(&self) -> RemoteObject {
        RemoteObject {
            cache_key: format!("google-drive:{}", self.id),
            length: self.size,
            version: self.version.clone(),
        }
    }
}

#[derive(Deserialize)]
struct TokenResponse {
    access_token: String,
    #[serde(default)]
    refresh_token: Option<String>,
    expires_in: u64,
}

struct AccessToken {
    value: Zeroizing<String>,
    expires_at: Instant,
}

trait DriveCredentialStore: Send + Sync {
    fn get(&self) -> Result<Option<Zeroizing<String>>, String>;
    fn set(&self, value: &str) -> Result<(), String>;
    fn delete(&self) -> Result<(), String>;
}

struct KeyringDriveCredentialStore {
    account: String,
}

impl KeyringDriveCredentialStore {
    fn new(config: &GoogleConfig) -> Self {
        Self {
            account: config.token_account(),
        }
    }

    fn entry(&self) -> Result<Entry, String> {
        Entry::new(TOKEN_SERVICE, &self.account)
            .map_err(|error| format!("Unable to open Drive credential store: {error}"))
    }
}

impl DriveCredentialStore for KeyringDriveCredentialStore {
    fn get(&self) -> Result<Option<Zeroizing<String>>, String> {
        match self.entry()?.get_secret() {
            Ok(secret) if !secret.is_empty() => Ok(Some(Zeroizing::new(
                String::from_utf8(secret)
                    .map_err(|_| "Stored Google refresh token is not valid UTF-8".to_string())?,
            ))),
            Ok(_) | Err(KeyringError::NoEntry) => Ok(None),
            Err(error) => Err(format!("Unable to read Drive credential: {error}")),
        }
    }

    fn set(&self, value: &str) -> Result<(), String> {
        self.entry()?
            .set_secret(value.as_bytes())
            .map_err(|error| format!("Unable to store Drive credential: {error}"))
    }

    fn delete(&self) -> Result<(), String> {
        match self.entry()?.delete_credential() {
            Ok(()) | Err(KeyringError::NoEntry) => Ok(()),
            Err(error) => Err(format!("Unable to remove Drive credential: {error}")),
        }
    }
}

pub struct GoogleTokenProvider {
    config: GoogleConfig,
    client: Client,
    access: Mutex<Option<AccessToken>>,
    credentials: Arc<dyn DriveCredentialStore>,
}

impl GoogleTokenProvider {
    fn new(config: GoogleConfig, initial: Option<TokenResponse>) -> Result<Self, String> {
        let credentials = Arc::new(KeyringDriveCredentialStore::new(&config));
        Self::new_with_store(config, initial, credentials)
    }

    fn new_with_store(
        config: GoogleConfig,
        initial: Option<TokenResponse>,
        credentials: Arc<dyn DriveCredentialStore>,
    ) -> Result<Self, String> {
        let client = Client::builder()
            .connect_timeout(Duration::from_secs(15))
            .timeout(Duration::from_secs(60))
            .redirect(reqwest::redirect::Policy::limited(4))
            .build()
            .map_err(|error| format!("Unable to build Google client: {error}"))?;
        let provider = Self {
            config,
            client,
            access: Mutex::new(None),
            credentials,
        };
        if let Some(token) = initial {
            provider.accept_token(token)?;
        }
        Ok(provider)
    }

    pub fn from_saved(config: GoogleConfig) -> Result<Arc<Self>, String> {
        let provider = Arc::new(Self::new(config, None)?);
        if provider.credentials.get()?.is_some() {
            Ok(provider)
        } else {
            Err("Google Drive is not connected".into())
        }
    }

    fn accept_token(&self, mut token: TokenResponse) -> Result<(), String> {
        if token.access_token.is_empty() || token.access_token.len() > 8192 {
            token.access_token.zeroize();
            return Err("Google returned an invalid access token".into());
        }
        if let Some(refresh) = token.refresh_token.take() {
            let refresh = Zeroizing::new(refresh);
            if refresh.is_empty() || refresh.len() > 8192 {
                return Err("Google returned an invalid refresh token".into());
            }
            self.credentials.set(refresh.as_str())?;
        }
        *self
            .access
            .lock()
            .map_err(|_| "Google access-token lock is poisoned".to_string())? = Some(AccessToken {
            value: Zeroizing::new(token.access_token),
            expires_at: Instant::now() + Duration::from_secs(token.expires_in.max(60)),
        });
        Ok(())
    }

    fn refresh_with(&self, refresh: Zeroizing<String>) -> Result<(), String> {
        let mut form = vec![
            ("client_id", self.config.client_id.as_str()),
            ("refresh_token", refresh.as_str()),
            ("grant_type", "refresh_token"),
        ];
        if !self.config.client_secret.is_empty() {
            form.push(("client_secret", self.config.client_secret.as_str()));
        }
        let response = self
            .client
            .post(&self.config.endpoints.token)
            .form(&form)
            .send()
            .map_err(|error| format!("Google token refresh failed: {error}"))?;
        if !response.status().is_success() {
            return Err(format!(
                "Google token refresh was rejected with HTTP {}",
                response.status()
            ));
        }
        let token = bounded_json(response, "Google token response", MAX_TOKEN_RESPONSE_BYTES)?;
        self.accept_token(token)
    }

    fn refresh(&self) -> Result<(), String> {
        let refresh = self
            .credentials
            .get()?
            .ok_or_else(|| "Google Drive is not connected".to_string())?;
        self.refresh_with(refresh)
    }

    pub fn access_token(&self) -> Result<Zeroizing<String>, String> {
        {
            let access = self
                .access
                .lock()
                .map_err(|_| "Google access-token lock is poisoned".to_string())?;
            if let Some(token) = access.as_ref()
                && token.expires_at > Instant::now() + Duration::from_secs(60)
            {
                return Ok(Zeroizing::new(token.value.to_string()));
            }
        }
        self.refresh()?;
        self.access
            .lock()
            .map_err(|_| "Google access-token lock is poisoned".to_string())?
            .as_ref()
            .map(|token| Zeroizing::new(token.value.to_string()))
            .ok_or_else(|| "Google token refresh produced no access token".into())
    }

    pub fn disconnect(&self) -> Result<(), String> {
        if let Ok(mut access) = self.access.lock() {
            access.take();
        }
        if let Some(token) = self.credentials.get()? {
            let _ = self
                .client
                .post(&self.config.endpoints.revoke)
                .form(&[("token", token.as_str())])
                .send();
        }
        self.credentials.delete()
    }

    fn clear_access(&self) {
        if let Ok(mut access) = self.access.lock() {
            access.take();
        }
    }
}

fn authorized_drive_response(
    tokens: &Arc<GoogleTokenProvider>,
    mut request: impl FnMut(&str) -> RequestBuilder,
    label: &str,
) -> Result<Response, String> {
    for attempt in 0..2 {
        let token = tokens.access_token()?;
        let response = request(token.as_str())
            .send()
            .map_err(|error| format!("{label} failed: {error}"))?;
        if response.status() == StatusCode::UNAUTHORIZED && attempt == 0 {
            tokens.clear_access();
            continue;
        }
        return Ok(response);
    }
    Err("Google Drive authorization expired".into())
}

pub struct GoogleDriveTransport {
    client: Client,
    tokens: Arc<GoogleTokenProvider>,
    drive_api: String,
}

impl GoogleDriveTransport {
    pub fn new(tokens: Arc<GoogleTokenProvider>) -> Result<Self, String> {
        let drive_api = tokens.config.endpoints.drive_api.clone();
        Ok(Self {
            client: Client::builder()
                .connect_timeout(Duration::from_secs(15))
                .timeout(Duration::from_secs(90))
                .redirect(reqwest::redirect::Policy::limited(4))
                .build()
                .map_err(|error| format!("Unable to build Drive range client: {error}"))?,
            tokens,
            drive_api,
        })
    }
}

fn validate_drive_range_response(
    status: StatusCode,
    content_range: Option<&str>,
    object_length: u64,
    start: u64,
    end_inclusive: u64,
) -> Result<(), String> {
    if status != StatusCode::PARTIAL_CONTENT {
        return Err(format!("Drive range request returned HTTP {status}"));
    }
    let expected = format!("bytes {start}-{end_inclusive}/{object_length}");
    if content_range != Some(expected.as_str()) {
        return Err("Drive returned an unexpected Content-Range".into());
    }
    Ok(())
}

impl RangeTransport for GoogleDriveTransport {
    fn fetch_range(
        &self,
        object: &RemoteObject,
        start: u64,
        end_inclusive: u64,
    ) -> Result<Vec<u8>, String> {
        let expected = end_inclusive
            .checked_sub(start)
            .and_then(|value| value.checked_add(1))
            .ok_or_else(|| "Drive range bounds are invalid".to_string())?;
        if expected == 0 || expected > CACHE_BLOCK_BYTES {
            return Err("Drive range exceeds the transport allocation bound".into());
        }
        if end_inclusive >= object.length {
            return Err("Drive range exceeds the declared object length".into());
        }
        let file_id = object
            .cache_key
            .strip_prefix("google-drive:")
            .ok_or_else(|| "Invalid Google Drive cache key".to_string())?;
        let url = format!("{}/files/{file_id}", self.drive_api);
        for attempt in 0..2 {
            let token = self.tokens.access_token()?;
            let response = self
                .client
                .get(&url)
                .query(&[("alt", "media"), ("supportsAllDrives", "true")])
                .bearer_auth(token.as_str())
                .header(header::RANGE, format!("bytes={start}-{end_inclusive}"))
                .send()
                .map_err(|error| format!("Drive range request failed: {error}"))?;
            if response.status() == StatusCode::UNAUTHORIZED && attempt == 0 {
                self.tokens.clear_access();
                continue;
            }
            validate_drive_range_response(
                response.status(),
                response
                    .headers()
                    .get(header::CONTENT_RANGE)
                    .and_then(|value| value.to_str().ok()),
                object.length,
                start,
                end_inclusive,
            )?;
            if let Some(content_length) = response.content_length()
                && content_length != expected
            {
                return Err("Drive returned an unexpected Content-Length".into());
            }
            let expected = usize::try_from(expected)
                .map_err(|_| "Drive range exceeds this platform".to_string())?;
            let mut bytes = Vec::with_capacity(expected);
            response
                .take(expected as u64 + 1)
                .read_to_end(&mut bytes)
                .map_err(|error| format!("Unable to read Drive range: {error}"))?;
            if bytes.len() != expected {
                return Err(format!(
                    "Drive range returned {} bytes; expected {expected}",
                    bytes.len()
                ));
            }
            return Ok(bytes);
        }
        Err("Drive authorization expired".into())
    }
}

fn random_url_token(bytes: usize) -> String {
    let mut value = vec![0_u8; bytes];
    OsRng.fill_bytes(&mut value);
    URL_SAFE_NO_PAD.encode(value)
}

fn valid_drive_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 256
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

fn valid_page_token(value: &str) -> bool {
    !value.is_empty() && value.len() <= MAX_PAGE_TOKEN_BYTES && !value.contains(['\r', '\n', '\0'])
}

fn auth_url(config: &GoogleConfig, redirect_uri: &str, state: &str, challenge: &str) -> Url {
    let mut url = Url::parse(&config.endpoints.authorization).unwrap();
    url.query_pairs_mut()
        .append_pair("client_id", &config.client_id)
        .append_pair("redirect_uri", redirect_uri)
        .append_pair("response_type", "code")
        .append_pair("scope", DRIVE_SCOPE)
        .append_pair("access_type", "offline")
        .append_pair("prompt", "consent")
        .append_pair("state", state)
        .append_pair("code_challenge", challenge)
        .append_pair("code_challenge_method", "S256")
        .append_pair("trigger_onepick", "true")
        .append_pair("allow_multiple", "true")
        .append_pair("allow_folder_selection", "true");
    url
}

#[derive(Debug)]
struct OAuthCallback {
    code: Zeroizing<String>,
    picked_file_ids: Vec<String>,
}

fn parse_callback(target: &str, expected_state: &str) -> Result<OAuthCallback, String> {
    let url = Url::parse(&format!("http://127.0.0.1{target}"))
        .map_err(|_| "OAuth callback URL is invalid".to_string())?;
    let mut values = HashMap::<String, String>::new();
    for (key, value) in url.query_pairs() {
        if matches!(key.as_ref(), "state" | "code" | "error" | "picked_file_ids")
            && values
                .insert(key.into_owned(), value.into_owned())
                .is_some()
        {
            return Err("OAuth callback contains a duplicate security field".into());
        }
    }
    if values.get("state").map(String::as_str) != Some(expected_state) {
        return Err("OAuth state mismatch".into());
    }
    if let Some(error) = values.get("error") {
        return Err(format!("Google authorization was rejected: {error}"));
    }
    let code = values
        .get("code")
        .cloned()
        .filter(|value| !value.is_empty() && value.len() <= 8192)
        .ok_or_else(|| "OAuth callback has no authorization code".to_string())?;
    let picked = values
        .get("picked_file_ids")
        .map(String::as_str)
        .unwrap_or("")
        .split(',')
        .take(MAX_PICKED_IDS + 1)
        .map(str::to_owned)
        .collect::<Vec<_>>();
    if picked.is_empty()
        || picked.len() > MAX_PICKED_IDS
        || picked.iter().any(|value| !valid_drive_id(value))
    {
        return Err("Select between 1 and 2,000 Drive files or folders".into());
    }
    Ok(OAuthCallback {
        code: Zeroizing::new(code),
        picked_file_ids: picked,
    })
}

fn handle_callback(mut stream: TcpStream, expected_state: &str) -> Result<OAuthCallback, String> {
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .map_err(|error| error.to_string())?;
    let mut request = Vec::with_capacity(1024);
    let mut chunk = [0_u8; 1024];
    while request.len() < MAX_CALLBACK_BYTES {
        let read = stream
            .read(&mut chunk)
            .map_err(|error| format!("Unable to read OAuth callback: {error}"))?;
        if read == 0 {
            break;
        }
        request.extend_from_slice(&chunk[..read]);
        if request.windows(2).any(|window| window == b"\r\n") {
            break;
        }
    }
    if request.len() >= MAX_CALLBACK_BYTES {
        return Err("OAuth callback exceeds the request bound".into());
    }
    let request = std::str::from_utf8(&request)
        .map_err(|_| "OAuth callback is not valid UTF-8".to_string())?;
    let request_line = request
        .lines()
        .next()
        .ok_or_else(|| "OAuth callback request line is invalid".to_string())?;
    let mut request_parts = request_line.split_whitespace();
    if request_parts.next() != Some("GET") {
        return Err("OAuth callback must use GET".into());
    }
    let target = request_parts
        .next()
        .ok_or_else(|| "OAuth callback target is missing".to_string())?;
    if !request_parts
        .next()
        .is_some_and(|version| version.starts_with("HTTP/1."))
    {
        return Err("OAuth callback HTTP version is invalid".into());
    }
    let result = parse_callback(target, expected_state);
    let (status, body) = if result.is_ok() {
        (
            "200 OK",
            "LyricRail connected to Google Drive. You can close this tab.",
        )
    } else {
        (
            "400 Bad Request",
            "LyricRail could not complete Google Drive authorization.",
        )
    };
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let _ = stream.write_all(response.as_bytes());
    result
}

pub fn authorize_and_pick(
    app: &AppHandle,
) -> Result<(Arc<GoogleTokenProvider>, Vec<String>), String> {
    let config = GoogleConfig::from_environment()?;
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .map_err(|error| format!("Unable to open OAuth callback listener: {error}"))?;
    listener
        .set_nonblocking(true)
        .map_err(|error| format!("Unable to configure OAuth callback listener: {error}"))?;
    let port = listener
        .local_addr()
        .map_err(|error| error.to_string())?
        .port();
    let redirect_uri = format!("http://127.0.0.1:{port}");
    let state = random_url_token(32);
    let verifier = Zeroizing::new(random_url_token(64));
    let challenge = URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()));
    let url = auth_url(&config, &redirect_uri, &state, &challenge);
    app.opener()
        .open_url(url.as_str(), None::<&str>)
        .map_err(|error| format!("Unable to open Google authorization: {error}"))?;

    let deadline = Instant::now() + Duration::from_secs(300);
    let callback = loop {
        match listener.accept() {
            Ok((stream, _)) => match handle_callback(stream, &state) {
                Ok(callback) => break callback,
                Err(error) if error.starts_with("Google authorization was rejected:") => {
                    return Err(error);
                }
                Err(_) => continue,
            },
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                if Instant::now() >= deadline {
                    return Err("Google authorization timed out".into());
                }
                thread::sleep(Duration::from_millis(50));
            }
            Err(error) => return Err(format!("OAuth callback failed: {error}")),
        }
    };

    let client = Client::builder()
        .timeout(Duration::from_secs(60))
        .build()
        .map_err(|error| error.to_string())?;
    let token = exchange_authorization_code(
        &config,
        &client,
        callback.code.as_str(),
        verifier.as_str(),
        &redirect_uri,
    )?;
    let provider = Arc::new(GoogleTokenProvider::new(config, Some(token))?);
    Ok((provider, callback.picked_file_ids))
}

fn exchange_authorization_code(
    config: &GoogleConfig,
    client: &Client,
    code: &str,
    verifier: &str,
    redirect_uri: &str,
) -> Result<TokenResponse, String> {
    let mut form = vec![
        ("client_id", config.client_id.as_str()),
        ("code", code),
        ("code_verifier", verifier),
        ("redirect_uri", redirect_uri),
        ("grant_type", "authorization_code"),
    ];
    if !config.client_secret.is_empty() {
        form.push(("client_secret", config.client_secret.as_str()));
    }
    let response = client
        .post(&config.endpoints.token)
        .form(&form)
        .send()
        .map_err(|error| format!("Google token exchange failed: {error}"))?;
    if !response.status().is_success() {
        return Err(format!(
            "Google token exchange was rejected with HTTP {}",
            response.status()
        ));
    }
    bounded_json(response, "Google token response", MAX_TOKEN_RESPONSE_BYTES)
}

const FILE_FIELDS: &str =
    "id,name,mimeType,size,version,modifiedTime,md5Checksum,capabilities(canDownload)";

fn get_file(
    client: &Client,
    tokens: &Arc<GoogleTokenProvider>,
    id: &str,
) -> Result<DriveFile, String> {
    let url = format!("{}/files/{id}", tokens.config.endpoints.drive_api);
    let response = authorized_drive_response(
        tokens,
        |token| {
            client
                .get(&url)
                .query(&[("supportsAllDrives", "true"), ("fields", FILE_FIELDS)])
                .bearer_auth(token)
        },
        "Unable to inspect Drive selection",
    )?;
    if !response.status().is_success() {
        return Err(format!(
            "Drive selection metadata returned HTTP {}",
            response.status()
        ));
    }
    let file: DriveFile = bounded_json(
        response,
        "Drive selection metadata",
        MAX_METADATA_RESPONSE_BYTES,
    )?;
    file.validate()?;
    Ok(file)
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct FileList {
    #[serde(default)]
    files: Vec<DriveFile>,
    next_page_token: Option<String>,
    #[serde(default)]
    incomplete_search: bool,
}

fn list_children(
    client: &Client,
    tokens: &Arc<GoogleTokenProvider>,
    folder_id: &str,
    maximum_entries: usize,
) -> Result<Vec<DriveFile>, String> {
    if maximum_entries > MAX_DRIVE_FILES {
        return Err("Drive folder expansion entry budget is invalid".into());
    }
    let mut output = Vec::new();
    let mut page_token: Option<String> = None;
    let mut seen_page_tokens = HashSet::new();
    let mut pages = 0_usize;
    loop {
        pages += 1;
        if pages > MAX_DRIVE_PAGES {
            return Err("Drive folder pagination exceeds its bound".into());
        }
        let query = format!("'{folder_id}' in parents and trashed = false");
        let url = format!("{}/files", tokens.config.endpoints.drive_api);
        let fields = format!("nextPageToken,files({FILE_FIELDS})");
        let response = authorized_drive_response(
            tokens,
            |token| {
                let mut request = client
                    .get(&url)
                    .query(&[
                        ("q", query.as_str()),
                        ("spaces", "drive"),
                        ("pageSize", "1000"),
                        ("supportsAllDrives", "true"),
                        ("includeItemsFromAllDrives", "true"),
                        ("fields", fields.as_str()),
                    ])
                    .bearer_auth(token);
                if let Some(page_token) = page_token.as_deref() {
                    request = request.query(&[("pageToken", page_token)]);
                }
                request
            },
            "Unable to list Drive folder",
        )?;
        if !response.status().is_success() {
            return Err(format!(
                "Drive folder listing returned HTTP {}",
                response.status()
            ));
        }
        let page: FileList = bounded_json(
            response,
            "Drive folder listing",
            MAX_METADATA_RESPONSE_BYTES,
        )?;
        if page.incomplete_search {
            return Err("Drive reported an incomplete folder search".into());
        }
        for file in &page.files {
            file.validate()?;
        }
        output.extend(page.files);
        if output.len() > maximum_entries {
            return Err(format!(
                "Drive source exceeds {MAX_DRIVE_FILES} discovered entries"
            ));
        }
        page_token = page.next_page_token;
        if let Some(token) = page_token.as_ref() {
            if !valid_page_token(token) {
                return Err("Drive folder returned an invalid bounded page token".into());
            }
            if !seen_page_tokens.insert(token.clone()) {
                return Err("Drive folder returned a repeated page token".into());
            }
        }
        if page_token.is_none() {
            return Ok(output);
        }
    }
}

pub fn resolve_selection_roots(
    tokens: &Arc<GoogleTokenProvider>,
    selected_ids: Vec<String>,
) -> Result<Vec<DriveRoot>, String> {
    if selected_ids.is_empty() || selected_ids.len() > MAX_PICKED_IDS {
        return Err("Select between 1 and 2,000 Drive files or folders".into());
    }
    let client = Client::builder()
        .timeout(Duration::from_secs(60))
        .build()
        .map_err(|error| error.to_string())?;
    let mut roots = Vec::new();
    let mut seen = HashSet::new();
    for id in selected_ids {
        if !seen.insert(id.clone()) {
            continue;
        }
        match get_file(&client, tokens, &id) {
            Ok(file) => {
                let is_folder = file.is_folder();
                roots.push(DriveRoot {
                    file_id: file.id,
                    name: file.name,
                    is_folder,
                });
            }
            Err(_) => roots.push(DriveRoot {
                file_id: id.clone(),
                name: id,
                is_folder: false,
            }),
        }
    }
    roots.sort_by(|left, right| left.file_id.cmp(&right.file_id));
    Ok(roots)
}

pub fn expand_drive_root(
    tokens: &Arc<GoogleTokenProvider>,
    root_id: &str,
) -> Result<Vec<DriveFile>, String> {
    let client = Client::builder()
        .timeout(Duration::from_secs(60))
        .build()
        .map_err(|error| error.to_string())?;
    let mut queue = VecDeque::new();
    let root = get_file(&client, tokens, root_id)?;
    let mut discovered = HashSet::from([root.id.clone()]);
    queue.push_back((root, 0_usize));
    let mut files = Vec::new();
    while let Some((file, depth)) = queue.pop_front() {
        if file.is_folder() {
            if depth >= MAX_FOLDER_DEPTH {
                return Err(format!("Drive folder nesting exceeds {MAX_FOLDER_DEPTH}"));
            }
            let remaining = MAX_DRIVE_FILES.saturating_sub(discovered.len());
            let children = list_children(&client, tokens, &file.id, remaining)?;
            enqueue_discovered(&mut queue, &mut discovered, children, depth + 1)?;
        } else if file.is_lrail() {
            files.push(file);
        }
    }
    files.sort_by(|left, right| {
        left.name
            .cmp(&right.name)
            .then_with(|| left.id.cmp(&right.id))
    });
    Ok(files)
}

fn enqueue_discovered(
    queue: &mut VecDeque<(DriveFile, usize)>,
    discovered: &mut HashSet<String>,
    children: Vec<DriveFile>,
    depth: usize,
) -> Result<(), String> {
    for child in children {
        if discovered.contains(&child.id) {
            continue;
        }
        if discovered.len() >= MAX_DRIVE_FILES {
            return Err(format!(
                "Drive source exceeds {MAX_DRIVE_FILES} discovered entries"
            ));
        }
        discovered.insert(child.id.clone());
        queue.push_back((child, depth));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        DriveCredentialStore, GoogleConfig, GoogleDriveTransport, GoogleEndpoints,
        GoogleTokenProvider, MAX_DRIVE_FILES, TokenResponse, auth_url, enqueue_discovered,
        exchange_authorization_code, expand_drive_root, parse_callback, valid_page_token,
        validate_drive_range_response,
    };
    use crate::{
        range_cache::{
            CACHE_BLOCK_BYTES, CachedRandomAccessSource, RangeCache, RangeTransport, RemoteObject,
        },
        scheduler::{IoPriority, PriorityScheduler},
    };
    use lrail_format::{
        AssetRequest, ContentEncoding, PackageReader, PackageRequest, pack_for_vault,
    };
    use reqwest::blocking::Client;
    use serde_json::json;
    use std::{
        fs,
        io::{Read, Write},
        net::{TcpListener, TcpStream},
        sync::{
            Arc, Mutex,
            atomic::{AtomicBool, Ordering},
        },
        thread,
        time::{Duration, Instant},
    };
    use zeroize::Zeroizing;

    #[derive(Default)]
    struct MemoryStore(Mutex<Option<Zeroizing<String>>>);

    impl DriveCredentialStore for MemoryStore {
        fn get(&self) -> Result<Option<Zeroizing<String>>, String> {
            Ok(self
                .0
                .lock()
                .unwrap()
                .as_ref()
                .map(|value| Zeroizing::new(value.to_string())))
        }

        fn set(&self, value: &str) -> Result<(), String> {
            *self.0.lock().unwrap() = Some(Zeroizing::new(value.to_owned()));
            Ok(())
        }

        fn delete(&self) -> Result<(), String> {
            self.0.lock().unwrap().take();
            Ok(())
        }
    }

    fn config_with_base(base: &str) -> GoogleConfig {
        GoogleConfig {
            client_id: "public-client-id.apps.googleusercontent.com".into(),
            client_secret: Zeroizing::new(String::new()),
            endpoints: GoogleEndpoints {
                authorization: format!("{base}/auth"),
                token: format!("{base}/token"),
                revoke: format!("{base}/revoke"),
                drive_api: format!("{base}/drive"),
            },
        }
    }

    fn config() -> GoogleConfig {
        config_with_base("https://fixture.invalid")
    }

    fn fixture_file(id: &str) -> super::DriveFile {
        super::DriveFile {
            id: id.into(),
            name: format!("{id}.lrail"),
            mime_type: "application/octet-stream".into(),
            size: 1,
            version: "1".into(),
            modified_time: None,
            md5_checksum: None,
            capabilities: super::DriveCapabilities { can_download: true },
        }
    }

    fn http_response(status: &str, headers: &[(&str, String)], body: &[u8]) -> Vec<u8> {
        let mut response = format!(
            "HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n",
            body.len()
        );
        for (name, value) in headers {
            response.push_str(&format!("{name}: {value}\r\n"));
        }
        response.push_str("\r\n");
        let mut encoded = response.into_bytes();
        encoded.extend_from_slice(body);
        encoded
    }

    fn fixture_server(
        responses: Vec<Vec<u8>>,
    ) -> (String, Arc<Mutex<Vec<String>>>, thread::JoinHandle<()>) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let requests = Arc::new(Mutex::new(Vec::new()));
        let observed = requests.clone();
        let handle = thread::spawn(move || {
            for response in responses {
                let (mut stream, _) = listener.accept().unwrap();
                let bytes = read_http_request(&mut stream);
                observed
                    .lock()
                    .unwrap()
                    .push(String::from_utf8_lossy(&bytes).into_owned());
                let _ = stream.write_all(&response);
            }
        });
        (base, requests, handle)
    }

    fn read_http_request(stream: &mut TcpStream) -> Vec<u8> {
        let mut bytes = Vec::new();
        let mut buffer = [0_u8; 2048];
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            let read = match stream.read(&mut buffer) {
                Ok(read) => read,
                Err(error)
                    if matches!(
                        error.kind(),
                        std::io::ErrorKind::WouldBlock
                            | std::io::ErrorKind::Interrupted
                            | std::io::ErrorKind::TimedOut
                    ) && Instant::now() < deadline =>
                {
                    thread::sleep(Duration::from_millis(2));
                    continue;
                }
                Err(error) => panic!("fixture request read failed: {error}"),
            };
            if read == 0 {
                break;
            }
            bytes.extend_from_slice(&buffer[..read]);
            let Some(header_end) = bytes.windows(4).position(|value| value == b"\r\n\r\n") else {
                continue;
            };
            let headers = String::from_utf8_lossy(&bytes[..header_end + 4]);
            let content_length = headers
                .lines()
                .filter_map(|line| line.split_once(':'))
                .find(|(name, _)| name.eq_ignore_ascii_case("content-length"))
                .map(|(_, value)| value.trim())
                .and_then(|value| value.parse::<usize>().ok())
                .unwrap_or(0);
            if bytes.len() >= header_end + 4 + content_length {
                break;
            }
        }
        bytes
    }

    type DynamicRangeFixture = (
        String,
        Arc<Mutex<Vec<(u64, u64)>>>,
        Arc<AtomicBool>,
        thread::JoinHandle<()>,
    );

    fn dynamic_range_server(object: Arc<Vec<u8>>) -> DynamicRangeFixture {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        listener.set_nonblocking(true).unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let ranges = Arc::new(Mutex::new(Vec::new()));
        let observed = ranges.clone();
        let stop = Arc::new(AtomicBool::new(false));
        let stopping = stop.clone();
        let handle = thread::spawn(move || {
            while !stopping.load(Ordering::Relaxed) {
                let (mut stream, _) = match listener.accept() {
                    Ok(connection) => connection,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2));
                        continue;
                    }
                    Err(error) => panic!("range fixture accept failed: {error}"),
                };
                let request = String::from_utf8_lossy(&read_http_request(&mut stream)).into_owned();
                let range = request
                    .lines()
                    .filter_map(|line| line.split_once(':'))
                    .find(|(name, _)| name.eq_ignore_ascii_case("range"))
                    .map(|(_, value)| value.trim())
                    .and_then(|value| value.strip_prefix("bytes="))
                    .and_then(|value| value.split_once('-'))
                    .map(|(start, end)| {
                        (start.parse::<u64>().unwrap(), end.parse::<u64>().unwrap())
                    })
                    .unwrap();
                observed.lock().unwrap().push(range);
                let body = &object[range.0 as usize..=range.1 as usize];
                let response = http_response(
                    "206 Partial Content",
                    &[(
                        "Content-Range",
                        format!("bytes {}-{}/{}", range.0, range.1, object.len()),
                    )],
                    body,
                );
                stream.write_all(&response).unwrap();
            }
        });
        (base, ranges, stop, handle)
    }

    #[test]
    fn picker_flow_is_pkce_state_bound_and_file_scoped() {
        let config = config();
        let url = auth_url(&config, "http://127.0.0.1:43210", "state", "challenge");
        let query = url.query().unwrap();
        assert!(query.contains("drive.file"));
        assert!(query.contains("code_challenge_method=S256"));
        assert!(query.contains("trigger_onepick=true"));
        assert!(query.contains("allow_multiple=true"));
        assert!(query.contains("allow_folder_selection=true"));
        assert!(!query.contains("picker_allow_"));
        assert!(!query.contains("client_secret"));
        assert!(!config.token_account().contains(&config.client_id));

        let callback = parse_callback(
            "/?state=state&code=authorization&picked_file_ids=one%2Ctwo",
            "state",
        )
        .unwrap();
        assert_eq!(callback.picked_file_ids, vec!["one", "two"]);
        assert!(parse_callback("/?state=other&code=x&picked_file_ids=one", "state").is_err());
        assert!(
            parse_callback(
                "/?state=state&state=other&code=x&picked_file_ids=one",
                "state"
            )
            .is_err()
        );
    }

    #[test]
    fn refresh_token_stays_behind_the_native_credential_boundary() {
        let store = Arc::new(MemoryStore::default());
        let provider = GoogleTokenProvider::new_with_store(
            config(),
            Some(TokenResponse {
                access_token: "short-lived-access".into(),
                refresh_token: Some("long-lived-refresh".into()),
                expires_in: 3600,
            }),
            store.clone(),
        )
        .unwrap();
        assert_eq!(
            provider.access_token().unwrap().as_str(),
            "short-lived-access"
        );
        assert_eq!(store.get().unwrap().unwrap().as_str(), "long-lived-refresh");
        store.delete().unwrap();
        assert!(store.get().unwrap().is_none());
    }

    #[test]
    fn drive_range_contract_rejects_full_or_mismatched_responses() {
        assert!(
            validate_drive_range_response(
                reqwest::StatusCode::PARTIAL_CONTENT,
                Some("bytes 10-19/100"),
                100,
                10,
                19,
            )
            .is_ok()
        );
        assert!(
            validate_drive_range_response(
                reqwest::StatusCode::OK,
                Some("bytes 10-19/100"),
                100,
                10,
                19,
            )
            .is_err()
        );
        assert!(
            validate_drive_range_response(
                reqwest::StatusCode::PARTIAL_CONTENT,
                Some("bytes 10-20/100"),
                100,
                10,
                19,
            )
            .is_err()
        );
    }

    #[test]
    fn discovery_budget_is_enforced_before_children_enter_the_queue() {
        let mut discovered = (0..MAX_DRIVE_FILES - 1)
            .map(|index| format!("seen-{index}"))
            .collect::<std::collections::HashSet<_>>();
        let mut queue = std::collections::VecDeque::new();
        let result = enqueue_discovered(
            &mut queue,
            &mut discovered,
            vec![fixture_file("new-one"), fixture_file("new-two")],
            1,
        );
        assert!(result.is_err());
        assert_eq!(discovered.len(), MAX_DRIVE_FILES);
        assert_eq!(queue.len(), 1);
        assert!(valid_page_token("bounded-token"));
        assert!(!valid_page_token(&"x".repeat(4 * 1024 + 1)));
    }

    #[test]
    fn controlled_oauth_exchange_and_revocation_use_native_token_boundaries() {
        let token_body =
            br#"{"access_token":"access","refresh_token":"refresh","expires_in":3600}"#;
        let responses = vec![
            http_response(
                "200 OK",
                &[("Content-Type", "application/json".into())],
                token_body,
            ),
            http_response("200 OK", &[], b""),
        ];
        let (base, requests, server) = fixture_server(responses);
        let config = config_with_base(&base);
        let client = Client::builder().build().unwrap();
        let token = exchange_authorization_code(
            &config,
            &client,
            "authorization-code",
            "pkce-verifier",
            "http://127.0.0.1/callback",
        )
        .unwrap();
        let store = Arc::new(MemoryStore::default());
        let provider =
            GoogleTokenProvider::new_with_store(config, Some(token), store.clone()).unwrap();
        provider.disconnect().unwrap();
        server.join().unwrap();
        let requests = requests.lock().unwrap();
        assert!(requests[0].starts_with("POST /token "));
        assert!(requests[0].contains("code_verifier=pkce-verifier"));
        assert!(requests[0].contains("grant_type=authorization_code"));
        assert!(requests[1].starts_with("POST /revoke "));
        assert!(requests[1].contains("token=refresh"));
        assert!(store.get().unwrap().is_none());
    }

    #[test]
    fn controlled_folder_fixture_paginates_and_filters_compatible_packages() {
        let folder = br#"{"id":"folder","name":"Songs","mimeType":"application/vnd.google-apps.folder","capabilities":{"canDownload":false}}"#;
        let first = br#"{"files":[{"id":"one","name":"One.lrail","mimeType":"application/octet-stream","size":"12","version":"1","capabilities":{"canDownload":true}}],"nextPageToken":"next"}"#;
        let second = br#"{"files":[{"id":"skip","name":"raw.mp3","mimeType":"audio/mpeg","size":"12","version":"1","capabilities":{"canDownload":true}},{"id":"two","name":"Two.LRAIL","mimeType":"application/octet-stream","size":"13","version":"2","capabilities":{"canDownload":true}}]}"#;
        let responses = [folder.as_slice(), first.as_slice(), second.as_slice()]
            .into_iter()
            .map(|body| {
                http_response(
                    "200 OK",
                    &[("Content-Type", "application/json".into())],
                    body,
                )
            })
            .collect();
        let (base, requests, server) = fixture_server(responses);
        let store = Arc::new(MemoryStore::default());
        let provider = Arc::new(
            GoogleTokenProvider::new_with_store(
                config_with_base(&base),
                Some(TokenResponse {
                    access_token: "access".into(),
                    refresh_token: Some("refresh".into()),
                    expires_in: 3600,
                }),
                store,
            )
            .unwrap(),
        );
        let files = expand_drive_root(&provider, "folder").unwrap();
        server.join().unwrap();
        assert_eq!(
            files
                .iter()
                .map(|file| file.id.as_str())
                .collect::<Vec<_>>(),
            ["one", "two"]
        );
        let requests = requests.lock().unwrap();
        assert!(requests[1].contains("pageSize=1000"));
        assert!(requests[2].contains("pageToken=next"));
    }

    #[test]
    fn controlled_range_fixture_refreshes_401_and_bounds_track_reads() {
        let unauthorized = http_response("401 Unauthorized", &[], b"");
        let refreshed = http_response(
            "200 OK",
            &[("Content-Type", "application/json".into())],
            br#"{"access_token":"fresh","expires_in":3600}"#,
        );
        let karaoke = http_response(
            "206 Partial Content",
            &[("Content-Range", "bytes 10-19/100".into())],
            b"karaoke!!!",
        );
        let original = http_response(
            "206 Partial Content",
            &[("Content-Range", "bytes 40-49/100".into())],
            b"original!!",
        );
        let (base, requests, server) =
            fixture_server(vec![unauthorized, refreshed, karaoke, original]);
        let provider = Arc::new(
            GoogleTokenProvider::new_with_store(
                config_with_base(&base),
                Some(TokenResponse {
                    access_token: "stale".into(),
                    refresh_token: Some("refresh".into()),
                    expires_in: 3600,
                }),
                Arc::new(MemoryStore::default()),
            )
            .unwrap(),
        );
        let transport = GoogleDriveTransport::new(provider).unwrap();
        let object = RemoteObject {
            cache_key: "google-drive:file".into(),
            length: 100,
            version: "1".into(),
        };
        assert_eq!(
            transport.fetch_range(&object, 10, 19).unwrap(),
            b"karaoke!!!"
        );
        assert_eq!(
            transport.fetch_range(&object, 40, 49).unwrap(),
            b"original!!"
        );
        server.join().unwrap();
        let requests = requests.lock().unwrap();
        let requests = requests
            .iter()
            .map(|request| request.to_ascii_lowercase())
            .collect::<Vec<_>>();
        assert!(requests[0].contains("authorization: bearer stale"));
        assert!(requests[1].starts_with("post /token "));
        assert!(requests[2].contains("authorization: bearer fresh"));
        assert!(requests[2].contains("range: bytes=10-19"));
        assert!(requests[3].contains("range: bytes=40-49"));
    }

    #[test]
    fn controlled_http_package_opens_and_switches_tracks_before_full_transfer() {
        let directory = tempfile::tempdir().unwrap();
        let video = directory.path().join("video.mp4");
        let karaoke = directory.path().join("karaoke.m4a");
        let original = directory.path().join("original.m4a");
        fs::write(&video, vec![0x11; (CACHE_BLOCK_BYTES * 2 + 113) as usize]).unwrap();
        fs::write(&karaoke, vec![0x22; (CACHE_BLOCK_BYTES + 127) as usize]).unwrap();
        fs::write(&original, vec![0x33; (CACHE_BLOCK_BYTES + 139) as usize]).unwrap();
        let package = directory.path().join("fixture.lrail");
        let vault = [0x31_u8; 32];
        pack_for_vault(
            &PackageRequest {
                metadata: json!({"title": "Range fixture"}),
                assets: vec![
                    AssetRequest {
                        logical_name: "media/video.mp4".into(),
                        path: video,
                        media_type: "video/mp4".into(),
                        kind: "playback-video".into(),
                        track_name: None,
                        language: None,
                        default: true,
                        content_encoding: ContentEncoding::Identity,
                    },
                    AssetRequest {
                        logical_name: "audio/karaoke.m4a".into(),
                        path: karaoke,
                        media_type: "audio/mp4".into(),
                        kind: "playback-audio".into(),
                        track_name: Some("karaoke".into()),
                        language: Some("vi".into()),
                        default: true,
                        content_encoding: ContentEncoding::Identity,
                    },
                    AssetRequest {
                        logical_name: "audio/original-reference.m4a".into(),
                        path: original,
                        media_type: "audio/mp4".into(),
                        kind: "playback-audio".into(),
                        track_name: Some("original-reference".into()),
                        language: Some("vi".into()),
                        default: false,
                        content_encoding: ContentEncoding::Identity,
                    },
                ],
                producer: "controlled-fixture".into(),
                minimum_player_version: "0.8.0".into(),
            },
            &package,
            &vault,
            None,
        )
        .unwrap();
        let package_bytes = Arc::new(fs::read(&package).unwrap());
        let (base, ranges, stop, server) = dynamic_range_server(package_bytes.clone());
        let provider = Arc::new(
            GoogleTokenProvider::new_with_store(
                config_with_base(&base),
                Some(TokenResponse {
                    access_token: "access".into(),
                    refresh_token: None,
                    expires_in: 3600,
                }),
                Arc::new(MemoryStore::default()),
            )
            .unwrap(),
        );
        let cache = Arc::new(
            RangeCache::new(
                directory.path().join("cache"),
                Arc::new(GoogleDriveTransport::new(provider).unwrap()),
                Arc::new(PriorityScheduler::default()),
            )
            .unwrap(),
        );
        let object = RemoteObject {
            cache_key: "google-drive:file".into(),
            length: package_bytes.len() as u64,
            version: "1".into(),
        };
        let source = CachedRandomAccessSource::new(
            "gdrive://file".into(),
            object.clone(),
            cache.clone(),
            IoPriority::Playback,
        );
        let mut reader = PackageReader::open_source_with_vault(Box::new(source), &vault).unwrap();
        assert_eq!(
            reader
                .read_asset_range("audio/karaoke.m4a", 0, 32)
                .unwrap()
                .as_slice(),
            &[0x22; 32]
        );
        assert_eq!(
            reader
                .read_asset_range("audio/original-reference.m4a", 0, 32)
                .unwrap()
                .as_slice(),
            &[0x33; 32]
        );
        assert!(!cache.is_complete(&object));
        let progressive_requests = ranges.lock().unwrap().len();
        assert!(progressive_requests < object.length.div_ceil(CACHE_BLOCK_BYTES) as usize);

        let changed_version = RemoteObject {
            version: "2".into(),
            ..object.clone()
        };
        let mut header = [0_u8; 64];
        cache
            .read_exact(&changed_version, 0, &mut header, IoPriority::Playback)
            .unwrap();
        assert!(ranges.lock().unwrap().len() > progressive_requests);

        let mut complete = vec![0_u8; object.length as usize];
        cache
            .read_exact(&object, 0, &mut complete, IoPriority::Background)
            .unwrap();
        assert_eq!(complete, *package_bytes);
        assert!(cache.is_complete(&object));
        let requests_after_fill = ranges.lock().unwrap().len();
        stop.store(true, Ordering::Relaxed);
        server.join().unwrap();
        let mut offline = [0_u8; 128];
        cache
            .read_exact(&object, 0, &mut offline, IoPriority::Playback)
            .unwrap();
        assert_eq!(offline.as_slice(), &package_bytes[..128]);
        assert_eq!(ranges.lock().unwrap().len(), requests_after_fill);
    }

    #[test]
    fn hostile_range_body_is_rejected_before_unbounded_buffering() {
        let body = vec![0x41; 110];
        let response = http_response(
            "206 Partial Content",
            &[("Content-Range", "bytes 0-9/100".into())],
            &body,
        );
        let (base, _requests, server) = fixture_server(vec![response]);
        let provider = Arc::new(
            GoogleTokenProvider::new_with_store(
                config_with_base(&base),
                Some(TokenResponse {
                    access_token: "access".into(),
                    refresh_token: None,
                    expires_in: 3600,
                }),
                Arc::new(MemoryStore::default()),
            )
            .unwrap(),
        );
        let transport = GoogleDriveTransport::new(provider).unwrap();
        let object = RemoteObject {
            cache_key: "google-drive:file".into(),
            length: 100,
            version: "1".into(),
        };
        assert!(transport.fetch_range(&object, 0, 9).is_err());
        server.join().unwrap();
    }
}
