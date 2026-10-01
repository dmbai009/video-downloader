# Video Downloader

A small local web UI for downloading videos from **YouTube** and **Niconico** with
control over quality, codec and container. It runs on your own machine and is
reachable there by default, or from a phone, tablet or another laptop when
local-network access is enabled.

Under the hood it is a thin wrapper around [yt-dlp](https://github.com/yt-dlp/yt-dlp)
and ffmpeg. The server is plain Python standard library: no Flask, no npm, no build step.

## Why a server and not just an HTML file

A static page cannot do this. Browsers block cross-origin requests to YouTube and
Niconico, and both sites deliver video as encrypted or segmented streams that have
to be reassembled with ffmpeg. So the page talks to a small local server that drives
yt-dlp, and the HTML stays a plain single file.

## Requirements

- Python 3.8 or newer
- `yt-dlp` and `ffmpeg` on PATH

| OS | Install |
|---|---|
| Windows | `scoop install yt-dlp ffmpeg` |
| macOS | `brew install yt-dlp ffmpeg` |
| Linux | `pipx install yt-dlp` and your package manager's `ffmpeg` |

If either tool lives somewhere unusual, point `config.json` at it directly.

## Running

```
git clone https://github.com/dmbai009/video-downloader.git
cd video-downloader
```

- **Windows** — double-click `start.bat`
- **macOS / Linux** — `chmod +x start.sh && ./start.sh`
- **Any platform** — `python server.py`

On first run the app writes a default `config.json` for you. The console prints
where to open it:

```
This machine  : http://localhost:8777
Download dir  : /home/you/Downloads   (the browser's folder)
```

The secure default is `host: 127.0.0.1`, which only accepts connections from this
computer. To use the page from another device on a trusted home network, change it
to `0.0.0.0` and restart; the console will then print the LAN address as well.

Stop with `Ctrl+C`.

## Using it

1. Paste a link and press **Check**. The app reads the title, duration and the
   resolutions and codecs the video *actually* has — the quality list is built from
   that video, not from a fixed menu.
2. Pick a preset, or set quality, video codec, audio codec and container by hand.
   The **Pick an exact format** section exposes every individual format ID if you
   want one specific stream.
3. Press **Download**. Progress, speed and ETA appear below.

That is the whole download. The file is written straight to the download folder on the
machine running the server — nothing else is needed.

## Download folder

By default files go to the same folder your browser saves downloads to: the folder set
in Chrome, Edge or Brave (for the profile used last), or the system **Downloads** folder
when the browser keeps its default.

To change it, use the **Download folder** section at the bottom of the page: type a full
path and press **Save**, or press **Browse…** to pick a folder in a dialog. **Use the
browser's folder** goes back to the default. The choice is saved to `config.json`.

The folder can only be changed on the computer running the server. Other devices see
where files go but get no controls, and the server refuses such requests from them:
otherwise anyone on the network could make it write files anywhere on the disk.
Finished downloads stay playable on the page after the folder is changed.

When a job finishes it turns into a player right there on the page, with the file's
real codecs and size next to it, so you can confirm what you got without opening the
folder. **Save to this device** below the player is only for pulling the file onto a
phone or another computer; on the machine that did the downloading it would just
write a second copy.

## Presets

| Preset | What it picks | Use for |
|---|---|---|
| **For editing** | H.264 + AAC in MP4 | Premiere, Resolve, Vegas, Final Cut |
| **Best quality** | any codec, MKV | watching, archiving |
| **Audio only** | m4a / mp3 / opus / wav / flac | music, an audio track for editing |

### Why "For editing" exists

YouTube serves the highest-bitrate audio it has, which is almost always **Opus**.
A default `bv*+ba` selector picks it, and `--merge-output-format mp4` then stores
Opus inside an MP4 without re-encoding. The result is a valid file that every media
player handles — but most video editors cannot decode Opus in MP4, so the clip
imports as picture with no sound at all.

The preset forces the AAC track instead. No re-encoding happens either way, so
quality is unchanged; only the track choice differs.

The UI flags risky combinations before you download, and every finished job shows
its file's real codecs via ffprobe. An amber `editors will hear nothing` badge marks
exactly this situation.

## Configuration — `config.json`

Created automatically on first run; copy `config.example.json` if you want to start over.
Restart the server after editing.

| Key | Meaning |
|---|---|
| `download_dir` | where files land. Empty (default) = the browser's download folder. Relative paths resolve against the project folder, so `"downloads"` stays portable. Usually set from the page instead |
| `cookies_file` | Netscape-format cookie file. Niconico needs one for most videos |
| `cookies_from_browser` | alternative: `chrome`, `firefox`, `edge` — read cookies straight from a browser profile |
| `port` | default `8777` |
| `host` | `127.0.0.1` by default; use `0.0.0.0` for other devices on a trusted local network |
| `ytdlp`, `ffmpeg` | explicit executable paths when auto-detection fails |

### Cookies

Niconico refuses most videos to logged-out clients. Export a cookie file with a
browser extension that produces Netscape format, save it outside the repository,
and set `cookies_file` to its path. Cookie files hold live session tokens — treat
them like passwords. `.gitignore` already excludes `*cookies*.txt` and `config.json`
so neither the file nor its location gets committed.

Cookies expire. When downloads start failing with a login error, export a fresh file.

## Security

- The server has no user accounts. Keep the default `127.0.0.1` on public or
  untrusted networks. With `host: 0.0.0.0`, every device on the LAN can deliberately
  open and use the downloader.
- Browser writes must be same-origin JSON and request bodies are size-limited, which
  prevents ordinary cross-site request attacks. Finished media is served only through
  its completed-job ID, never by an arbitrary filename from Downloads.
- Only YouTube and Niconico links are accepted. They are passed to yt-dlp after `--`,
  so a value such as `--exec=...` can never become an option. Download choices are
  restricted to values offered by the page, active downloads and probes are bounded,
  and responses carry restrictive browser security headers.
- This is still a local tool, not an internet-facing service. Do not port-forward it
  or expose it through a public reverse proxy.

## Notes

- **Tests:** `python -m unittest discover -s tests -v` (no network or yt-dlp runs).
- **Playlists are disabled** (`--no-playlist`): a link to a video inside a playlist
  downloads that one video, not the whole list.
- **MKV does not preview.** Browsers cannot play the Matroska container, so the
  "Best quality" preset shows a note instead of a player. The file itself is fine —
  this only affects the preview.
- **Keep yt-dlp current.** YouTube changes its player regularly and older versions
  break: `scoop update yt-dlp` / `brew upgrade yt-dlp` / `pipx upgrade yt-dlp`.
- yt-dlp may warn about a missing JavaScript runtime. Installing Deno or Node 20+
  silences it and keeps every format reachable on newer YouTube videos.

## License

MIT
