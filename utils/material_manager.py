###############################################################################
#  素材管理器 — 图片/PPT 上传、关键词匹配、幻灯片切换
###############################################################################

import os
import json
import shutil
import re
import uuid
from utils.logger import logger

MATERIALS_DIR = os.path.join("data", "materials")
IMAGES_DIR = os.path.join(MATERIALS_DIR, "images")
CONFIG_PATH = os.path.join(MATERIALS_DIR, "slides_config.json")

# 每个 session 当前的幻灯片 ID
_session_slides: dict = {}  # sessionid -> slide_id

# 每个 session 最近一次整篇投喂的讲稿（带 [PPT:N] 标签的 echo 文本）。
# 提问模式启动时若没带讲稿，就回退用它——展示页「连接后播放预设」是把整篇
# 一次性投给 /human 的，而启动提问会 flush_talk() 清队列，不留底就等于丢稿。
_session_lectures: dict = {}  # sessionid -> lecture text

# 当前上传的 PPT 名称（用于画面水印显示）
_current_ppt_name: str = "Python基础知识——铁路客流量统计"


def set_ppt_name(name: str):
    """设置当前 PPT 名称"""
    global _current_ppt_name
    _current_ppt_name = name


def get_ppt_name() -> str:
    """获取当前 PPT 名称"""
    return _current_ppt_name


def _wrap_text(text: str, font, max_width: int, draw) -> list[str]:
    """文字换行辅助函数"""
    lines = []
    current_line = ""
    for char in text:
        test_line = current_line + char
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] > max_width and current_line:
            lines.append(current_line)
            current_line = char
        else:
            current_line = test_line
    if current_line:
        lines.append(current_line)
    return lines if lines else [text]


def _ensure_dirs():
    """确保素材目录存在"""
    os.makedirs(IMAGES_DIR, exist_ok=True)


def _load_config() -> list:
    """加载幻灯片配置"""
    _ensure_dirs()
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        return []
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_config(config: list):
    """保存幻灯片配置"""
    _ensure_dirs()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def list_slides() -> list:
    """获取所有幻灯片"""
    return _load_config()


def get_slide(slide_id: int) -> dict | None:
    """根据 ID 获取幻灯片"""
    for s in _load_config():
        if s["id"] == slide_id:
            return s
    return None


def add_slide(name: str, image_path: str, keywords: list[str], ppt_id: str = "", ppt_name: str = "") -> dict:
    """添加一个幻灯片"""
    config = _load_config()
    new_id = max([s["id"] for s in config], default=0) + 1
    slide = {
        "id": new_id,
        "name": name,
        "image": image_path,
        "keywords": keywords,
        "ppt_id": ppt_id or "single",
        "ppt_name": ppt_name or name,
    }
    config.append(slide)
    _save_config(config)
    logger.info(f"[素材] 新增幻灯片: id={new_id}, name={name}")
    return slide


def update_slide(slide_id: int, name: str = None, keywords: list[str] = None, ppt_name: str = None) -> dict | None:
    """更新幻灯片信息"""
    config = _load_config()
    for s in config:
        if s["id"] == slide_id:
            if name is not None:
                s["name"] = name
            if keywords is not None:
                s["keywords"] = keywords
            if ppt_name is not None:
                s["ppt_name"] = ppt_name
            _save_config(config)
            logger.info(f"[素材] 更新幻灯片: id={slide_id}")
            return s
    return None


def update_slides_batch(updates: list[dict]) -> int:
    """
    批量更新幻灯片名称和关键词
    updates: [{"id": 1, "name": "新名称", "keywords": ["k1"]}, ...]
    返回更新数量
    """
    config = _load_config()
    updated = 0
    for up in updates:
        for s in config:
            if s["id"] == up.get("id"):
                if "name" in up:
                    s["name"] = up["name"]
                if "keywords" in up:
                    s["keywords"] = up["keywords"]
                updated += 1
                break
    _save_config(config)
    logger.info(f"[素材] 批量更新 {updated} 个幻灯片")
    return updated


def delete_slide(slide_id: int) -> bool:
    """删除幻灯片"""
    config = _load_config()
    for i, s in enumerate(config):
        if s["id"] == slide_id:
            # 删除图片文件
            img_path = os.path.join(os.path.dirname(MATERIALS_DIR), s["image"].lstrip("/"))
            if os.path.exists(img_path):
                os.remove(img_path)
            config.pop(i)
            _save_config(config)
            logger.info(f"[素材] 删除幻灯片: id={slide_id}")
            return True
    return False


