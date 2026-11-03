import logging
import sys
from logging.handlers import RotatingFileHandler

# 确保 stdout/stderr 使用 UTF-8 编码，避免 emoji 等字符导致 GBK 编码错误
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 配置日志器（按大小轮转，单文件上限 10MB，保留最近 5 份）
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fhandler = RotatingFileHandler(
    'knowtalk.log',
    maxBytes=10 * 1024 * 1024,   # 10MB
    backupCount=5,               # 保留 knowtalk.log.1 ~ .5
    encoding='utf-8',
)
fhandler.setFormatter(formatter)
fhandler.setLevel(logging.INFO)
logger.addHandler(fhandler)
