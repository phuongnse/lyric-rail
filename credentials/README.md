# Local secrets

Everything in this directory except this README is excluded from Git and must
never enter an installer, runtime pack, support archive, or CI artifact.

## Runtime-pack signing

`runtime-signing-private.key` is the current development/RC Ed25519 seed. Its
public half is `config/runtime-signing-public.key` and is embedded in Studio.
Possession of the private seed permits signing code that Studio will trust, so:

1. Do not copy it into a runtime pack or source-control system.
2. Keep the workspace on an OS-encrypted volume with a user-only ACL.
3. Before a stable release, replace this development key with an offline or
   hardware-backed release-signing workflow, back it up securely, and rehearse
   revocation/recovery.
4. If exposure is suspected, rotate the key, rebuild Studio with the new public
   key, and do not trust packs signed by the old key.

The manifest builder refuses a private key located inside the runtime root, and
runtime inventory rejects `credentials/`, root `.env*`, mutable data
directories, and `runtime-signing-private.key`.

## YouTube OAuth

Không đặt mật khẩu Google trong dự án. Uploader sử dụng OAuth 2.0.

Thiết lập một lần:

1. Tạo Google Cloud Project.
2. Bật YouTube Data API v3.
3. Tạo OAuth Client loại Desktop app.
4. Tải JSON và lưu thành `credentials/client_secret.json`.
5. Điền ID chính xác của kênh vào `config/channel.json` → `expectedChannelId`.
6. Chạy với `--upload`, đăng nhập đúng kênh và cấp quyền trong trình duyệt hệ thống.
7. Refresh token được lưu thành `credentials/token.json`.

Hai file JSON này đã bị loại khỏi Git bằng `.gitignore`.

Lưu ý: dự án YouTube API chưa qua audit sẽ bị giới hạn video upload bằng API ở chế độ private. Chỉ bật tự public sau khi dự án đã đáp ứng yêu cầu kiểm duyệt của Google.
