"""
Unit tests for VisionExpert routing, config, and utility logic.
Covers: _should_execute_code, _is_codegen_task, _vision_api_configured,
        temp directory cleanup, language/format detection.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kaiwu.core.context import TaskContext
from kaiwu.experts.vision_expert import (
    VisionExpert,
    validate_image_path,
    save_clipboard_image,
    get_image_info,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_COUNT,
)


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def mock_llm():
    return MagicMock()


@pytest.fixture
def expert(mock_llm):
    e = VisionExpert(llm=mock_llm)
    yield e
    e.cleanup()


@pytest.fixture
def ctx_factory():
    """Return a factory that builds a TaskContext with optional fields."""
    def _make(user_input: str = "", image_paths=None, image_path=None):
        ctx = TaskContext(user_input=user_input)
        ctx.image_paths = image_paths
        ctx.image_path = image_path
        return ctx
    return _make


# ── 1. _should_execute_code keyword matching ────────────────────────

class TestShouldExecuteCode:
    """_should_execute_code must match compound phrases, not single words."""

    def test_chinese_execute_code(self, expert: VisionExpert):
        assert expert._should_execute_code("请执行代码并运行") is True

    def test_chinese_run_code(self, expert: VisionExpert):
        assert expert._should_execute_code("运行代码") is True

    def test_english_run_code(self, expert: VisionExpert):
        assert expert._should_execute_code("please run code") is True

    def test_english_execute_code(self, expert: VisionExpert):
        assert expert._should_execute_code("execute the code") is True

    def test_single_word_execute_rejected(self, expert: VisionExpert):
        """Single verb '执行' without target should NOT match."""
        assert expert._should_execute_code("执行") is False

    def test_single_word_run_rejected(self, expert: VisionExpert):
        """Single verb 'run' without target should NOT match."""
        assert expert._should_execute_code("run") is False

    def test_empty_string(self, expert: VisionExpert):
        assert expert._should_execute_code("") is False

    def test_run_with_extra_context(self, expert: VisionExpert):
        assert expert._should_execute_code("请运行一下这个代码") is True

    def test_case_insensitive(self, expert: VisionExpert):
        assert expert._should_execute_code("RUN CODE") is True

    def test_unrelated_input(self, expert: VisionExpert):
        assert expert._should_execute_code("分析这张图片") is False


# ── 2. _is_codegen_task verb+target matching ────────────────────────

class TestIsCodegenTask:
    """_is_codegen_task must require verb+target combos; single verbs rejected."""

    def test_chinese_generate_code(self, expert: VisionExpert):
        assert expert._is_codegen_task("请生成代码") is True

    def test_chinese_write_page(self, expert: VisionExpert):
        assert expert._is_codegen_task("写页面") is True

    def test_chinese_create_component(self, expert: VisionExpert):
        assert expert._is_codegen_task("创建组件") is True

    def test_english_generate_code(self, expert: VisionExpert):
        assert expert._is_codegen_task("generate code from this screenshot") is True

    def test_english_build_a_component(self, expert: VisionExpert):
        assert expert._is_codegen_task("build a component") is True

    def test_single_verb_rejected(self, expert: VisionExpert):
        """Just '生成' (generate) alone should not trigger codegen."""
        assert expert._is_codegen_task("生成") is False

    def test_analysis_request_rejected(self, expert: VisionExpert):
        assert expert._is_codegen_task("分析这张图片的内容") is False

    def test_empty_string(self, expert: VisionExpert):
        assert expert._is_codegen_task("") is False

    def test_case_insensitive(self, expert: VisionExpert):
        assert expert._is_codegen_task("WRITE CODE") is True

    def test_implement_function(self, expert: VisionExpert):
        assert expert._is_codegen_task("实现函数") is True


# ── 3. _vision_api_configured logic ─────────────────────────────────

class TestVisionApiConfigured:
    """Must require BOTH URL AND MODEL (not URL OR KEY)."""

    def test_both_url_and_model(self):
        with patch.dict(os.environ, {
            "KWCODE_VISION_API_URL": "https://api.example.com",
            "KWCODE_VISION_MODEL": "gpt-4o",
        }):
            assert VisionExpert._vision_api_configured() is True

    def test_url_only_no_model(self):
        with patch.dict(os.environ, {
            "KWCODE_VISION_API_URL": "https://api.example.com",
        }, clear=False):
            env = os.environ.copy()
            env.pop("KWCODE_VISION_MODEL", None)
            with patch.dict(os.environ, env, clear=True):
                assert VisionExpert._vision_api_configured() is False

    def test_model_only_no_url(self):
        with patch.dict(os.environ, {
            "KWCODE_VISION_MODEL": "gpt-4o",
        }, clear=False):
            env = os.environ.copy()
            env.pop("KWCODE_VISION_API_URL", None)
            with patch.dict(os.environ, env, clear=True):
                assert VisionExpert._vision_api_configured() is False

    def test_neither_set(self):
        env = os.environ.copy()
        env.pop("KWCODE_VISION_API_URL", None)
        env.pop("KWCODE_VISION_MODEL", None)
        env.pop("KWCODE_VISION_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            assert VisionExpert._vision_api_configured() is False

    def test_url_and_key_no_model(self):
        """Having URL + KEY but no MODEL should return False."""
        env = os.environ.copy()
        env.pop("KWCODE_VISION_MODEL", None)
        with patch.dict(os.environ, {
            "KWCODE_VISION_API_URL": "https://api.example.com",
            "KWCODE_VISION_API_KEY": "sk-xxx",
        }, clear=True):
            assert VisionExpert._vision_api_configured() is False


# ── 4. Temp directory cleanup ───────────────────────────────────────

class TestTempDirCleanup:
    """TemporaryDirectory should be created on init, cleaned up on cleanup."""

    def test_temp_dir_created(self, expert: VisionExpert):
        assert expert._temp_dir.exists()

    def test_temp_dir_prefix(self, expert: VisionExpert):
        assert "kwcode_vision_" in str(expert._temp_dir)

    def test_cleanup_removes_dir(self, mock_llm):
        e = VisionExpert(llm=mock_llm)
        temp_path = e._temp_dir
        assert temp_path.exists()
        e.cleanup()
        assert not temp_path.exists()

    def test_double_cleanup_safe(self, mock_llm):
        """Calling cleanup() twice should not raise."""
        e = VisionExpert(llm=mock_llm)
        e.cleanup()
        e.cleanup()  # should not raise


# ── 5. Language / media-type detection (magic bytes) ────────────────

class TestMediaTypeDetection:
    """Magic-byte detection for image formats via _media_type_for_bytes."""

    def test_png_magic(self):
        assert VisionExpert._media_type_for_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) == "image/png"

    def test_jpeg_magic(self):
        assert VisionExpert._media_type_for_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 12) == "image/jpeg"

    def test_gif87a_magic(self):
        assert VisionExpert._media_type_for_bytes(b"GIF87a" + b"\x00" * 10) == "image/gif"

    def test_gif89a_magic(self):
        assert VisionExpert._media_type_for_bytes(b"GIF89a" + b"\x00" * 10) == "image/gif"

    def test_webp_magic(self):
        assert VisionExpert._media_type_for_bytes(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 4) == "image/webp"

    def test_bmp_magic(self):
        assert VisionExpert._media_type_for_bytes(b"BM" + b"\x00" * 14) == "image/bmp"

    def test_unknown_returns_none(self):
        assert VisionExpert._media_type_for_bytes(b"\x00\x00\x00\x00\x00\x00\x00\x00") is None

    def test_empty_bytes(self):
        assert VisionExpert._media_type_for_bytes(b"") is None


class TestMediaTypeBase64:
    """Base64-prefix detection via _media_type_for_base64."""

    def test_png_base64(self):
        # base64 of PNG magic: iVBORw0KGgo...
        sample = "iVBORw0KGgo" + "AAAA" * 5
        assert VisionExpert._media_type_for_base64(sample) == "image/png"

    def test_jpeg_base64(self):
        sample = "/9j/4AAQ" + "AAAA" * 5
        assert VisionExpert._media_type_for_base64(sample) == "image/jpeg"

    def test_gif_base64(self):
        sample = "R0lGODlh" + "AAAA" * 5
        assert VisionExpert._media_type_for_base64(sample) == "image/gif"

    def test_unknown_base64(self):
        assert VisionExpert._media_type_for_base64("AAAA" * 10) is None


# ── 6. _validate_image ─────────────────────────────────────────────

class TestValidateImage:

    def test_nonexistent_file(self, expert: VisionExpert):
        assert expert._validate_image("/nonexistent/path/image.png") is False

    def test_too_large_file(self, expert: VisionExpert, tmp_path):
        big_file = tmp_path / "big.png"
        big_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_IMAGE_BYTES + 1))
        assert expert._validate_image(str(big_file)) is False

    def test_valid_png_file(self, expert: VisionExpert, tmp_path):
        png_file = tmp_path / "test.png"
        png_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        assert expert._validate_image(str(png_file)) is True

    def test_unsupported_format(self, expert: VisionExpert, tmp_path):
        bad_file = tmp_path / "test.txt"
        bad_file.write_bytes(b"not an image" * 10)
        assert expert._validate_image(str(bad_file)) is False


# ── 7. Run routing (no image → error, too many images → error) ─────

class TestRunRouting:

    def test_no_images_returns_error(self, expert: VisionExpert, ctx_factory):
        ctx = ctx_factory(user_input="hello")
        result = expert.run(ctx)
        assert result["success"] is False
        assert "未提供图片路径" in result["output"]

    def test_too_many_images_returns_error(self, expert: VisionExpert, ctx_factory):
        paths = [f"/tmp/img_{i}.png" for i in range(MAX_IMAGE_COUNT + 1)]
        ctx = ctx_factory(user_input="analyze", image_paths=paths)
        result = expert.run(ctx)
        assert result["success"] is False
        assert "最多支持" in result["output"]


# ── 8. _call_vision_llm fallback to API ─────────────────────────────

class TestCallVisionLlmFallback:

    def test_raises_when_api_not_configured(self, mock_llm):
        """When self.llm doesn't support vision and env vars missing → RuntimeError."""
        mock_llm.chat.side_effect = AttributeError("no vision")
        mock_llm._mode = "llamacpp"
        expert = VisionExpert(llm=mock_llm)

        env = os.environ.copy()
        env.pop("KWCODE_VISION_API_URL", None)
        env.pop("KWCODE_VISION_MODEL", None)
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="未显式配置Vision API"):
                expert._call_vision_llm("sys", "user", [])

    def test_falls_back_to_anthropic_api(self, mock_llm):
        """When self.llm raises and env vars are set, should attempt API call."""
        mock_llm.chat.side_effect = AttributeError("no vision")
        mock_llm._mode = "llamacpp"
        expert = VisionExpert(llm=mock_llm)

        with patch.dict(os.environ, {
            "KWCODE_VISION_API_URL": "https://api.example.com/v1/messages",
            "KWCODE_VISION_API_KEY": "sk-test",
            "KWCODE_VISION_MODEL": "gpt-4o",
        }):
            with patch.object(expert, "_call_anthropic_vision", return_value="API result") as mock_api:
                result = expert._call_vision_llm("sys", "user", [{"path": "/tmp/test.png", "base64": "abc", "media_type": "image/png"}])
                assert result == "API result"
                mock_api.assert_called_once()


