# Tasks: 合并课程知识库与 PPT 库，收敛上传入口

## 1. TDD 锁定（先红）

- [x] 1.1 新建 `tests/test_course_library_page.py`：断言 `knowledge-base.html` 含三个 tab 与 PPT 上传/网格/标签命名相关元素；断言 `index.html` 不含素材准备区卡片 ID 与上传函数、保留 `tagPptSelect`；断言 `ppt-library.html` 不存在；断言站内无 `/ppt-library.html` 链接
- [x] 1.2 运行新测试确认红灯（知识库页尚未扩展、首页尚未清理）

## 2. 知识库页扩展

- [x] 2.1 `web/knowledge-base.html`：新增 tab 结构（课程文档 / PPT 课件 / 图片素材），课程文档区原有 DOM 与 JS 原样包进第一个 tab panel
- [x] 2.2 迁入 PPT 课件分区：PPT 上传（`/api/materials/upload-ppt` + 逐页注册 `/api/slides`）、按册分组网格预览、改名、删除整册（逻辑自 `ppt-library.html` 迁移）
- [x] 2.3 迁入分页标签命名：逐页名称 + 关键词表单，`PUT /api/slides/batch` 保存（自首页 `showPptLabelPanel`/`savePptLabels` 迁移）
- [x] 2.4 迁入图片素材上传（`/api/materials/upload-image` + 状态反馈）

## 3. 首页清理与旧页退役

- [x] 3.1 `web/index.html`：删除「素材准备」区（三张卡 + `pptLabelPanel`），删除 `uploadMaterialImage`/`uploadMaterialPPT`/`showPptLabelPanel`/`savePptLabels` 及 `.ppt-page-row`/`.ppt-page-name` 样式
- [x] 3.2 删除 Hero 下方能力条（口型驱动 / TTS / 推流 / 提问式课堂四项）
- [x] 3.3 首页工作流段、页脚中 `/ppt-library.html` 链接改指 `/knowledge-base.html`
- [x] 3.4 删除 `web/ppt-library.html`；`README.md` 页面说明表两行合并为一行

## 4. 验证与归档

- [x] 4.1 `tests/test_course_library_page.py` 转绿
- [x] 4.2 全量 pytest + 前端 `node --test tests/js/*.test.js` + HTML 平衡校验
- [x] 4.3 `openspec validate --all --strict` 通过后归档变更
- [x] 4.4 更新 `.workbuddy/memory/2026-10-24.md`
