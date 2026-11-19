# 设备维保资料的适配收集与离线交接

这是尚待建立业务工具的本地项目。已提供合成来源站点、原件及业务契约，未实现用户要求的采集和自动办理。先阅读 docs/来源契约.md 与 docs/接口与运行.md。

Python3.11，来源启动命令：

    python source_server.py --port 8765

打开 http://127.0.0.1:8765/ 。浏览器运行依赖提前固定并预装，来源本身仅使用标准库，无外部网站或账户。依赖和验证命令：

    python -m pip install -r requirements.txt
    python -m playwright install chromium
    python -m unittest discover -s tests -v
    python -m compileall -q source_server.py tests

测试只针对来源服务，不代表新业务工具完成。所有资料均为虚构，不可用于实际设备控制、支付或门禁。
