"""Generate pre-rendered and live real-time dancing RIO logo animations."""

import math
import os
import struct
import subprocess
import tempfile
import zlib

from .config import IMAGE_CACHE_DIR
from .conversion import RIO_CACHE_PREFIX

# Bump version when visual styling changes so cached videos regenerate.
_RIO_ASSET_VERSION = "v1"

# Shared filter graph animating background hue shift + 3 independently dancing letters.
_RIO_FILTER_COMPLEX = (
    "[0:v]scale=1920:1080:flags=bicubic,hue=h=36*t[bg];"
    "[1:v]format=rgba,scale=340:396,"
    "rotate='0.22*sin(4.4*t)':ow=440:oh=480:c=none[lr];"
    "[2:v]format=rgba,scale=300:396,"
    "rotate='0.26*sin(4.4*t+2.1)':ow=440:oh=480:c=none[li];"
    "[3:v]format=rgba,scale=350:396,"
    "rotate='0.30*sin(4.4*t+4.2)':ow=440:oh=480:c=none[lo];"
    "[bg][lr]overlay="
    "x='470+45*sin(2.2*t)':"
    "y='295-85*abs(sin(4.4*t))':shortest=1[v1];"
    "[v1][li]overlay="
    "x='745+45*sin(2.2*t)':"
    "y='295-85*abs(sin(4.4*t+1.05))':shortest=1[v2];"
    "[v2][lo]overlay="
    "x='1010+45*sin(2.2*t)':"
    "y='295-85*abs(sin(4.4*t+2.10))':shortest=1,"
    "format=yuv420p[vout]"
)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _write_rgba_png(path: str, width: int, height: int, pixels: bytearray) -> None:
    """Write an RGBA image buffer to a PNG file using only the standard library."""
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # Filter type 0 (None)
        start = y * stride
        raw.extend(pixels[start : start + stride])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png.extend(_png_chunk(b"IHDR", ihdr))
    png.extend(_png_chunk(b"IDAT", zlib.compress(bytes(raw), level=6)))
    png.extend(_png_chunk(b"IEND", b""))

    with open(path, "wb") as f:
        f.write(png)


