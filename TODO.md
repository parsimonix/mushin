# FFmpeg integration

Implement video conversion as a separate milestone. Start with container
remuxing, then add transcoding with one explicit encoding profile.

Both nodes accept an `ArtifactRef` and return a new reference. The source
content remains unchanged. A remux copies encoded streams into another
container; a transcode re-encodes selected streams.

## Implementation order

Complete and verify each step before moving to the next:

1. [x] Add isolated input-path leases and staged output publication to the local artifact store; test isolation, publication, and cleanup.
2. [x] Add configurable FFmpeg/ffprobe discovery, version reporting, and capability checks.
3. [x] Build the asynchronous process runner with bounded diagnostics, timeout handling, and cancellation that terminates and reaps the child before releasing artifact leases.
4. [x] Add typed ffprobe metadata parsing and define explicit stream-selection policies.
5. [x] Implement typed `RemuxVideo` configuration, explicit stream copying, output verification, and artifact publication.
6. [x] Add generated video fixtures and integration coverage for success, incompatible containers, malformed input, missing tools, timeout, and cancellation.
7. [x] Add the import → remux → save playground and installation/testing documentation.
8. [x] Add execution progress events and FFmpeg progress parsing, including unknown durations and throttling.
9. [ ] Implement and test one explicit transcoding profile using the same services.

## 1. Managed artifact file access

- [x] Add managed input-path access for external tools without loading video bytes into Python memory.
- [x] Add temporary output-file creation within the artifact workspace.
- [x] Publish completed output as a new artifact only after successful processing.
- [x] Define extensionless output paths requiring callers to pass an explicit format to external tools.
- [x] Remove partial output when the processing block raises on failure, timeout, or cancellation.
- [x] Define workspace ownership and cleanup while an external process is using files.
- [x] Verify source isolation, successful publication, and failed-output cleanup.

## 2. FFmpeg and ffprobe service

- [x] Locate configurable FFmpeg and ffprobe executables and report missing binaries clearly.
- [x] Record executable versions and check required container/codec capabilities.
- [x] Run tools with `asyncio.create_subprocess_exec()` and argument lists.
- [x] Disable interactive stdin for FFmpeg.
- [x] Drain subprocess output concurrently and retain a bounded stderr tail for diagnostics.
- [x] Return structured errors for launch failures and nonzero exits.
- [x] Return structured errors for invalid probe output.
- [x] Use ffprobe to obtain actual duration, container, and stream metadata.
- [x] Define stream selection for video, multiple audio tracks, subtitles, attachments, and chapters.
- [x] Handle task cancellation and the execution context's cooperative cancellation signal.
- [x] On cancellation or timeout, terminate, allow a bounded grace period, then kill if necessary and reap the process.
- [x] Finish process cleanup before removing temporary files.

## 3. RemuxVideo node

- [x] Define typed configuration for the destination container and stream-selection policy.
- [x] Accept an artifact input and return a new artifact output.
- [x] Build explicit stream mappings and use stream copying (`-c copy`).
- [x] Reject incompatible streams/container combinations with useful diagnostics.
- [x] Avoid silently dropping streams or falling back to transcoding.
- [x] Assign a logical filename matching the new container.
- [x] Verify expected output streams before publishing the artifact.
- [x] Accept `mkv` as an alias for the Matroska output container.
- [x] Return the original artifact when container identity is confirmed and the stream/chapter selection is unchanged.

## 4. Integration tests and playground

- [x] Generate tiny valid video fixtures with known streams; keep the current dummy-file example for filename inference only.
- [x] Test successful remuxing and inspect output with ffprobe.
- [x] Test missing binaries, unsupported formats, malformed input, and process failures.
- [x] Test cancellation and timeout cleanup, including child-process termination.
- [x] Verify the source remains unchanged and unsuccessful runs publish no output artifact.
- [x] Cover multiple audio tracks, subtitles, opaque attachments, cover art, chapters, and idempotency for MKV/MP4/WebM.
- [x] Add a runnable valid-video playground demonstrating import → remux → save.
- [x] Document FFmpeg installation requirements and how to run the integration checks.

## 5. Progress reporting

- [x] Extend execution events with node progress notifications.
- [x] Parse FFmpeg's machine-readable `-progress pipe:1` output.
- [x] Use ffprobe duration to calculate percentage when duration is known.
- [x] Support unknown duration without inventing a percentage.
- [x] Throttle updates and preserve event ordering through completion and cancellation.
- [x] Display conversion progress in the playground.

## 6. TranscodeVideo node

- [ ] Choose one initial encoding profile with explicit container, video codec, audio codec, and quality settings.
- [ ] Define typed profile configuration and validate supported settings.
- [ ] Specify handling of subtitles, extra audio tracks, attachments, and chapters.
- [ ] Reuse artifact staging, process execution, cancellation, and progress services.
- [ ] Verify output codecs, streams, and duration using valid fixtures.
- [ ] Document quality/speed tradeoffs and supported profile limitations.

## References

- [FFmpeg command-line documentation](https://ffmpeg.org/ffmpeg.html)
- [ffprobe documentation](https://ffmpeg.org/ffprobe.html)
- [Python asyncio subprocess documentation](https://docs.python.org/3/library/asyncio-subprocess.html)
