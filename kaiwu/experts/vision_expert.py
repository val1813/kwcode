"""
VisionExpert: 多模态图片处理专家
支持图片上传、剪贴板粘贴、图片分析和基于图片的代码生成。

Pipeline: 图片输入 → 图片分析 → 任务路由 → 代码生成/图片描述
"""

import base64
import logging
import os
import shlex
import tempfile
from pathlib import Path
from typing import Optional

from kaiwu.core.context import TaskContext

logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_COUNT = 20

# ── Vision system prompts ──────────────────────────────────────────

VISION_ANALYSIS_SYSTEM = """\
你是KWCode的多模态视觉专家。用户上传了一张图片，请根据图片内容提供有用的分析。

## 分析类型
1. **代码截图分析**：如果图片包含代码、错误信息、终端输出
   - 识别代码语言和功能
   - 分析错误信息并提供修复建议
   - 如果用户有相关任务，提供代码修改建议

2. **UI/设计图分析**：如果图片是UI设计、网页截图、界面原型
   - 描述布局和设计元素
   - 提供实现建议（HTML/CSS/框架）
   - 识别可复用的组件

3. **文档/表格分析**：如果图片是文档、表格、图表
   - 提取关键信息
   - 结构化数据
   - 提供数据处理建议

4. **通用图片分析**：其他类型图片
   - 详细描述图片内容
   - 识别关键元素
   - 提供相关建议

## 输出格式
- 简洁明了，2-5句话
- 如果是代码相关，提供具体的修改建议
- 如果是UI设计，提供实现方向
- 使用中文回复"""

VISION_CODEGEN_SYSTEM = """\
你是KWCode的多模态代码生成专家。用户上传了图片并要求生成代码。

## 代码生成规则
1. **UI截图 → HTML/CSS**
   - 使用Tailwind CSS
   - 响应式设计
   - 保持视觉一致性

2. **错误截图 → 修复代码**
   - 分析错误信息
   - 定位问题根源
   - 提供最小化修复

3. **设计图 → 组件代码**
   - 选择合适的框架（React/Vue/原生）
   - 模块化组件设计
   - 可复用性优先

## 输出要求
- 只输出完整可执行的代码
- 不要解释，不要markdown代码块标记
- 代码末尾添加使用说明注释"""


