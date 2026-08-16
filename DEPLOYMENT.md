# GitHub 与在线演示

本项目的目标仓库是：

`https://github.com/Gikemj/tightening-quality-guardian`

## 在线演示

GitHub Pages 站点地址：

`https://gikemj.github.io/tightening-quality-guardian/`

站点文件位于 `docs/`，固定入口为 `docs/round2.html`；`docs/index.html` 仅作为旧入口的零秒跳转页。管理员页的“打开第二轮公开演示”同页指向 `round2.html`，视频入口指向 `docs/video/index.html` 播放页，MP4 直链保持不变。页面使用仓库内的示例数据和生成结果，不需要后端服务或数据库。

## 自动部署

- `.github/workflows/ci.yml`：在 push 和 pull request 时运行 Python、Node、浏览器引擎和仓库审计测试。
- `.github/workflows/pages.yml`：CI 成功后重新生成 `docs/` 公共产物，并通过 GitHub Pages 发布。
- GitHub 仓库设置中需要将 Pages 的构建方式设为 **GitHub Actions**。

首次发布后，可在仓库的 **Actions** 页面查看 `Verify prototype` 和 `Deploy GitHub Pages demo` 两个工作流；部署完成后，工作流摘要会显示实际站点 URL。

## 关键路径

| 路径 | 用途 |
|---|---|
| `src/torque_guard/` | Python 风险分析、SPC、工作流和集成代码 |
| `data/` | 可复现的演示事件与数据清单 |
| `knowledge/` | PFMEA、控制计划、告警字典、历史案例和关系图谱 |
| `outputs/` | 风险卡、指标和场景评估结果 |
| `scripts/build_all.py` | 重建全部演示产物 |
| `scripts/audit_repository.py` | 检查公共产物和安全边界 |
| `docs/` | 可直接发布的只读 Web 演示 |
| `tests/` | Python 与 Node 测试 |

公开页的 AI 问答默认先尝试本机受控服务；未启动服务时会明确显示浏览器确定性回退。真实模型密钥只由服务端 `.env` 读取，绝不进入 GitHub Pages。

## 本地运行演示

```powershell
python -m http.server 8000 --bind 127.0.0.1 --directory docs
```

浏览器打开 `http://127.0.0.1:8000/`。完整验证命令见 `README.md` 和 `docs/local-development.md`。