def clear_all_slides() -> int:
    """清除所有幻灯片（清空配置 + 删除所有图片文件）"""
    config = _load_config()
    count = len(config)
    # 删除所有图片文件
    if os.path.exists(IMAGES_DIR):
        for f in os.listdir(IMAGES_DIR):
            try:
                os.remove(os.path.join(IMAGES_DIR, f))
            except:
                pass
    # 清空配置
    _save_config([])
    logger.info(f"[素材] 清除全部 {count} 个幻灯片")
    return count


def upload_image(file_bytes: bytes, filename: str) -> str:
    """上传图片，返回相对路径"""
    # 先清除旧文件
    if os.path.exists(IMAGES_DIR):
        for f in os.listdir(IMAGES_DIR):
            try:
                os.remove(os.path.join(IMAGES_DIR, f))
            except:
                pass
    _ensure_dirs()
    # 保留中文文件名
    safe_name = filename.replace("\\", "/").split("/")[-1]
    dest = os.path.join(IMAGES_DIR, safe_name)
    with open(dest, "wb") as f:
        f.write(file_bytes)
    rel_path = f"/materials/images/{safe_name}"
    logger.info(f"[素材] 图片已保存: {rel_path}")
    return rel_path


def upload_ppt(file_bytes: bytes, filename: str) -> list[str]:
    """
    上传 PPT 文件，每页转为图片保存，返回图片路径列表
    优先使用 PowerPoint COM 自动化，失败时回退到 LibreOffice / PIL 渲染
    """
    _ensure_dirs()
    display_name = os.path.splitext(filename)[0]
    base_name = display_name + "_" + uuid.uuid4().hex[:8]
    # 记录 PPT 名称（去掉扩展名），用于画面水印显示
    set_ppt_name(display_name)
    # 保留原始扩展名（.ppt 或 .pptx），避免 PowerPoint COM 无法打开
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ('.ppt', '.pptx'):
        ext = '.pptx'

    # 保存临时 PPT 文件（使用绝对路径，确保 PowerPoint COM 能正确访问）
    temp_ppt_path = os.path.abspath(os.path.join(IMAGES_DIR, f"_temp_{uuid.uuid4().hex}{ext}"))
    with open(temp_ppt_path, "wb") as f:
        f.write(file_bytes)
    logger.info(f"[素材] 临时PPT保存至: {temp_ppt_path}")

    # 尝试使用 PowerPoint COM 自动化（Windows 最可靠）
    image_paths = _convert_with_powerpoint(temp_ppt_path, base_name)
    if image_paths is None:
        # 尝试使用 LibreOffice 高质量转换
        image_paths = _convert_with_libreoffice(temp_ppt_path, base_name)
    if image_paths is None:
        # 最后回退到 PIL 渲染
        logger.info("[素材] PowerPoint 和 LibreOffice 均不可用，回退到 PIL 渲染")
        image_paths = _convert_with_pil(file_bytes, base_name)

    # 清理临时文件
    try:
        os.remove(temp_ppt_path)
    except:
        pass

    return image_paths


def _convert_with_powerpoint(ppt_path: str, base_name: str) -> list[str] | None:
    """
    使用 Windows PowerPoint COM 自动化将 PPT 转为高质量图片
    需要安装 Microsoft PowerPoint，成功返回路径列表，失败返回 None
    """
    try:
        import win32com.client
        import pythoncom
    except ImportError:
        logger.info("[素材] win32com 未安装 (pip install pywin32)，跳过 PowerPoint 转换")
        return None

    powerpoint = None
    presentation = None
    try:
        pythoncom.CoInitialize()
        powerpoint = win32com.client.Dispatch("PowerPoint.Application")
        try:
            powerpoint.Visible = 0  # 不显示窗口（部分版本不允许隐藏，忽略即可）
        except:
            logger.info("[素材] PowerPoint 不允许隐藏窗口，将在可见模式下运行")

        presentation = powerpoint.Presentations.Open(ppt_path, WithWindow=False)

        image_paths = []
        for i in range(1, presentation.Slides.Count + 1):
            page_filename = f"{base_name}_page_{i:03d}.png"
            page_path = os.path.abspath(os.path.join(IMAGES_DIR, page_filename))
            # 导出幻灯片为 PNG 图片（不指定宽高，使用幻灯片原始尺寸）
            presentation.Slides(i).Export(page_path, "PNG")
            rel_path = f"/materials/images/{page_filename}"
            image_paths.append(rel_path)
            logger.info(f"[素材] PPT 第{i}页(PowerPoint) → {rel_path}")

        logger.info(f"[素材] PowerPoint 成功转换 {len(image_paths)} 页")
        return image_paths

    except Exception as e:
        logger.warning(f"[素材] PowerPoint 转换异常: {type(e).__name__}: {e}")
        import traceback
        logger.warning(f"[素材] PowerPoint 转换详细错误:\n{traceback.format_exc()}")
        return None
    finally:
        if presentation:
            try:
                presentation.Close()
            except:
                pass
        if powerpoint:
            try:
                powerpoint.Quit()
            except:
                pass
        try:
            pythoncom.CoUninitialize()
        except:
            pass


