# Contributing

This repository is a competition prototype. Changes should keep the demo reproducible and preserve the distinction between synthetic evidence and real factory data.

Before opening a pull request:

```bash
make all
```

On Windows or without Make, use the active project interpreter and then run the same submission gates:

```powershell
python -m pip install -e .
python scripts/build_all.py
python -m unittest discover -s tests -v
node --test tests/web-engine.test.mjs
node --check docs/risk-engine.js
node --check docs/app.js
python scripts/audit_repository.py
```

CI 还会在重新构建后执行 `git diff --exit-code -- data docs/data outputs`，确保受版本控制的生成产物没有遗漏更新且可重复生成。

Do not commit credentials, real VINs, employee data, factory documents, or unlicensed proprietary datasets. New risk rules require a test case, a `risk_policy_version` update, regenerated deterministic artifacts, and a short explanation in `docs/method.md`.
