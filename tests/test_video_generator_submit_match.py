"""Unit tests for Jimeng asset submit_id matching."""
from pathlib import Path

from app.core.browser import CookieManager
from app.core.video_generator import JimengVideoGenerator


def _generator() -> JimengVideoGenerator:
    return JimengVideoGenerator(CookieManager(Path("./data/cookies.json")))


def test_asset_matches_submit_id_with_nested_field():
    gen = _generator()
    submit_id = "616d143c-3e57-4bac-a5e4-5685250e09ae"
    asset = {
        "asset_id": "a1",
        "agent_conversation_session": {
            "tool_calls": [
                {
                    "name": "video_gen",
                    "args": {"submit_id": submit_id},
                }
            ]
        },
    }
    assert gen._asset_matches_binding(asset, submit_id) is True


def test_asset_matches_submit_id_with_json_string_field():
    gen = _generator()
    submit_id = "616d143c-3e57-4bac-a5e4-5685250e09ae"
    asset = {
        "asset_id": "a1",
        "agent_conversation_session": (
            '{"tool_calls":[{"name":"video_gen","args":{"submit_id":"'
            + submit_id
            + '"}}]}'
        ),
    }
    assert gen._asset_matches_binding(asset, submit_id) is True


def test_asset_does_not_match_other_submit_id():
    gen = _generator()
    asset = {
        "asset_id": "a1",
        "agent_conversation_session": {
            "tool_calls": [
                {
                    "name": "video_gen",
                    "args": {"submit_id": "other-id"},
                }
            ]
        },
    }
    assert gen._asset_matches_binding(asset, "expected-id") is False


def test_asset_matches_with_provider_item_ids_when_submit_id_missing():
    gen = _generator()
    asset = {
        "asset_id": "a1",
        "agent_conversation_session": {
            "tool_calls": [
                {
                    "name": "video_gen",
                    "args": {"pre_gen_item_ids": ["item-x", "item-y"]},
                }
            ]
        },
    }
    assert gen._asset_matches_binding(asset, "unknown-submit-id", ["item-y"]) is True


def test_build_asset_list_payload_has_expected_shape():
    gen = _generator()
    payload = gen._build_asset_list_payload(gen._asset_list_payload_templates()[0], offset=40)
    assert payload["count"] == 20
    assert payload["direction"] == 1
    assert payload["mode"] == "workbench"
    assert payload["asset_type_list"] == [1, 2, 5, 6, 7, 8, 9, 10]
    assert payload["option"]["hide_story_agent_result"] is True
    assert payload["offset"] == 40


def test_extract_binding_info_contains_generate_id():
    gen = _generator()
    payload = {
        "tool_calls": [
            {
                "args": {
                    "submit_id": "s-1",
                    "generate_id": "g-1",
                    "pre_gen_item_ids": ["i-1"],
                }
            }
        ]
    }
    info = gen._extract_binding_info(payload)
    assert info["submit_id"] == "s-1"
    assert info["generate_id"] == "g-1"
    assert info["pre_gen_item_ids"] == ["i-1"]


def test_select_primary_video_url_prefers_higher_bitrate():
    gen = _generator()
    urls = [
        "https://example.com/a/video/tos/test/o1/?br=480&bt=480&ds=1&mime_type=video_mp4",
        "https://example.com/a/video/tos/test/o2/?br=780&bt=780&ds=2&mime_type=video_mp4",
        "https://example.com/a/video/tos/test/o3/?br=1441&bt=1441&ds=3&mime_type=video_mp4",
    ]
    selected = gen._select_primary_video_url(urls)
    assert selected == urls[2]