def _sd_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Distance from point (px, py) to line segment AB."""
    pax, pay = px - ax, py - ay
    bax, bay = bx - ax, by - ay
    denom = bax * bax + bay * bay
    if denom == 0.0:
        return math.hypot(pax, pay)
    h = max(0.0, min(1.0, (pax * bax + pay * bay) / denom))
    return math.hypot(pax - bax * h, pay - bay * h)


def _sd_r(x: float, y: float) -> float:
    """Signed distance to a bold geometric 'R' in normalized [-1, 1] space."""
    stroke = 0.145
    d = _sd_segment(x, y, -0.42, -0.62, -0.42, 0.62) - stroke
    d = min(d, _sd_segment(x, y, -0.42, -0.62, 0.12, -0.62) - stroke)
    d = min(d, _sd_segment(x, y, -0.42, -0.02, 0.12, -0.02) - stroke)
    bx, by = x - 0.12, y - (-0.32)
    if bx >= 0.0:
        d_arc = abs(math.hypot(bx, by) - 0.30) - stroke
    else:
        d_arc = min(math.hypot(bx, by + 0.30), math.hypot(bx, by - 0.30)) - stroke
    d = min(d, d_arc)
    d = min(d, _sd_segment(x, y, 0.02, -0.02, 0.44, 0.62) - stroke)
    return d


def _sd_i(x: float, y: float) -> float:
    """Signed distance to a bold geometric 'I' in normalized [-1, 1] space."""
    stroke = 0.145
    d = _sd_segment(x, y, 0.0, -0.62, 0.0, 0.62) - stroke
    d = min(d, _sd_segment(x, y, -0.34, -0.62, 0.34, -0.62) - stroke)
    d = min(d, _sd_segment(x, y, -0.34, 0.62, 0.34, 0.62) - stroke)
    return d


def _sd_o(x: float, y: float) -> float:
    """Signed distance to a bold geometric 'O' in normalized [-1, 1] space."""
    stroke = 0.145
    rx, ry = 0.46, 0.62
    nx, ny = x / rx, y / ry
    r = math.hypot(nx, ny)
    if r == 0.0:
        return min(rx, ry) - stroke
    gx, gy = nx / rx, ny / ry
    g = math.hypot(gx, gy)
    dist_to_curve = abs(r - 1.0) / g
    return dist_to_curve - stroke


def _render_letter_png(
    path: str,
    sdf,
    top_rgb: tuple[int, int, int],
    bottom_rgb: tuple[int, int, int],
    glow_rgb: tuple[int, int, int],
    width: int = 240,
    height: int = 280,
) -> None:
    """Render an anti-aliased glowing letter sprite to an RGBA PNG."""
    pixels = bytearray(width * height * 4)
    scale = min(width, height) * 0.48
    cx = width * 0.5
    cy = height * 0.5
    aa = 1.6 / scale

    for py in range(height):
        y = (py - cy) / scale
        t = max(0.0, min(1.0, (y + 0.78) / 1.56))
        fill_r = top_rgb[0] + (bottom_rgb[0] - top_rgb[0]) * t
        fill_g = top_rgb[1] + (bottom_rgb[1] - top_rgb[1]) * t
        fill_b = top_rgb[2] + (bottom_rgb[2] - top_rgb[2]) * t

        row_offset = py * width * 4
        for px in range(width):
            x = (px - cx) / scale
            d = sdf(x, y)

            idx = row_offset + px * 4
            if d < -aa:
                highlight = max(0.0, min(0.32, (-d - 0.03) * 1.4)) * max(0.0, 1.0 - t * 1.3)
                r = min(255, int(fill_r + (255 - fill_r) * highlight))
                g = min(255, int(fill_g + (255 - fill_g) * highlight))
                b = min(255, int(fill_b + (255 - fill_b) * highlight))
                pixels[idx] = r
                pixels[idx + 1] = g
                pixels[idx + 2] = b
                pixels[idx + 3] = 255
            elif d <= aa:
                alpha = max(0.0, min(1.0, (aa - d) / (2.0 * aa)))
                pixels[idx] = min(255, int(fill_r * 0.75 + 255 * 0.25))
                pixels[idx + 1] = min(255, int(fill_g * 0.75 + 255 * 0.25))
                pixels[idx + 2] = min(255, int(fill_b * 0.75 + 255 * 0.25))
                pixels[idx + 3] = int(alpha * 255)
            elif d < 0.24:
                glow = ((0.24 - d) / 0.24) ** 2 * 0.65
                pixels[idx] = glow_rgb[0]
                pixels[idx + 1] = glow_rgb[1]
                pixels[idx + 2] = glow_rgb[2]
                pixels[idx + 3] = int(glow * 255)

    _write_rgba_png(path, width, height, pixels)


def _render_background_png(path: str, width: int = 640, height: int = 360) -> None:
    """Render a vibrant dark-indigo spotlight + retro dancefloor background PNG."""
    pixels = bytearray(width * height * 4)
    cx = width * 0.5
    cy = height * 0.44
    horizon = height * 0.68

    for py in range(height):
        dy = (py - cy) / (height * 0.65)
        row_offset = py * width * 4
        for px in range(width):
            dx = (px - cx) / (width * 0.55)
            dist = math.hypot(dx, dy)
            spot = max(0.0, 1.0 - dist) ** 1.8

            r = 12.0 + 62.0 * spot
            g = 10.0 + 24.0 * spot
            b = 34.0 + 115.0 * spot

            if py > horizon:
                depth = (py - horizon) / (height - horizon)
                gx = abs(((px - cx) / (depth * 140.0 + 18.0)) % 1.0 - 0.5)
                gy = abs((math.log(depth + 0.06) * 2.8) % 1.0 - 0.5)
                if gx > 0.46 or gy > 0.45:
                    glow = depth * 0.65
                    r += 160.0 * glow
                    g += 45.0 * glow
                    b += 210.0 * glow

            idx = row_offset + px * 4
            pixels[idx] = min(255, int(r))
            pixels[idx + 1] = min(255, int(g))
            pixels[idx + 2] = min(255, int(b))
            pixels[idx + 3] = 255

    _write_rgba_png(path, width, height, pixels)


def render_rio_assets(assets_dir: str) -> tuple[str, str, str, str]:
    """Render background and R, I, O sprite PNGs into assets_dir."""
    bg_path = os.path.join(assets_dir, "bg.png")
    r_path = os.path.join(assets_dir, "r.png")
    i_path = os.path.join(assets_dir, "i.png")
    o_path = os.path.join(assets_dir, "o.png")

    _render_background_png(bg_path)
    _render_letter_png(
        r_path, _sd_r, top_rgb=(70, 245, 255), bottom_rgb=(30, 120, 255), glow_rgb=(40, 210, 255)
    )
    _render_letter_png(
        i_path, _sd_i, top_rgb=(255, 110, 230), bottom_rgb=(210, 35, 165), glow_rgb=(255, 70, 210)
    )
    _render_letter_png(
        o_path, _sd_o, top_rgb=(255, 235, 95), bottom_rgb=(255, 125, 30), glow_rgb=(255, 195, 50)
    )
    return bg_path, r_path, i_path, o_path


def build_dancing_rio_live_hls_cmd(assets_dir: str, hls_dir: str) -> list[str]:
    """Build an ffmpeg command that continuously renders a live rolling HLS stream."""
    bg_path = os.path.join(assets_dir, "bg.png")
    r_path = os.path.join(assets_dir, "r.png")
    i_path = os.path.join(assets_dir, "i.png")
    o_path = os.path.join(assets_dir, "o.png")
    playlist_path = os.path.join(hls_dir, "playlist.m3u8")

    return [
        "ffmpeg", "-y",
        "-re",
        "-loop", "1", "-framerate", "30", "-i", bg_path,
        "-loop", "1", "-framerate", "30", "-i", r_path,
        "-loop", "1", "-framerate", "30", "-i", i_path,
        "-loop", "1", "-framerate", "30", "-i", o_path,
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-filter_complex", _RIO_FILTER_COMPLEX,
        "-map", "[vout]",
        "-map", "4:a",
        "-c:v", "libx264",
        "-profile:v", "high",
        "-level:v", "4.1",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-crf", "23",
        "-g", "60",
        "-keyint_min", "60",
        "-sc_threshold", "0",
        "-c:a", "aac",
        "-b:a", "128k",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "6",
        "-hls_flags", "delete_segments+independent_segments+temp_file",
        "-hls_segment_filename", os.path.join(hls_dir, "segment%05d.ts"),
        playlist_path,
    ]


def create_dancing_rio_video(duration: int = 15) -> str | None:
    """Generate (or return cached) 1080p MP4 featuring a dancing RIO logo."""
    if duration <= 0:
        print("❌ Duration must be greater than 0 seconds")
        return None

    os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
    output_path = os.path.join(
        IMAGE_CACHE_DIR, f"{RIO_CACHE_PREFIX}{_RIO_ASSET_VERSION}_{duration}s.mp4"
    )

    if os.path.exists(output_path):
        print("⚡ Using cached dancing RIO video")
        return output_path

    print(f"🕺 Generating {duration}s dancing RIO logo animation...")

    tmp_output = os.path.join(
        IMAGE_CACHE_DIR, f"{RIO_CACHE_PREFIX}{_RIO_ASSET_VERSION}_{duration}s.{os.getpid()}.tmp.mp4"
    )

    with tempfile.TemporaryDirectory(prefix="tv_cast_rio_") as assets_dir:
        bg_path, r_path, i_path, o_path = render_rio_assets(assets_dir)

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-framerate", "30", "-t", str(duration), "-i", bg_path,
            "-loop", "1", "-framerate", "30", "-t", str(duration), "-i", r_path,
            "-loop", "1", "-framerate", "30", "-t", str(duration), "-i", i_path,
            "-loop", "1", "-framerate", "30", "-t", str(duration), "-i", o_path,
            "-f", "lavfi", "-t", str(duration), "-i", "anullsrc=r=44100:cl=stereo",
            "-filter_complex", _RIO_FILTER_COMPLEX,
            "-map", "[vout]",
            "-map", "4:a",
            "-c:v", "libx264",
            "-profile:v", "high",
            "-level:v", "4.1",
            "-preset", "ultrafast",
            "-crf", "22",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            tmp_output,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            print("❌ ffmpeg not found. Please install ffmpeg and try again.")
            return None

    if result.returncode != 0:
        if os.path.exists(tmp_output):
            os.remove(tmp_output)
        print(f"❌ Failed to generate dancing RIO video: {result.stderr[-500:]}")
        return None

    os.replace(tmp_output, output_path)
    print(f"   ✅ Created {duration}s dancing RIO video")
    return output_path
