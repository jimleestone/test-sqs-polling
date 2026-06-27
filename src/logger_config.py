# -*- coding: utf-8 -*-
import logging
from logging.handlers import RotatingFileHandler
import os


def setup_logging():
    """アプリケーション全体のロギング設定を一括初期化します。

    コンソール出力と、ローテーション機能付きファイル出力（logs/monitor.log）の
    両方に同じフォーマットで出力します。
    """
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # 共通のログフォーマット
    log_format = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 環境変数からログレベルを取得（指定がなければ一律 INFO）
    env_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    debug_mode = env_level == "DEBUG"

    active_level = logging.DEBUG if debug_mode else logging.INFO

    root_logger = logging.getLogger()
    root_logger.setLevel(active_level)

    # 重複出力を防止するため既存のハンドラーを初期化
    if root_logger.handlers:
        root_logger.handlers = []

    # ハンドラー1: 標準出力（コンソール）
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_format)
    console_handler.setLevel(active_level)
    root_logger.addHandler(console_handler)

    # ハンドラー2: ファイル出力（最大10MB、最大5世代までバックアップ）
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "monitor.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(log_format)
    file_handler.setLevel(active_level)
    root_logger.addHandler(file_handler)

    # サードパーティ製ライブラリの過剰なノイズログを制限
    third_party_level = logging.INFO if debug_mode else logging.WARNING
    logging.getLogger("boto3").setLevel(third_party_level)
    logging.getLogger("botocore").setLevel(third_party_level)
    logging.getLogger("urllib3").setLevel(third_party_level)
