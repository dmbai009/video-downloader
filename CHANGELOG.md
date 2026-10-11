# Changelog

## 1.1.0 - 2026-10-11

- Download a video or audio time range with optional start/end and millisecond input.
- Keep whole-video downloads as the default when both fields are blank.
- Apply presets, codec/container choices and audio conversion to clips, with clean cuts.
- Validate ranges against source duration and reject live-stream clipping.
- Give clips distinct filenames and show the selected interval in download progress.
- Add offline media integration tests and UI checks for time-range controls.

## 1.0.0 - 2026-10-01

First public release.

- Download and inspect YouTube and Niconico media from a local web interface.
- Select presets, codecs, containers and exact yt-dlp formats.
- Track, cancel, preview and save completed downloads from another device.
- Detect the browser download folder and support explicit folder selection.
- Restrict accepted sites and options, same-origin JSON writes and request sizes.
- Serve only files belonging to completed jobs, with strict byte-range handling.
- Bound concurrent downloads, metadata probes, retained jobs and the metadata cache.
