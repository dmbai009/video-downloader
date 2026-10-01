"""Tests for input checks and the download folder setting.

Run:  python -m unittest discover -s tests -v

The server module is imported with a temporary config.json, so the real settings and
download folder are never touched, and yt-dlp is never actually run.
"""

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

TMP = Path(tempfile.mkdtemp(prefix="video-downloader-test-"))
(TMP / "start").mkdir()
(TMP / "config.json").write_text(json.dumps({"download_dir": str(TMP / "start")}), encoding="utf-8")
os.environ["VIDEO_DOWNLOADER_CONFIG"] = str(TMP / "config.json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402


class Client:
    def __init__(self, port):
        self.port = port

    def request(self, method, path, body=None, headers=None, return_headers=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        payload = (body if isinstance(body, bytes) else json.dumps(body).encode()) if body is not None else None
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        conn.request(method, path, body=payload, headers=request_headers)
        res = conn.getresponse()
        data = res.read()
        response_headers = dict(res.getheaders())
        conn.close()
        try:
            if response_headers.get("Content-Type", "").startswith("application/json"):
                result = (res.status, json.loads(data))
            else:
                raise ValueError
        except ValueError:
            result = (res.status, data)
        return (*result, response_headers) if return_headers else result

    def post(self, path, body):
        return self.request("POST", path, body)

    def get(self, path):
        return self.request("GET", path)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.client = Client(cls.httpd.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        server.set_download_dir(str(TMP / "start"))
        with server.JOBS_LOCK:
            server.JOBS.clear()


class UrlChecks(ServerTest):
    def test_only_http_links_are_accepted(self):
        for url in ("https://www.youtube.com/watch?v=abc", "http://nicovideo.jp/watch/sm9",
                    "HTTPS://M.YOUTUBE.COM/a", "https://nico.ms/sm9"):
            self.assertEqual(server.check_url(url), url)
        for url in ("--exec=calc", "-o C:/x", "--config-locations=//evil/share/c", "file:///C:/Windows",
                    "ftp://x/y", "https://x.com/a", "https://youtube.com@example.com/a",
                    "https://a b", "", "https://" + "a" * 3000):
            with self.assertRaises(ValueError, msg=url):
                server.check_url(url)

    def test_the_url_comes_after_the_end_of_options_marker(self):
        cmd = server.download_command("https://youtu.be/x", {"container": "mp4"}, TMP, TMP / "done.txt")
        self.assertEqual(cmd[-2:], ["--", "https://youtu.be/x"])
        self.assertIn("--ignore-config", cmd)

    def test_probe_refuses_an_option_disguised_as_a_link_without_running_yt_dlp(self):
        with mock.patch.object(server.subprocess, "run", side_effect=AssertionError("yt-dlp was run")):
            status, body = self.client.post("/api/probe", {"url": "--exec=calc.exe"})
        self.assertEqual(status, 400)
        self.assertIn("YouTube", body["error"])

    def test_download_refuses_bad_links_and_options_without_starting_a_job(self):
        cases = [
            {"url": "--exec=calc.exe"},
            {"url": "https://youtu.be/x", "container": "--exec=calc"},
            {"url": "https://youtu.be/x", "audio_format": "wav; calc"},
            {"url": "https://youtu.be/x", "height": "1080 --exec"},
            {"url": "https://youtu.be/x", "format_id": "137 --exec calc"},
        ]
        with mock.patch.object(server.subprocess, "Popen", side_effect=AssertionError("yt-dlp was run")):
            for body in cases:
                status, _ = self.client.post("/api/download", body)
                self.assertEqual(status, 400, body)
        self.assertEqual(server.JOBS, {})


class DownloadFolder(ServerTest):
    def test_a_chosen_folder_is_created_used_and_remembered(self):
        target = TMP / "chosen" / "videos"
        status, cfg = self.client.post("/api/folder", {"path": str(target)})
        self.assertEqual(status, 200, cfg)
        self.assertTrue(target.is_dir())
        self.assertEqual(Path(cfg["download_dir"]), target.resolve())
        self.assertFalse(cfg["auto_dir"])
        saved = json.loads((TMP / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["download_dir"], str(target))
        cmd = server.download_command("https://youtu.be/x", {}, server.DOWNLOAD_DIR, TMP / "d.txt")
        self.assertTrue(cmd[cmd.index("-o") + 1].startswith(str(target.resolve())))

    def test_the_browser_folder_is_the_default(self):
        browser = TMP / "browser-downloads"
        browser.mkdir(exist_ok=True)
        with mock.patch.object(server, "browser_download_dir", return_value=browser):
            status, cfg = self.client.post("/api/folder", {"path": ""})
        self.assertEqual(status, 200, cfg)
        self.assertTrue(cfg["auto_dir"])
        self.assertEqual(Path(cfg["download_dir"]), browser.resolve())
        self.assertEqual(json.loads((TMP / "config.json").read_text(encoding="utf-8"))["download_dir"], "")

    def test_a_relative_path_is_refused(self):
        status, body = self.client.post("/api/folder", {"path": "videos"})
        self.assertEqual(status, 400)
        self.assertIn("full path", body["error"])

    def test_other_devices_cannot_change_the_folder(self):
        self.assertFalse(server.is_local_client("192.0.2.10"))
        self.assertTrue(server.is_local_client("127.0.0.1"))
        self.assertTrue(server.is_local_client("::ffff:127.0.0.1"))
        with mock.patch.object(server, "is_local_client", return_value=False):
            status, _ = self.client.post("/api/folder", {"path": str(TMP / "nope")})
            self.assertEqual(status, 403)
            status, _ = self.client.post("/api/pick-folder", {})
            self.assertEqual(status, 403)
            _, cfg = self.client.get("/api/config")
            self.assertFalse(cfg["can_change_dir"])
        self.assertFalse((TMP / "nope").exists())

    def test_finished_downloads_stay_playable_after_the_folder_changes(self):
        old = server.DOWNLOAD_DIR
        (old / "clip.mp4").write_bytes(b"video")
        with server.JOBS_LOCK:
            server.JOBS["a1"] = {"id": "a1", "status": "done", "file": "clip.mp4", "dir": str(old), "created": 1}
        server.set_download_dir(str(TMP / "elsewhere"))
        status, body = self.client.get("/media/a1")
        self.assertEqual((status, body), (200, b"video"))
        # A guessed filename is never enough; only the opaque completed job id works.
        self.assertEqual(self.client.get("/media/clip.mp4")[0], 404)
        self.assertEqual(self.client.get("/media/..%2Fconfig.json")[0], 404)
        _, state = self.client.get("/api/state")
        self.assertNotIn("dir", state["jobs"][0], "folder paths are not sent to other devices")


class HttpSecurity(ServerTest):
    def finished_job(self, data=b"0123456789"):
        path = server.DOWNLOAD_DIR / "clip.mp4"
        path.write_bytes(data)
        with server.JOBS_LOCK:
            server.JOBS["abc123"] = {
                "id": "abc123", "status": "done", "file": path.name,
                "dir": str(path.parent), "created": 1,
            }
        return path

    def test_post_requires_small_json_objects(self):
        status, _ = self.client.request(
            "POST", "/api/forget", b"{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 400)
        status, _ = self.client.request("POST", "/api/forget", b"[]")
        self.assertEqual(status, 400)
        status, _ = self.client.request(
            "POST", "/api/forget", b" " * (server.MAX_JSON_BODY + 1))
        self.assertEqual(status, 413)

    def test_cross_site_posts_are_rejected(self):
        status, _ = self.client.request(
            "POST", "/api/forget", {}, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        origin = f"http://127.0.0.1:{self.client.port}"
        status, _ = self.client.request("POST", "/api/forget", {}, headers={"Origin": origin})
        self.assertEqual(status, 200)

    def test_media_is_bound_to_a_completed_job(self):
        path = self.finished_job()
        self.assertEqual(self.client.get("/media/abc123"), (200, path.read_bytes()))
        with server.JOBS_LOCK:
            server.JOBS["abc123"]["status"] = "running"
        self.assertEqual(self.client.get("/media/abc123")[0], 404)

    def test_ranges_and_head_are_strict(self):
        self.finished_job()
        status, body, headers = self.client.request(
            "GET", "/media/abc123", headers={"Range": "bytes=2-5"}, return_headers=True)
        self.assertEqual((status, body), (206, b"2345"))
        self.assertEqual(headers["Content-Range"], "bytes 2-5/10")
        for invalid in ("bytes=7-3", "bytes=10-", "bytes=-0", "bytes=1-2,4-5", "nonsense"):
            self.assertEqual(self.client.request(
                "GET", "/media/abc123", headers={"Range": invalid})[0], 416, invalid)
        status, body, headers = self.client.request(
            "HEAD", "/media/abc123", return_headers=True)
        self.assertEqual((status, body, headers["Content-Length"]), (200, b"", "10"))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_downloads_are_bounded(self):
        with server.JOBS_LOCK:
            for index in range(server.MAX_ACTIVE_DOWNLOADS):
                server.JOBS[str(index)] = {"status": "running"}
        status, body = self.client.post("/api/download", {"url": "https://youtu.be/x"})
        self.assertEqual(status, 429)
        self.assertIn("At most", body["error"])

    def test_only_recent_jobs_are_retained(self):
        with server.JOBS_LOCK:
            for index in range(server.MAX_RETAINED_JOBS):
                key = f"old{index}"
                server.JOBS[key] = {"id": key, "status": "done", "created": index}
        with mock.patch.object(server.threading, "Thread") as thread:
            new_id = server.start_job("https://youtu.be/x", {})
        self.assertEqual(len(server.JOBS), server.MAX_RETAINED_JOBS)
        self.assertNotIn("old0", server.JOBS)
        self.assertIn(new_id, server.JOBS)
        thread.return_value.start.assert_called_once()


class FinishedJob(unittest.TestCase):
    def test_file_details_are_ready_before_the_job_is_marked_done(self):
        clip = TMP / "finished.mp4"
        clip.write_bytes(b"x")
        job = {"id": "j", "status": "running", "stage": "Downloading"}
        seen = {}

        def slow_codecs(path):
            seen["status_while_reading"] = job["status"]
            return {"video": "h264", "audio": "aac"}

        with mock.patch.object(server, "file_codecs", side_effect=slow_codecs):
            server.finish(job, "done", file=str(clip))
        self.assertEqual(seen["status_while_reading"], "running")
        self.assertEqual((job["status"], job["codecs"]["audio"], job["percent"]), ("done", "aac", 100))


class BrowserFolder(unittest.TestCase):
    def make_browser(self, folder):
        root = Path(tempfile.mkdtemp(dir=TMP))
        (root / "Local State").write_text(json.dumps({"profile": {"last_used": "Profile 3"}}), encoding="utf-8")
        (root / "Profile 3").mkdir()
        prefs = {"download": {"default_directory": str(folder)}} if folder else {}
        (root / "Profile 3" / "Preferences").write_text(json.dumps(prefs), encoding="utf-8")
        return root

    def dirs(self, roots):
        return mock.patch.dict(server.CHROMIUM_DIRS, {"nt": roots, "darwin": roots, "linux": roots})

    def test_uses_the_folder_from_the_last_used_profile(self):
        custom = TMP / "chrome-folder"
        custom.mkdir(exist_ok=True)
        with self.dirs([TMP / "missing-browser", self.make_browser(custom)]):
            self.assertEqual(server.chromium_download_dir(), custom)
            self.assertEqual(server.browser_download_dir(), custom)

    def test_falls_back_to_the_system_downloads_folder(self):
        with self.dirs([self.make_browser(None)]):
            self.assertIsNone(server.chromium_download_dir())
            self.assertEqual(server.browser_download_dir(), server.system_downloads_dir())
        with self.dirs([]):
            self.assertIsNone(server.chromium_download_dir())


if __name__ == "__main__":
    unittest.main()
