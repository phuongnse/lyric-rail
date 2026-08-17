# Models

Runtime engines download model weights into this directory. Model files are machine-local, may be several gigabytes, and are intentionally excluded from Git.

LyricRail does not install or run a text-recognition model. The Vietnamese singing CTC model is retained only for forced alignment of exact user-supplied lyrics; audio-separation and speaker-embedding models support instrumental cleanup and vocal-role classification.
