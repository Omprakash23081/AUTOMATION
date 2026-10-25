import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from yt_dlp.utils import DownloadError

from local_agent import youtube_download


class LocalYoutubeDownloadTests(unittest.TestCase):
    def test_authentication_error_is_safe_and_uses_only_the_selected_local_browser(self):
        downloader=Mock()
        downloader.__enter__=Mock(return_value=downloader)
        downloader.__exit__=Mock(return_value=False)
        downloader.extract_info.side_effect=DownloadError(
            "Sign in to confirm you're not a bot. Use --cookies-from-browser."
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(youtube_download,"YoutubeDL",return_value=downloader) as factory:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Sign in to the selected browser profile",
                ) as raised:
                    youtube_download.download_youtube_source(
                        "https://youtu.be/a1B2c3D4e5F",
                        Path(directory),
                        "edge",
                        threading.Event(),
                        Mock(),
                    )

        options=factory.call_args.args[0]
        self.assertEqual(options["cookiesfrombrowser"],("edge",None,None,None))
        self.assertTrue(options["quiet"])
        self.assertTrue(options["no_warnings"])
        self.assertNotIn("cookies",str(raised.exception).lower())

    def test_download_returns_video_and_reports_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/"source.mp4"
            downloader=Mock()
            downloader.__enter__=Mock(return_value=downloader)
            downloader.__exit__=Mock(return_value=False)
            downloader.prepare_filename.return_value=str(output)

            def write_source(*_args,**_kwargs):
                output.write_bytes(b"video")
                options=factory.call_args.args[0]
                options["progress_hooks"][0]({
                    "status":"downloading",
                    "downloaded_bytes":50,
                    "total_bytes":100,
                })
                return {"filepath":str(output)}

            downloader.extract_info.side_effect=write_source
            progress=Mock()
            with patch.object(youtube_download,"YoutubeDL",return_value=downloader) as factory:
                result=youtube_download.download_youtube_source(
                    "https://youtu.be/a1B2c3D4e5F",
                    Path(directory),
                    None,
                    threading.Event(),
                    progress,
                )

        self.assertEqual(result,output)
        progress.assert_called_once_with(50,"Downloading YouTube source on Windows")

    def test_rejects_non_youtube_urls_before_downloading(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(youtube_download,"YoutubeDL") as downloader:
                with self.assertRaisesRegex(ValueError,"HTTPS YouTube"):
                    youtube_download.download_youtube_source(
                        "https://example.com/video",
                        Path(directory),
                        None,
                        threading.Event(),
                        Mock(),
                    )
        downloader.assert_not_called()


if __name__=="__main__":
    unittest.main()
