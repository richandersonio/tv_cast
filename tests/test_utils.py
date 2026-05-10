from tv_cast.utils import format_size, is_youtube_url


def test_format_size_uses_human_units():
    assert format_size(42) == "42 B"
    assert format_size(1536) == "1.5 KB"
    assert format_size(2 * 1024 * 1024) == "2.0 MB"
    assert format_size(3 * 1024 * 1024 * 1024) == "3.0 GB"


def test_is_youtube_url_accepts_common_video_urls():
    assert is_youtube_url("https://www.youtube.com/watch?v=vKQi3bBA1y8")
    assert is_youtube_url("https://youtu.be/vKQi3bBA1y8")
    assert is_youtube_url("https://youtube.com/shorts/example")
    assert is_youtube_url("https://youtube.com/live/example")


def test_is_youtube_url_rejects_non_youtube_urls():
    assert not is_youtube_url("https://example.com/watch?v=vKQi3bBA1y8")
    assert not is_youtube_url("/Users/example/video.mp4")
