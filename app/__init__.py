"""统一日志配置。放在包入口保证先于所有子模块生效（auth 在 import 时就可能告警）。

uvicorn 只配置它自己的 logger，应用侧 logger 需要 root handler 才有输出；
级别用 LOG_LEVEL 环境变量调整（默认 INFO）。
"""

import logging
import os

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