# ── 9. validate_image_path module-level function ────────────────────

class TestValidateImagePath:

    def test_nonexistent(self):
        assert validate_image_path("/nonexistent/file.png") is False

    def test_valid_png(self, tmp_path):
        f = tmp_path / "ok.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        assert validate_image_path(str(f)) is True


# ── 10. get_image_info (mocks PIL) ─────────────────────────────────

class TestGetImageInfo:

    def test_returns_error_on_invalid_path(self):
        result = get_image_info("/nonexistent/path.png")
        assert "error" in result

    def test_returns_info_for_valid_image(self):
        """Mock PIL.Image.open to return size/format info."""
        mock_img = MagicMock()
        mock_img.__enter__ = MagicMock(return_value=mock_img)
        mock_img.__exit__ = MagicMock(return_value=False)
        mock_img.format = "PNG"
        mock_img.size = (100, 200)
        mock_img.mode = "RGBA"

        mock_pil_image = MagicMock()
        mock_pil_image.open = MagicMock(return_value=mock_img)
        mock_pil = MagicMock()
        mock_pil.Image = mock_pil_image

        with patch.dict("sys.modules", {"PIL": mock_pil, "PIL.Image": mock_pil_image}):
            with patch("os.path.getsize", return_value=1024):
                result = get_image_info("/tmp/test.png")
                assert result["format"] == "PNG"
                assert result["size"] == (100, 200)
                assert result["mode"] == "RGBA"
                assert result["file_size"] == 1024


