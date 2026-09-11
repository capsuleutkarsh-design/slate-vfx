# Worker Threads Architecture

UT_VFX is built heavily around PyQt6's `QThread` and `QRunnable` architectures. Because database queries, file transfers, and media rendering block the Global Interpreter Lock (GIL) or the Qt Event Loop, all heavy lifting happens off the main GUI thread.

---

## 1. Background Polling (`PollWorker`)

UT_VFX uses a stateless SQLite backend. Because SQLite doesn't have "Push" notifications like PostgreSQL's `LISTEN/NOTIFY`, the Dashboard tab runs a `PollWorker`.

### Interruptible Sleep
If the user closes the application while a thread is sleeping (`time.sleep(3)`), the app will hang for 3 seconds before tearing down. `PollWorker` solves this via `_sleep_interruptibly()`:
```python
def _sleep_interruptibly(self, total_ms: int, step_ms: int = 100) -> bool:
    elapsed = 0
    while elapsed < total_ms:
        if not self.running or self.isInterruptionRequested():
            return False
        self.msleep(slice_ms)
        elapsed += slice_ms
```
This splits the 3-second sleep into 100ms slices. If the user hits "Close", the thread breaks out instantly.

### Lightweight Timestamping
Instead of querying the full database every 3 seconds, `PollWorker` executes:
`SELECT MAX(last_updated) FROM tracking_shots WHERE project_code=%s`
If the returned timestamp is greater than the local `last_known_timestamp`, it fires the `updates_available` signal.

---

## 2. Review Tab Rendering (`ShotProxyRenderWorker`)

The Shot Review tab cannot play 500MB 4K EXR sequences smoothly. When a shot is sent to review, the `ShotProxyRenderWorker` is spawned.

### FFmpeg Auto-Detection
The worker infers the starting frame of the sequence (e.g., `shot_v01.1001.exr` -> `1001`) via `_infer_start_number()`. It passes this to FFmpeg:
```bash
ffmpeg -start_number 1001 -i sequence.%04d.exr -vf scale=-2:720:flags=lanczos -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -movflags +faststart -an -y output.mp4
```
It renders both the "Scan" media and "Render" media simultaneously in the background, emitting percentage progress updates to the UI.

---

## 3. Cache Hydration (`FrameCacheWorker`)

While the Proxy renderer handles MP4 generation, the Review Player also supports flipbook-style RAM playback. `FrameCacheWorker` handles hydrating the `LRUCache`.

### Signal Throttling
A critical detail in `FrameCacheWorker`:
```python
# Throttle progress signals to every 5 frames to avoid event loop saturation
if cached_count % 5 == 0 or cached_count == total_frames:
    self.progress.emit(cached_count, total_frames)
```
If a sequence has 500 frames, emitting 500 progress signals in 2 seconds overloads the PyQt Event Queue and causes UI lag. Emitting every 5 frames drops the queue load by 80% while still looking completely smooth to the human eye.