def _convert_with_libreoffice(ppt_path: str, base_name: str) -> list[str] | None:
    """
    使用 LibreOffice 将 PPT 转为高质量图片
    成功返回路径列表，失败返回 None
    """
    import subprocess
    import shutil
    import glob as glob_mod

    # 查找 LibreOffice 可执行文件
    soffice_paths = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "soffice",
        "libreoffice",
    ]
    soffice = None
    for path in soffice_paths:
        if os.path.exists(path) or shutil.which(path):
            soffice = path
            break

    if soffice is None:
        logger.info("[素材] LibreOffice 未安装，使用 PIL 渲染")
        return None

    # 使用临时输出目录避免文件名冲突
    temp_out = os.path.join(IMAGES_DIR, "_temp_out")
    os.makedirs(temp_out, exist_ok=True)

    try:
        # 用 LibreOffice 将 PPT 转为 PNG 图片
        cmd = [soffice, '--headless', '--convert-to', 'png',
               '--outdir', temp_out, ppt_path]
        logger.info(f"[素材] 执行 LibreOffice: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            logger.warning(f"[素材] LibreOffice 转换失败 (code={result.returncode}): {result.stderr}")
            shutil.rmtree(temp_out, ignore_errors=True)
            return None

        # 查找生成的 PNG 文件（LibreOffice 命名规则：slide1.png, slide2.png, ...）
        png_files = sorted(glob_mod.glob(os.path.join(temp_out, "*.png")),
                          key=lambda x: int(re.findall(r'(\d+)', os.path.basename(x))[0])
                          if re.findall(r'(\d+)', os.path.basename(x)) else 0)

        if not png_files:
            logger.warning("[素材] LibreOffice 未生成图片文件")
            shutil.rmtree(temp_out, ignore_errors=True)
            return None

        # 重命名并移动到目标目录
        image_paths = []
        for i, png_file in enumerate(png_files):
            new_name = f"{base_name}_page_{i+1:03d}.png"
            new_path = os.path.join(IMAGES_DIR, new_name)
            shutil.move(png_file, new_path)
            rel_path = f"/materials/images/{new_name}"
            image_paths.append(rel_path)
            logger.info(f"[素材] PPT 第{i+1}页(LibreOffice) → {rel_path}")

        logger.info(f"[素材] LibreOffice 成功转换 {len(image_paths)} 页")
        return image_paths

    except subprocess.TimeoutExpired:
        logger.warning("[素材] LibreOffice 转换超时")
        return None
    except Exception as e:
        logger.warning(f"[素材] LibreOffice 转换异常: {e}")
        return None
    finally:
        shutil.rmtree(temp_out, ignore_errors=True)


def _render_shape(shape, img, draw, emu_per_inch, dpi, slide_width, slide_height, group_left=0, group_top=0):
    """递归渲染单个 shape（包括 group 内的子 shape）"""
    import io
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    from PIL import Image, ImageFont, ImageDraw

    left = group_left + max(0, int(shape.left / emu_per_inch * dpi)) if shape.left else group_left
    top = group_top + max(0, int(shape.top / emu_per_inch * dpi)) if shape.top else group_top
    width = min(int(shape.width / emu_per_inch * dpi), slide_width - left) if shape.width else slide_width - left
    height = min(int(shape.height / emu_per_inch * dpi), slide_height - top) if shape.height else slide_height - top

    shape_type = shape.shape_type

    # 处理 Group 形状 — 递归渲染子元素
    if shape_type == MSO_SHAPE_TYPE.GROUP:
        for child in shape.shapes:
            # 子 shape 的坐标是相对于 group 的，需要加上 group 的偏移
            _render_shape(child, img, draw, emu_per_inch, dpi, slide_width, slide_height, 
                         group_left=left, group_top=top)
        return

    # 绘制形状背景填充
    try:
        fill = shape.fill
        if fill and fill.type is not None:
            if hasattr(fill, 'fore_color') and fill.fore_color and fill.fore_color.type is not None:
                try:
                    rgb = fill.fore_color.rgb
                    fill_color = (rgb[0], rgb[1], rgb[2])
                    draw.rectangle([left, top, left + width, top + height], fill=fill_color)
                except:
                    pass
    except:
        pass

    # 处理文本框/占位符
    if shape.has_text_frame:
        text_frame = shape.text_frame
        # 文本背景
        try:
            tf_bg = text_frame.fill
            if tf_bg and tf_bg.type is not None and hasattr(tf_bg, 'fore_color') and tf_bg.fore_color:
                try:
                    rgb = tf_bg.fore_color.rgb
                    draw.rectangle([left, top, left + width, min(top + height, slide_height)],
                                  fill=(rgb[0], rgb[1], rgb[2]))
                except:
                    draw.rectangle([left, top, left + width, min(top + height, slide_height)],
                                  fill="white")
        except:
            draw.rectangle([left, top, left + width, min(top + height, slide_height)],
                          fill="white")

        text_top = top + 8
        for para in text_frame.paragraphs:
            text = para.text
            if not text.strip():
                text_top += 16
                continue

            # 处理每个 run（支持同一段落中不同格式的文本）
            if para.runs:
                for run in para.runs:
                    run_text = run.text
                    if not run_text:
                        continue
                    try:
                        font_size = 14
                        text_color = "black"
                        run_font = run.font.size
                        if run_font:
                            font_size = int(run_font / 12700)
                        if run.font.color and run.font.color.type is not None:
                            try:
                                text_color = '#' + str(run.font.color.rgb)
                            except AttributeError:
                                # _SchemeColor 等类型无 .rgb 属性，使用默认黑色
                                pass
                        font = ImageFont.truetype("simhei.ttf", max(font_size, 10))
                    except:
                        font = ImageFont.load_default()
                        text_color = "black"
                    max_text_width = max(width - 16, 100)
                    lines = _wrap_text(run_text, font, max_text_width, draw)
                    for line in lines:
                        draw.text((left + 8, text_top), line, fill=text_color, font=font)
                        bbox = draw.textbbox((0, 0), line, font=font)
                        text_top += bbox[3] - bbox[1] + 4
            else:
                # 无 run 的段落（纯文本），使用默认字体
                try:
                    font = ImageFont.truetype("simhei.ttf", 14)
                except:
                    font = ImageFont.load_default()
                max_text_width = max(width - 16, 100)
                lines = _wrap_text(text, font, max_text_width, draw)
                for line in lines:
                    draw.text((left + 8, text_top), line, fill="black", font=font)
                    bbox = draw.textbbox((0, 0), line, font=font)
                    text_top += bbox[3] - bbox[1] + 4

    # 处理图片
    elif shape_type == MSO_SHAPE_TYPE.PICTURE:
        try:
            image = shape.image
            img_bytes = image.blob
            pil_img = Image.open(io.BytesIO(img_bytes))
            pil_img = pil_img.resize((width, height))
            if pil_img.mode == "RGBA":
                img.paste(pil_img, (left, top), pil_img)
            else:
                img.paste(pil_img, (left, top))
        except Exception as e:
            logger.warning(f"[素材] 图片提取失败: {e}")

    # 处理表格
    elif hasattr(shape, 'has_table') and shape.has_table:
        table = shape.table
        draw.rectangle([left, top, left + width, top + height], outline="gray")
        row_h = height // max(table.rows.__len__(), 1)
        col_w = width // max(len(table.columns), 1)
        for r_idx, row in enumerate(table.rows):
            for c_idx, cell in enumerate(row.cells):
                cell_left = left + c_idx * col_w
                cell_top = top + r_idx * row_h
                draw.rectangle([cell_left, cell_top, cell_left + col_w, cell_top + row_h],
                              outline="lightgray")
                if cell.text.strip():
                    try:
                        font = ImageFont.truetype("simhei.ttf", 10)
                    except:
                        font = ImageFont.load_default()
                    draw.text((cell_left + 4, cell_top + 4), cell.text[:50],
                             fill="black", font=font)


def _convert_with_pil(file_bytes: bytes, base_name: str) -> list[str]:
    """
    PIL 手动渲染 PPT（回退方案，质量有限）
    """
    try:
        from pptx import Presentation
        from pptx.util import Inches
        from PIL import Image, ImageDraw, ImageFont
        import io
    except ImportError:
        raise ImportError("需要安装 python-pptx 和 Pillow: pip install python-pptx Pillow")

    prs = Presentation(io.BytesIO(file_bytes))
    emu_per_inch = 914400
    dpi = 200
    slide_width = int(prs.slide_width / emu_per_inch * dpi)
    slide_height = int(prs.slide_height / emu_per_inch * dpi)
    max_size = 3200
    if slide_width > max_size: slide_height = int(slide_height * max_size / slide_width); slide_width = max_size
    if slide_height > max_size: slide_width = int(slide_width * max_size / slide_height); slide_height = max_size

    image_paths = []
    for i, slide in enumerate(prs.slides):
        img = Image.new("RGB", (slide_width, slide_height), "white")
        draw = ImageDraw.Draw(img)

        # 绘制背景
        bg = slide.background
        if bg.fill and bg.fill.type is not None:
            try:
                if hasattr(bg.fill, 'fore_color') and bg.fill.fore_color:
                    rgb = bg.fill.fore_color.rgb
                    img = Image.new("RGB", (slide_width, slide_height),
                                    (rgb[0], rgb[1], rgb[2]))
                    draw = ImageDraw.Draw(img)
            except:
                pass

        for shape in slide.shapes:
            _render_shape(shape, img, draw, emu_per_inch, dpi, slide_width, slide_height)

        page_filename = f"{base_name}_page_{i+1:03d}.png"
        page_path = os.path.join(IMAGES_DIR, page_filename)
        img.save(page_path, "PNG")
        rel_path = f"/materials/images/{page_filename}"
        image_paths.append(rel_path)
        logger.info(f"[素材] PPT 第{i+1}页(PIL) → {rel_path}")

    return image_paths


def match_slide(text: str) -> int | None:
    """
    根据文本内容匹配幻灯片
    返回匹配到的幻灯片 ID，没有匹配返回 None
    """
    config = _load_config()
    if not config:
        return None

    text_lower = text.lower()
    best_match = None
    best_score = 0

    for slide in config:
        score = 0
        for kw in slide.get("keywords", []):
            if kw.lower() in text_lower:
                score += len(kw)  # 更长的关键词匹配权重更高
        if score > best_score:
            best_score = score
            best_match = slide["id"]

    return best_match


# ─── Session 幻灯片状态管理 ──────────────────────────────────────────

def set_current_slide(sessionid: str, slide_id: int | None):
    """设置当前 session 的幻灯片 ID"""
    _session_slides[sessionid] = slide_id


def get_current_slide(sessionid: str) -> int | None:
    """获取当前 session 的幻灯片 ID，默认返回 1"""
    return _session_slides.get(sessionid, 1)


def slide_reached(current, target) -> bool:
    """判定"当前幻灯片是否已经到达（或越过）目标页"。

    按课件里的**页序**比较，而不是 slide_id 的数值大小：slide_id 是自增 ID，
    跳页/删页后与页码不再一致，直接比大小会把第 4 页判成"未到"（第 5 页都在放了）。
    取不到页序时（例如素材库为空）退回数值比较。
    """
    if target is None:
        return True
    if current is None:
        return False
    try:
        order = {slide["id"]: index for index, slide in enumerate(list_slides())}
    except Exception:  # noqa: BLE001 - 素材库读不出来时不能让课堂停住
        order = {}
    if current in order and target in order:
        return order[current] >= order[target]
    try:
        return int(current) >= int(target)
    except (TypeError, ValueError):
        return True


def remember_lecture(sessionid: str, text: str):
    """记下本会话最近一次投喂的整篇讲稿。

    只在 /human 的 echo 分支、且文本含 [PPT:N] 标签时调用：不带标签的长文本
    可能是闲聊或 RAG 朗读，误当讲稿会把提问点全部错位。
    """
    if not sessionid or not text:
        return
    _session_lectures[sessionid] = text


def get_lecture(sessionid: str) -> str:
    """取本会话最近一次投喂的整篇讲稿；没有则返回空串。"""
    return _session_lectures.get(sessionid) or ""


def forget_lecture(sessionid: str):
    """会话销毁时清掉讲稿留底，避免字典随刷新无限增长。"""
    _session_lectures.pop(sessionid, None)


def check_and_update_slide(sessionid: str, text: str):
    """
    检查文本是否匹配某个幻灯片，如有匹配则更新
    供 llm.py / tts 模块调用
    """
    slide_id = match_slide(text)
    if slide_id is not None:
        _session_slides[sessionid] = slide_id
