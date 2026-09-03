# Windows release artifacts

Generated installers and executables are intentionally not committed. A Windows build
now produces one LyricRail Player bundle; the removed Studio shell and volume-broker
service are no longer release products.

The separately delivered signed runtime contains Python, FFmpeg/ffprobe, the native
`lrail` tool and pinned models used by local processing. Release Player builds reject an
incomplete or incorrectly signed runtime. Authenticode signing and clean-host install,
association, upgrade and uninstall tests remain required release gates.
