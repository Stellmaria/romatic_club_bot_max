from __future__ import annotations

from webapi.app import _card_file_id


def test_card_image_prefers_legacy_photo_over_non_photo_media() -> None:
    assert (
        _card_file_id(
            {
                "image_id": "photo-file-id",
                "media_type": "video",
                "media_file_id": "video-file-id",
            }
        )
        == "photo-file-id"
    )


def test_card_image_uses_thumbnail_for_video_card() -> None:
    assert (
        _card_file_id(
            {
                "media_type": "video",
                "media_file_id": "video-file-id",
                "thumb_file_id": "thumb-file-id",
            }
        )
        == "thumb-file-id"
    )


def test_card_image_does_not_serve_video_as_jpeg_without_thumbnail() -> None:
    assert _card_file_id({"media_type": "video", "media_file_id": "video-file-id"}) is None


def test_card_image_accepts_photo_media_fallback() -> None:
    assert (
        _card_file_id({"media_type": "photo", "media_file_id": "photo-file-id"})
        == "photo-file-id"
    )
