# 视频产物

`quality-guardian-v2.mp4` 由 GitHub Pages 作为演示站点静态资源发布，正式分发文件已内嵌中文字幕；制作源文件不进入仓库历史。视频为 1920×1080、H.264/AAC、267 秒。

渲染源位于上级目录：`index.html`、`DESIGN.md` 与 `narration.txt`。本机重新生成配音后，可在该目录临时放置 `narration.m4a` 并运行：

```bash
npx hyperframes render video --output video/assets/quality-guardian-v2.mp4 --quality standard --workers 1
```

发布前应运行 `npx hyperframes lint video`，并抽检首屏、案卷页和结束页。公开案例不包含任何原始脱敏工作簿内容。
