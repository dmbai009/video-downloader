"""Optional offline integration tests using local yt-dlp, ffmpeg and ffprobe."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_server import server, TMP, ServerTest


@unittest.skipUnless(server.YTDLP and server.FFMPEG and server.FFPROBE,
                     "yt-dlp, ffmpeg and ffprobe are needed for local media tests")
class ClipMedia(ServerTest):
    def test_presets_produce_playable_clips_with_the_requested_duration(self):
        with tempfile.TemporaryDirectory(dir=TMP) as folder:
            root = Path(folder)
            source = root / "source.webm"
            subprocess.run([server.FFMPEG, "-hide_banner", "-loglevel", "error",
                            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25",
                            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                            "-t", "5", "-c:v", "libvpx-vp9", "-g", "125",
                            "-c:a", "libopus", str(source)], check=True, timeout=30,
                           creationflags=server.NO_WINDOW)
            infofile = root / "info.json"
            with server.JOBS_LOCK:
                server.JOBS["f1"] = {"status": "done", "file": source.name, "dir": str(root)}
            infofile.write_text(json.dumps({
                "id": "local", "title": "Local clip", "duration": 5,
                "extractor": "generic", "extractor_key": "Generic",
                "webpage_url": "https://youtu.be/local",
                "formats": [{"format_id": "local", "url": f"http://127.0.0.1:{self.client.port}/media/f1",
                             "ext": "webm", "vcodec": "vp9", "acodec": "opus",
                             "height": 90, "width": 160, "fps": 25}],
            }), encoding="utf-8")
            presets = (
                {"vcodec": "h264", "acodec": "aac", "container": "mp4"},
                {"vcodec": "any", "acodec": "any", "container": "mkv"},
                {"vcodec": "vp9", "acodec": "opus", "container": "webm"},
                {"mode": "audio", "audio_format": "wav"},
                {"mode": "audio", "audio_format": "m4a"},
                {"mode": "audio", "audio_format": "mp3"},
                {"mode": "audio", "audio_format": "opus"},
                {"mode": "audio", "audio_format": "flac"},
                {"vcodec": "h264", "acodec": "aac", "container": "mp4", "format_id": "video+audio"},
                {"vcodec": "h264", "acodec": "aac", "container": "mp4", "start_time": "", "end_time": "2.5"},
                {"vcodec": "h264", "acodec": "aac", "container": "mp4", "start_time": "2.5", "end_time": ""},
            )
            audio_source = root / "source.opus"
            subprocess.run([server.FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(source),
                            "-vn", "-c:a", "copy", str(audio_source)], check=True, timeout=10,
                           creationflags=server.NO_WINDOW)
            with server.JOBS_LOCK:
                server.JOBS["f2"] = {"status": "done", "file": audio_source.name, "dir": str(root)}
            for index, preset in enumerate(presets):
                with self.subTest(preset=preset):
                    outdir = root / str(index)
                    outdir.mkdir()
                    donefile = outdir / "done.txt"
                    if preset.get("format_id"):
                        info = json.loads(infofile.read_text(encoding="utf-8"))
                        video = {**info["formats"][0], "format_id": "video", "acodec": "none"}
                        audio = {"format_id": "audio", "url": f"http://127.0.0.1:{self.client.port}/media/f2",
                                 "ext": "opus", "vcodec": "none", "acodec": "opus"}
                        info["formats"] = [video, audio]
                        infofile.write_text(json.dumps(info), encoding="utf-8")
                    cmd = server.download_command("https://youtu.be/local", {
                        "start_time": "1.125", "end_time": "3.625", "metadata": True, **preset,
                    }, outdir, donefile)
                    # Serve the fixture on loopback; no remote extraction occurs.
                    cmd = cmd[:-2] + ["--load-info-json", str(infofile)]
                    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace",
                                            env=server.run_env(), timeout=60, creationflags=server.NO_WINDOW)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    output = Path(donefile.read_text(encoding="utf-8").strip())
                    self.assertTrue(output.is_file())
                    probe = subprocess.run([server.FFPROBE, "-v", "error", "-show_entries",
                                            "format=duration:stream=codec_type,codec_name",
                                            "-of", "json", str(output)], capture_output=True,
                                           encoding="utf-8", check=True, timeout=10,
                                           creationflags=server.NO_WINDOW)
                    media = json.loads(probe.stdout)
                    self.assertAlmostEqual(float(media["format"]["duration"]), 2.5, delta=.1)
                    codecs = {s["codec_type"]: s["codec_name"] for s in media["streams"]}
                    if preset.get("container") == "mp4":
                        self.assertEqual(codecs, {"video": "h264", "audio": "aac"})
                        self.assertEqual(output.suffix, ".mp4")
                    elif index == 1:
                        self.assertEqual(output.suffix, ".mkv")
                    elif index == 2:
                        self.assertEqual(codecs, {"video": "vp9", "audio": "opus"})
                    else:
                        self.assertNotIn("video", codecs)
                        self.assertEqual(output.suffix, "." + preset["audio_format"])
