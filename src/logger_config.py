# -*- coding: utf-8 -*-
"""アプリケーション全体のロギング基盤を一括制御・初期化する構成モジュール。

このモジュールは、CLI引数の解析ステージが始まるよりも前の「完全な起動直後」に
環境変数をインスペクションし、標準出力およびファイルローテーションハンドラーの
ログレベルを一元的に確立します。
"""

import logging
from logging.handlers import RotatingFileHandler
import os


def setup_logging():
    """アプリケーション全体のロギング設定を一括初期化。

    【ブートストラップ・運用設計規約】
    1. CLI引数のパースエラーログを確実に捕捉するため、環境変数 'LOG_LEVEL' から起動直後にレベルを動的決定。
    2. コンソール（標準出力）とローテーション仕様のログファイルの両方に、同一フォーマットで多重出力。
    3. 本番運用時のディスク逼迫を防ぐため、1ファイル最大10MB、最大10世代（計100MB）のローテーションを実施。
    4. 大量のデバッグログを発生させるサードパーティ（boto3等）のログレベルを適切に制限し、ノイズをシャットアウト。
    """
    log_dir = "logs"
    # ログ出力先ディレクトリが存在しない場合は、書き込みエラーを防ぐため自動生成
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # 共通のログフォーマット（時間、レベル、ロガー名、メッセージ）を定義
    log_format = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # argparse のパースステージより前に評価するため、環境変数からログレベルを取得（未指定時は標準の INFO）
    env_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    debug_mode = env_level == "DEBUG"

    # システム全体（Root Logger）に適用するアクティブなログレベルの決定
    active_level = logging.DEBUG if debug_mode else logging.INFO

    root_logger = logging.getLogger()
    root_logger.setLevel(active_level)

    # プログラムの二重起動や、モジュールの再読み込み時に同一ハンドラーが重複登録されて
    # ログメッセージが画面に2重、3重に出力されてしまう不具合を防止するためのクリア処理
    if root_logger.handlers:
        root_logger.handlers = []

    # -------------------------------------------------------------------------
    # [ハンドラー1: 標準出力（コンソール）の設定]
    # -------------------------------------------------------------------------
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_format)
    console_handler.setLevel(active_level)
    root_logger.addHandler(console_handler)

    # -------------------------------------------------------------------------
    # [ハンドラー2: ファイル出力（ローテーション仕様）の設定]
    # -------------------------------------------------------------------------
    # maxBytes: 10 * 1024 * 1024 (10MBに達した時点で自動ローテーション)
    # backupCount: 10 (monitor.log.1 から monitor.log.10 まで最大10世代の過去ログを維持)
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "monitor.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(log_format)
    file_handler.setLevel(active_level)
    root_logger.addHandler(file_handler)

    # -------------------------------------------------------------------------
    # [サードパーティ製ライブラリのノイズログ制限制御]
    # -------------------------------------------------------------------------
    # boto3 や urllib3 は内部のコンポーネントで大量のHTTP通信ログ（Connection pool等）を発生させます。
    # 自前のアプリケーションログが埋もれてしまうのを防ぐため、明示的にレベルを制限します。
    # - DEBUGモード時は、重要な接続イベントを追えるように INFO レベルまで透過。
    # - 通常運用（INFO）時は、警告以上のみを出力させるため WARNING レベルに設定。
    third_party_level = logging.INFO if debug_mode else logging.WARNING
    logging.getLogger("boto3").setLevel(third_party_level)
    logging.getLogger("botocore").setLevel(third_party_level)
    logging.getLogger("urllib3").setLevel(third_party_level)