class VisionExpert:
    """多模态图片处理专家：分析图片内容，生成代码或提供分析"""

    def __init__(self, llm, tool_executor=None):
        self.llm = llm
        self.tools = tool_executor
        self._temp_dir = Path(tempfile.mkdtemp(prefix="kwcode_vision_"))

    def run(self, ctx: TaskContext) -> dict:
        """
        处理图片输入任务
        
        Args:
            ctx: 任务上下文，包含 user_input 和 image_path
            
        Returns:
            dict: 包含 success, output, metadata
        """
        image_paths = list(getattr(ctx, 'image_paths', []) or [])
        image_path = getattr(ctx, 'image_path', None)
        if not image_paths and image_path:
            image_paths = [image_path]
        
        if not image_paths:
            return {
                "success": False,
                "output": "错误：未提供图片路径",
                "metadata": {"error": "no_image_path"}
            }
        if len(image_paths) > MAX_IMAGE_COUNT:
            return {
                "success": False,
                "output": f"错误：一次最多支持 {MAX_IMAGE_COUNT} 张图片",
                "metadata": {"error": "too_many_images", "count": len(image_paths)}
            }
        
        # 验证图片文件
        invalid_paths = [path for path in image_paths if not self._validate_image(path)]
        if invalid_paths:
            return {
                "success": False,
                "output": f"错误：图片文件不存在、过大或格式不支持: {', '.join(invalid_paths)}",
                "metadata": {"error": "invalid_image", "paths": invalid_paths}
            }
        
        # 分析用户意图
        user_input = ctx.user_input.strip()
        is_codegen_task = self._is_codegen_task(user_input)
        
        try:
            images = []
            for path in image_paths:
                image_base64 = self._encode_image(path)
                images.append({
                    "path": path,
                    "base64": image_base64,
                    "media_type": self._media_type_for_path(path, image_base64),
                })
            
            if is_codegen_task:
                return self._run_codegen(ctx, images)
            else:
                return self._run_analysis(ctx, images)
                
        except Exception as e:
            logger.error(f"VisionExpert error: {e}")
            return {
                "success": False,
                "output": f"图片处理失败: {str(e)}",
                "metadata": {"error": str(e)}
            }

    def _validate_image(self, image_path: str) -> bool:
        """验证图片文件是否存在且格式支持"""
        path = Path(image_path).expanduser()
        if not path.exists() or not path.is_file():
            return False
        if path.stat().st_size > MAX_IMAGE_BYTES:
            return False
        
        try:
            with path.open("rb") as f:
                return self._media_type_for_bytes(f.read(16)) is not None
        except OSError:
            return False

    def _encode_image(self, image_path: str) -> str:
        """将图片编码为base64"""
        with open(Path(image_path).expanduser(), "rb") as f:
            return base64.b64encode(f.read()).decode('utf-8')

    def _media_type_for_path(self, image_path: str, image_base64: str) -> str:
        """Return a MIME type compatible with common vision APIs."""
        media_type = self._media_type_for_base64(image_base64)
        if media_type:
            return media_type

        suffix_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }
        suffix = Path(image_path).suffix.lower()
        if suffix in suffix_map:
            return suffix_map[suffix]

        return "image/png"

    @staticmethod
    def _media_type_for_base64(image_base64: str) -> Optional[str]:
        raw_sample = image_base64[:20]
        if raw_sample.startswith("iVBORw0KGgo"):
            return "image/png"
        if raw_sample.startswith("/9j"):
            return "image/jpeg"
        if raw_sample.startswith("R0lGOD"):
            return "image/gif"
        if raw_sample.startswith("UklGR"):
            return "image/webp"
        if raw_sample.startswith("Qk"):
            return "image/bmp"
        return None

    @staticmethod
    def _media_type_for_bytes(raw: bytes) -> Optional[str]:
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if raw.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if raw.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
            return "image/webp"
        if raw.startswith(b"BM"):
            return "image/bmp"
        return None

    def _is_codegen_task(self, user_input: str) -> bool:
        """判断是否为代码生成任务"""
        codegen_keywords = [
            "生成代码", "写代码", "实现", "创建", "生成",
            "代码", "函数", "类", "组件", "页面",
            "generate", "create", "implement", "code",
            "html", "css", "javascript", "python", "react", "vue"
        ]
        
        # 检查是否包含代码相关关键词
        user_input_lower = user_input.lower()
        return any(kw in user_input_lower for kw in codegen_keywords)

    def _run_analysis(self, ctx: TaskContext, images: list[dict]) -> dict:
        """运行图片分析"""
        logger.info("[vision] 分析图片: %s", ", ".join(img["path"] for img in images))
        
        # 构建提示词
        user_input = ctx.user_input.strip()
        image_count = len(images)
        if user_input:
            prompt = f"用户上传了{image_count}张图片并说：{user_input}\n\n请结合所有图片进行分析。"
        else:
            prompt = f"用户上传了{image_count}张图片，请分析其内容。"
        
        # 调用Vision LLM
        response = self._call_vision_llm(
            system_prompt=VISION_ANALYSIS_SYSTEM,
            user_prompt=prompt,
            images=images,
        )
        
        return {
            "success": True,
            "output": response,
            "metadata": {
                "type": "vision_analysis",
                "image_paths": [img["path"] for img in images],
                "image_count": image_count,
                "has_user_task": bool(user_input)
            }
        }

    def _run_codegen(self, ctx: TaskContext, images: list[dict]) -> dict:
        """运行基于图片的代码生成"""
        logger.info("[vision] 基于图片生成代码: %s", ", ".join(img["path"] for img in images))
        
        user_input = ctx.user_input.strip()
        image_count = len(images)
        
        # 构建提示词
        prompt = f"用户上传了{image_count}张图片并要求：{user_input}\n\n请结合所有图片内容生成代码。"
        
        # 调用Vision LLM
        response = self._call_vision_llm(
            system_prompt=VISION_CODEGEN_SYSTEM,
            user_prompt=prompt,
            images=images,
        )
        
        # 尝试执行生成的代码（如果用户要求）
        if self.tools and self._should_execute_code(user_input):
            execution_result = self._execute_generated_code(response)
            if execution_result:
                response += f"\n\n--- 执行结果 ---\n{execution_result}"
        
        return {
            "success": True,
            "output": response,
            "metadata": {
                "type": "vision_codegen",
                "image_paths": [img["path"] for img in images],
                "image_count": image_count,
                "task": user_input
            }
        }

    def _call_vision_llm(self, system_prompt: str, user_prompt: str, images: list[dict]) -> str:
        """调用支持Vision的LLM (Anthropic Messages API 格式)

        优先使用 self.llm (如果支持 vision)，否则回退到环境变量配置的 API：
          KWCODE_VISION_API_URL   - API endpoint (Anthropic Messages API 格式，必填)
          KWCODE_VISION_API_KEY   - API key (必填)
          KWCODE_VISION_MODEL     - 模型名 (必填，需支持多模态，如 mimo-v2-omni)
        """
        # 尝试通过 self.llm 直接调用（如果后端支持多模态）
        if self.llm is not None:
            try:
                return self._try_llm_vision(system_prompt, user_prompt, images)
            except Exception as e:
                logger.debug(f"[vision] self.llm 不支持 vision，回退到 API: {e}")

        if not self._vision_api_configured():
            raise RuntimeError(
                "本地模型不支持图片输入，且未显式配置Vision API；"
                "请设置 KWCODE_VISION_API_URL 或 KWCODE_VISION_API_KEY 后重试"
            )

        # 回退：直接调用 Anthropic Messages API
        return self._call_anthropic_vision(system_prompt, user_prompt, images)

    @staticmethod
    def _vision_api_configured() -> bool:
        return bool(os.environ.get("KWCODE_VISION_API_URL") or os.environ.get("KWCODE_VISION_API_KEY"))

    def _try_llm_vision(self, system_prompt: str, user_prompt: str, images: list[dict]) -> str:
        """尝试通过 self.llm 的 chat 接口发送多模态请求"""
        # LLMBackend supports two HTTP styles. Native llama.cpp cannot consume
        # image payloads here, so force the documented vision API fallback.
        mode = getattr(self.llm, "_mode", None)
        is_openai_compat = getattr(self.llm, "_is_openai_compat", False)
        if mode and mode != "ollama":
            raise AttributeError("current LLM backend does not support image chat payloads")

        if mode == "ollama" and not is_openai_compat:
            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": user_prompt,
                    "images": [img["base64"] for img in images],
                },
            ]
            return self.llm.chat(messages, max_tokens=2048)

        content = [{"type": "text", "text": user_prompt}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{img['media_type']};base64,{img['base64']}"},
            }
            for img in images
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": content,
            },
        ]
        return self.llm.chat(messages, max_tokens=2048)

    def _call_anthropic_vision(self, system_prompt: str, user_prompt: str, images: list[dict]) -> str:
        """直接调用 Anthropic Messages API（兼容 xiaomimimo 等代理）"""
        import json as _json
        import urllib.request
        import urllib.error

        api_url = os.environ.get("KWCODE_VISION_API_URL", "")
        api_key = os.environ.get("KWCODE_VISION_API_KEY", "")
        model = os.environ.get("KWCODE_VISION_MODEL", "")

        if not api_url or not model:
            raise RuntimeError(
                "Vision API 未配置。请设置环境变量：\n"
                "  export KWCODE_VISION_API_URL=<Anthropic Messages API endpoint>\n"
                "  export KWCODE_VISION_API_KEY=<your api key>\n"
                "  export KWCODE_VISION_MODEL=<multimodal model name>\n"
                "示例：\n"
                "  export KWCODE_VISION_API_URL=https://your-provider.com/v1/messages\n"
                "  export KWCODE_VISION_API_KEY=sk-xxx\n"
                "  export KWCODE_VISION_MODEL=gpt-4o"
            )

        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img["media_type"],
                    "data": img["base64"],
                },
            }
            for img in images
        ]
        content.append({"type": "text", "text": user_prompt})

        payload = {
            "model": model,
            "max_tokens": 2048,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
        }

        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if api_key:
            headers["x-api-key"] = api_key

        logger.info(f"[vision] 调用 {api_url} model={model}")
        data_bytes = _json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(api_url, data=data_bytes, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                result = _json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"Vision API HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Vision API connection failed: {e.reason}") from e
        except TimeoutError as e:
            raise RuntimeError("Vision API request timed out") from e

        # 提取文本
        # 检查 API 级别错误
        if result.get("type") == "error" or result.get("isError"):
            error_msg = result.get("error", {}).get("message", str(result))
            raise RuntimeError(f"Vision API error: {error_msg}")

        stop_reason = result.get("stop_reason", "")
        if stop_reason == "max_tokens":
            logger.warning("[vision] 输出被截断 (max_tokens reached)")

        text_parts = []
        for block in result.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block["text"])

        usage = result.get("usage", {})
        logger.info(
            f"[vision] 完成: {usage.get('input_tokens', '?')} in / "
            f"{usage.get('output_tokens', '?')} out tokens"
        )
        return "\n".join(text_parts) if text_parts else "[VisionExpert] 模型未返回文本内容"

    def _should_execute_code(self, user_input: str) -> bool:
        """判断是否应该执行生成的代码"""
        execute_keywords = ["执行", "运行", "测试", "run", "execute", "test"]
        return any(kw in user_input.lower() for kw in execute_keywords)

    def _execute_generated_code(self, code: str) -> Optional[str]:
        """执行生成的代码"""
        if not self.tools:
            return None
        
        try:
            # 保存代码到临时文件
            temp_file = self._temp_dir / "generated_code.py"
            temp_file.write_text(code, encoding='utf-8')
            
            # 执行代码
            stdout, stderr, returncode = self.tools.run_bash(f"python {shlex.quote(str(temp_file))}")
            output = stdout.strip()
            error = stderr.strip()
            if returncode != 0:
                return f"退出码 {returncode}\n{error or output}".strip()
            return output or error
            
        except Exception as e:
            return f"执行失败: {str(e)}"

    def cleanup(self):
        """清理临时文件"""
        import shutil
        if self._temp_dir.exists():
            shutil.rmtree(self._temp_dir)


