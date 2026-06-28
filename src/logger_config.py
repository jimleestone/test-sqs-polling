# src/logger_config.py
# -*- coding: utf-8 -*-
"""アプリケーション全体のロギング基盤を一括初期化・制御する構成モジュール。

このモジュールは、CLI引数の解析ステージが始まるよりも前の「完全な起動直後」に
ロードされた AppConfig の多重ネストプロパティをチェーン参照し、
標準出力およびファイルローテーションハンドラーのログレベルとインフラ構成を一元的に確立します。
"""

import logging
from logging.handlers import RotatingFileHandler
import os

from argument_models import AppConfig


def setup_logging(app_config: AppConfig):
    """AppConfigのネストプロパティを直接チェーン参照し、システム全体のロギングを完全初期化。

    【ブートストラップ・運用設計規約】
    1. CLI引数のパースエラーログを確実に捕捉するため、環境変数からロードされた構成情報を最優先で適用。
    2. コンソール（標準出力）とローテーション仕様のログファイルの両方に、同一フォーマットで多重出力。
    3. 本番運用時のディスク逼迫を防ぐため、app_config に定義された最大MBサイズとバックアップ世代数でローテーション。
    4. 大量のデバッグログを発生させるサードパーティ（boto3等）のログレベルを適切に制限し、自前のログの視認性を確保。

    Args:
        app_config (AppConfig): 環境変数から最速でロードが完了したネスト構造付き環境構成インスタンス。
    """
    # 1. 多重ネストプロパティから出力先ディレクトリパスを展開（app_config.log.dir）
    log_dir = app_config.log.dir

    # ログ出力先ディレクトリが存在しない場合は、書き込みエラーを防ぐため自動生成
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # 共通のログフォーマット（時間、レベル、ロガー名、メッセージ）を定義
    log_format = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 2. ネストプロパティからログレベル（app_config.log.level）を取得して動的判定
    debug_mode = app_config.log.level == "DEBUG"

    # アプリケーション全体（Root Logger）に適用するアクティブなログレベルの決定
    active_level = logging.DEBUG if debug_mode else logging.INFO

    root_logger = logging.getLogger()
    root_logger.setLevel(active_level)

    # プログラムの二重起動やモジュールの再読み込み時に、同一ハンドラーが重複登録されて
    # ログメッセージがコンソールやファイルに2重・3重に出力されてしまう不具合を防止するためのクリア処理
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
    # ファイル名、MBサイズ、世代数をすべてネストプロパティから同期展開
    full_log_path = os.path.join(log_dir, app_config.log.file_name)

    # 単位をメガバイトからバイト単位へ内部クレンジング (max_size_mb * 1024 * 1024)
    max_bytes = app_config.log.max_size_mb * 1024 * 1024

    file_handler = RotatingFileHandler(
        full_log_path,
        maxBytes=max_bytes,
        backupCount=app_config.log.backup_count,
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
