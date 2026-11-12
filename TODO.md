# FFmpeg integration

Implement video conversion as a separate milestone. Start with container
remuxing, then add transcoding with one explicit encoding profile.

Both nodes accept an `ArtifactRef` and return a new reference. The source
content remains unchanged. A remux copies encoded streams into another
container; a transcode re-encodes selected streams.

## Implementation order

Complete and verify each step before moving to the next:

1. [x] Add isolated input-path leases and staged output publication to the local artifact store; test isolation, publication, and cleanup.
2. [ ] Add configurable FFmpeg/ffprobe discovery, version reporting, and capability checks.
3. [ ] Build the asynchronous process runner with bounded diagnostics, timeout handling, and cancellation that terminates and reaps the child before releasing artifact leases.
4. [ ] Add typed ffprobe metadata parsing and define explicit stream-selection policies.
5. [ ] Implement typed `RemuxVideo` configuration, explicit stream copying, output verification, and artifact publication.
6. [ ] Add generated video fixtures and integration coverage for success, incompatible containers, malformed input, missing tools, timeout, and cancellation.
7. [ ] Add the import → remux → save playground and installation/testing documentation.
8. [ ] Add execution progress events and FFmpeg progress parsing, including unknown durations and throttling.
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

- [ ] Locate configurable FFmpeg and ffprobe executables and report missing binaries clearly.
- [ ] Record executable versions and check required container/codec capabilities.
- [ ] Run tools with `asyncio.create_subprocess_exec()` and argument lists.
- [ ] Disable interactive stdin for FFmpeg.
- [ ] Drain subprocess output concurrently and retain a bounded stderr tail for diagnostics.
- [ ] Return structured errors for launch failures, nonzero exits, and invalid probe output.
- [ ] Use ffprobe to obtain actual duration, container, and stream metadata.
- [ ] Define stream selection for video, multiple audio tracks, subtitles, attachments, and chapters.
- [ ] Handle task cancellation and the execution context's cooperative cancellation signal.
- [ ] On cancellation or timeout, terminate, allow a bounded grace period, then kill if necessary and reap the process.
- [ ] Finish process cleanup before removing temporary files.

## 3. RemuxVideo node

- [ ] Define typed configuration for the destination container and stream-selection policy.
- [ ] Accept an artifact input and return a new artifact output.
- [ ] Build explicit stream mappings and use stream copying (`-c copy`).
- [ ] Reject incompatible streams/container combinations with useful diagnostics.
- [ ] Avoid silently dropping streams or falling back to transcoding.
- [ ] Assign a logical filename matching the new container.
- [ ] Verify expected output streams before publishing the artifact.

## 4. Integration tests and playground

- [ ] Generate tiny valid video fixtures with known streams; keep the current dummy-file example for filename inference only.
- [ ] Test successful remuxing and inspect output with ffprobe.
- [ ] Test missing binaries, unsupported formats, malformed input, and process failures.
- [ ] Test cancellation and timeout cleanup, including child-process termination.
- [ ] Verify the source remains unchanged and unsuccessful runs publish no output artifact.
- [ ] Add a runnable valid-video playground demonstrating import → remux → save.
- [ ] Document FFmpeg installation requirements and how to run the integration checks.

## 5. Progress reporting

- [ ] Extend execution events with node progress notifications.
- [ ] Parse FFmpeg's machine-readable `-progress pipe:1` output.
- [ ] Use ffprobe duration to calculate percentage when duration is known.
- [ ] Support unknown duration without inventing a percentage.
- [ ] Throttle updates and preserve event ordering through completion and cancellation.
- [ ] Display conversion progress in the playground.

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