def save_clipboard_image() -> Optional[str]:
    """
    从剪贴板保存图片
    
    Returns:
        str: 保存的图片路径，如果剪贴板没有图片则返回None
    """
    try:
        from PIL import Image, ImageGrab
        
        image = ImageGrab.grabclipboard()
        if isinstance(image, Image.Image):
            # 生成临时文件路径
            temp_dir = Path(tempfile.mkdtemp(prefix="kwcode_clipboard_"))
            temp_path = temp_dir / "clipboard_image.png"
            
            # 保存图片
            image.save(temp_path, "PNG")
            logger.info(f"[vision] 剪贴板图片已保存: {temp_path}")
            
            return str(temp_path)
        else:
            logger.debug("[vision] 剪贴板中没有图片")
            return None
            
    except ImportError:
        logger.warning("[vision] Pillow未安装，无法处理剪贴板图片")
        return None
    except Exception as e:
        logger.error(f"[vision] 处理剪贴板图片失败: {e}")
        return None


def validate_image_path(path: str) -> bool:
    """验证图片路径是否有效"""
    path_obj = Path(path).expanduser()
    if not path_obj.exists() or not path_obj.is_file():
        return False
    if path_obj.stat().st_size > MAX_IMAGE_BYTES:
        return False
    
    try:
        with path_obj.open("rb") as f:
            return VisionExpert._media_type_for_bytes(f.read(16)) is not None
    except OSError:
        return False


def get_image_info(image_path: str) -> dict:
    """获取图片基本信息"""
    try:
        from PIL import Image
        
        with Image.open(image_path) as img:
            return {
                "format": img.format,
                "size": img.size,
                "mode": img.mode,
                "file_size": os.path.getsize(image_path)
            }
    except Exception as e:
        return {"error": str(e)}
