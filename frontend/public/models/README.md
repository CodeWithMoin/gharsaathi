# Smart Turn Hinglish model

`smart_turn_hinglish_4s-39736bc2.onnx` is the 4-second window export
(`runs/smart_turn_4s.onnx`) of
[`CodeWithMoin/smart-turn-hinglish`](https://github.com/CodeWithMoin/smart-turn-hinglish)
at commit `64eafecdc0dd45ba7f4319ee2571e3bb493db30f`, supplied by the repository owner
for this application.

- Input: `waveform`, float32 `[1, 64000]`, 16 kHz mono, right-aligned (peak-normalised by the client, matching `smart_turn/infer.py`).
- Output: `p_complete`, probability that the speaker finished their turn.
- SHA-256: `39736bc2c4a1ff303d3bd6c54ac648d175f921624e232680c3e097e0e46bb966`.

The 31 MB binary is not committed (the repository has no git LFS). Fetch and verify it with:

```bash
cd frontend
npm run fetch:smart-turn-model                       # downloads the pinned commit
SMART_TURN_MODEL_SOURCE=/path/to/model.onnx npm run fetch:smart-turn-model   # or copy a local file
```

The browser re-checks the SHA-256 before creating the ONNX session and falls back to
the tuned GPT-Live session if the file is missing or does not match. The hash suffix
in the filename makes the URL safe for immutable caching.
