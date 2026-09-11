"""
Central media format registry for UT_VFX.
"""

from pathlib import Path

VIDEO_EXTENSIONS = {
    ".mov",
    ".mp4",
    ".avi",
    ".mkv",
    ".m4v",
    ".webm",
    ".mxf",
}

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".tiff",
    ".tif",
    ".exr",
    ".dpx",
    ".bmp",
    ".gif",
    ".webp",
    ".tga",
    ".psd",
    ".hdr",
}

MODEL_EXTENSIONS = {
    ".abc",
    ".fbx",
    ".obj",
    ".usd",
    ".usda",
    ".usdc",
}


def ext(path_or_ext) -> str:
    text = str(path_or_ext)
    if text.startswith("."):
        return text.lower()
    return Path(text).suffix.lower()


def is_video(path_or_ext) -> bool:
    return ext(path_or_ext) in VIDEO_EXTENSIONS


def is_image(path_or_ext) -> bool:
    return ext(path_or_ext) in IMAGE_EXTENSIONS