# ── 11. init stores llm and creates temp dir ───────────────────────

class TestInit:

    def test_stores_llm(self, mock_llm):
        e = VisionExpert(llm=mock_llm)
        assert e.llm is mock_llm
        e.cleanup()

    def test_stores_tool_executor(self, mock_llm):
        tools = MagicMock()
        e = VisionExpert(llm=mock_llm, tool_executor=tools)
        assert e.tools is tools
        e.cleanup()

    def test_none_llm(self):
        e = VisionExpert(llm=None)
        assert e.llm is None
        e.cleanup()


# ── 12. _media_type_for_path fallback ───────────────────────────────

class TestMediaTypeForPath:

    def test_falls_back_to_suffix(self, expert: VisionExpert):
        """If base64 detection fails, should fall back to file extension."""
        result = expert._media_type_for_path("/some/file.jpeg", "AAAA" * 10)
        assert result == "image/jpeg"

    def test_unknown_suffix_defaults_to_png(self, expert: VisionExpert):
        result = expert._media_type_for_path("/some/file.xyz", "AAAA" * 10)
        assert result == "image/png"

    def test_base64_detection_takes_priority(self, expert: VisionExpert):
        # PNG base64 prefix, even with .jpg extension
        result = expert._media_type_for_path("/some/file.jpg", "iVBORw0KGgo" + "AAAA" * 5)
        assert result == "image/png"
