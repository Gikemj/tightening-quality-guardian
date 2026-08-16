# 本地部署与 VS Code 调试指南

这份指南面向项目管理员。默认流程只读本地合成数据、生成本地 JSON 并启动静态网页，不需要飞书凭证，也不会调用外部模型或生产系统。

## 1. 第一次准备环境

在 VS Code 中打开项目根目录 `D:\Github\tightening-quality-guardian-main`，右下角选择 Conda 环境 `huawei` 的解释器：

```text
E:\Anaconda24.6\envs\huawei\python.exe
```

新建终端后运行：

```powershell
python -c "import sys; print(sys.executable)"
python --version
python -m pip install -e .
python scripts/check_local_environment.py
```

判断是否进入正确环境不要只看终端前面的 `(huawei)`，以 `sys.executable` 的结果为准。`pip install -e .` 会把 `src/torque_guard` 登记给当前解释器；源码仍留在原目录，修改 `.py` 后不必重新安装。

## 2. 一键验证项目

在 VS Code 按 `Ctrl+Shift+P`，执行 `Tasks: Run Test Task`，默认任务“验证：全部提交门禁”会依次：

1. 重新生成确定性演示数据、风险卡和评测结果；
2. 运行 Python 单元测试；
3. 使用 Node.js 20 或更高版本运行网页逻辑测试；
4. 检查两个 JavaScript 文件语法；
5. 审计公开资源、链接、安全边界和敏感信息。

也可以在终端运行：

```powershell
python scripts/build_all.py
python -m unittest discover -s tests -v
node --test tests/web-engine.test.mjs
node --check docs/risk-engine.js
node --check docs/app.js
python scripts/audit_repository.py
```

## 3. 调试 Python 风险分析

按 `F5`，选择“质控前哨：调试风险分析”。推荐断点位置：

- `src/torque_guard/cli.py`：命令入口、输出与本地预览；
- `src/torque_guard/agent.py`：感知、检测、检索、研判的总流程；
- `src/torque_guard/spc.py`：SPC 规则与统计量；
- `src/torque_guard/risk.py`：输入校验、风险分数与风险卡；
- `src/torque_guard/workflow.py`：审批、任务、验证与结案状态门禁。

修改风险算法后运行 `python scripts/build_all.py`。原因是网页读取 `docs/data/*.json`，不会在浏览器中实时调用 Python；不重建时，网页仍显示上一次生成的结果。

## 4. 打开和调试网页

按 `F5`，选择“质控前哨：本地网页服务”，VS Code 会在服务就绪后打开：

```text
http://127.0.0.1:8000
```

也可以手工运行：

```powershell
python -m http.server 8000 --bind 127.0.0.1 --directory docs
```

网页是静态展示层：Python 负责生成权威结果，JavaScript 负责读取、切换场景和渲染。浏览器按 `F12` 可查看 Console 与 Network；所有数据请求都应指向本机的 `docs/data`。

## 5. 本地管理员权限边界

你作为本地项目管理员可以修改源码、数据生成器、知识文件、页面和调试配置。当前项目不是带账号系统的生产后台，因此不存在网页里的“超级管理员登录”或真实企业人员权限；安全边界由以下方式保证：

- 默认 `preview` 模式只生成本地文件；
- 人工审批状态机阻止越级创建任务或结案；
- 飞书 `live` 路径默认关闭，缺少完整配置和匹配的审批记录时失败关闭；若显式联调，包含人员/远端 ID 的本地副本默认写入已忽略的 `.local/live/`，不得复制到公开产物目录；
- 项目没有 PLC 写入、停线或自动修改设备参数的能力；
- `.env` 已被 Git 忽略，真实密钥不得写入源码、JSON、截图或提交记录。

## 6. 常见问题

### VS Code 右下角选了 huawei，但终端仍是 Python 3.8

关闭旧终端并新建终端，再检查 `sys.executable`。仍不正确时执行：

```powershell
conda activate huawei
python -c "import sys; print(sys.executable)"
```

若激活提示异常，先在 Anaconda Prompt 运行 `conda init powershell`，完全关闭并重新打开 VS Code。

### 终端前面没有 `(huawei)`

括号只是提示符显示，不是最终证据。只要 `sys.executable` 指向 `E:\Anaconda24.6\envs\huawei\python.exe`，当前命令使用的就是该环境。

### 修改 Python 后网页没有变化

先运行 `python scripts/build_all.py`，再在浏览器按 `Ctrl+F5` 强制刷新。

### 8000 端口被占用

改用其他端口，例如：

```powershell
python -m http.server 8010 --bind 127.0.0.1 --directory docs
```

然后打开 `http://127.0.0.1:8010`。
