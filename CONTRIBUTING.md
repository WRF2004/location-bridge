# Contributing

欢迎提交问题和改进。提交设备兼容性问题时，请提供 macOS、iOS、Python 和工具版本、连接方式，以及去除敏感信息后的错误日志。

不要提交控制页链接、二维码、设备 UDID、配对文件、个人地点或访问令牌。

运行不接触真实设备的后端测试：

```sh
python3 -m unittest -v test_bridge.py
```

前端端到端流程使用 JSDOM、真实本地 HTTP 后台和假设备 CLI：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install segno==1.6.6
npm install --no-save --package-lock=false jsdom@26
TEST_PYTHON="$PWD/.venv/bin/python" node test_ui.cjs
```

真实设备操作与本地测试分开验证。修复时请说明触发条件、修改后的行为与验证范围；不要把假设备测试写成真机兼容性保证。
