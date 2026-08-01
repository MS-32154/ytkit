"""Built-in one-shot presets layered on top of the user's config."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any

from .config import Config


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    description: str
    overrides: dict[str, Any]

    def apply(self, cfg: Config) -> Config:
        """Return a new config; the caller's config is never mutated."""
        return replace(cfg.copy(), **deepcopy(self.overrides))


PRESETS: tuple[Preset, ...] = (
    Preset(
        "balanced",
        "Balanced video",
        "1080p MP4 with metadata, thumbnail and English transcript.",
        {
            "mode": "video",
            "video_quality": "1080",
            "container": "mp4",
            "video_codec": "h264",
            "subtitles": True,
            "auto_subtitles": True,
            "transcript_txt": True,
            "embed_metadata": True,
            "embed_chapters": True,
            "write_thumbnail": True,
        },
    ),
    Preset(
        "archival",
        "Archival master",
        "Best available quality in MKV, every subtitle language, everything kept.",
        {
            "mode": "video",
            "video_quality": "best",
            "video_codec": "any",
            "container": "mkv",
            "subtitles": True,
            "auto_subtitles": True,
            "sub_langs": ["all"],
            "sub_format": "srt",
            "embed_subs": True,
            "write_info_json": True,
            "write_description": True,
            "write_thumbnail": True,
            "embed_thumbnail": True,
            "embed_metadata": True,
            "embed_chapters": True,
            "transcript_txt": True,
            "write_summary": True,
        },
    ),
    Preset(
        "audio",
        "Audio only",
        "Best audio extracted to MP3 with cover art and tags.",
        {
            "mode": "audio",
            "audio_format": "mp3",
            "audio_quality": "0",
            "embed_thumbnail": True,
            "embed_metadata": True,
            "write_thumbnail": True,
            "subtitles": False,
            "auto_subtitles": False,
            "transcript_txt": False,
        },
    ),
    Preset(
        "podcast",
        "Podcast / long-form",
        "Opus audio, sponsor segments removed, timestamped transcript.",
        {
            "mode": "audio",
            "audio_format": "opus",
            "audio_quality": "0",
            "embed_metadata": True,
            "embed_thumbnail": True,
            "sponsorblock_remove": ["sponsor", "selfpromo"],
            "subtitles": True,
            "auto_subtitles": True,
            "transcript_txt": True,
            "transcript_timestamps": True,
        },
    ),
    Preset(
        "transcript",
        "Transcript only",
        "No media at all — captions, transcript and metadata sidecars.",
        {
            "mode": "subtitles-only",
            "subtitles": True,
            "auto_subtitles": True,
            "sub_format": "srt",
            "transcript_txt": True,
            "write_info_json": True,
            "write_description": True,
            "write_summary": True,
            "write_thumbnail": False,
        },
    ),
    Preset(
        "metadata",
        "Metadata only",
        "Just the .info.json, description, thumbnail and summary.",
        {
            "mode": "metadata-only",
            "subtitles": False,
            "auto_subtitles": False,
            "transcript_txt": False,
            "write_info_json": True,
            "write_description": True,
            "write_thumbnail": True,
            "write_summary": True,
        },
    ),
    Preset(
        "mobile",
        "Small / mobile",
        "720p H.264 MP4, no sidecars — quick and small.",
        {
            "mode": "video",
            "video_quality": "720",
            "video_codec": "h264",
            "container": "mp4",
            "subtitles": False,
            "auto_subtitles": False,
            "transcript_txt": False,
            "write_info_json": False,
            "write_description": False,
            "write_thumbnail": False,
            "write_summary": False,
        },
    ),
)

BY_KEY = {p.key: p for p in PRESETS}


def apply_preset(cfg: Config, key: str) -> Config:
    if key not in BY_KEY:
        raise KeyError(f"Unknown preset '{key}'. Available: {', '.join(BY_KEY)}")
    return BY_KEY[key].apply(cfg)
