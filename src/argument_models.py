# -*- coding: utf-8 -*-
"""アプリケーションの監視パラメータおよび起動設定を保持するドメインモデルモジュール。

このモジュールは、システム全体で共有される設定情報（GlueJobMonitorConfig）のデータ構造と、
その改ざんを防止するための不変（Immutable）仕様を定義します。
"""

from typing import List, Optional


class GlueJobMonitorConfig(object):
    """AWS Glueジョブ監視エンジンの起動設定を保持する不変（イミュータブル）設定クラス。

    Python 3.6.8環境の制約を考慮し、標準の `dataclasses.dataclass(frozen=True)` を使用せず、
    メタデータ辞書とマジックメソッドのオーバーライドによって同等のデータ構造と完全な不変性を実現しています。
    """

    # 汎用動的パーサー（utils.py）が実行時にリフレクションで読み取るための構成仕様定義メタデータ
    # 構造: "プロパティ名": (ターゲットデータ型, 初期デフォルト値またはNone, CLIヘルプメッセージ文字列)
    _FIELDS_SPEC = {
        "aws_account": (str, None, "Target AWS Account ID (12 digits)"),
        "queue_name": (str, None, "AWS SQS Queue name to poll from"),
        "job_list": (List[str], None, "List of AWS Glue Job names to monitor"),
        "max_execute_minutes": (
            Optional[int],
            60,
            "Max allowable execution time for a job before timeout",
        ),
        "loop_interval_seconds": (
            Optional[int],
            30,
            "Interval seconds between polling loops",
        ),
        "fetch_attempts": (Optional[int], 3, "Max retry attempts to fetch job status"),
        "fallback_retry": (
            Optional[int],
            3,
            "Max retry count when API error or fallback occurs",
        ),
        "fallback_sleep_seconds": (
            Optional[int],
            30,
            "Sleep seconds when API error or fallback occurs",
        ),
    }

    def __init__(self, **kwargs):
        """メタデータ仕様に基づき、完全にクレンジング済みのキーワード引数から不変インスタンス変数を生成。

        Args:
            **kwargs: 汎用動的パーサーによって型変換とバリデーションが完了した設定データのキーワード引数。
        """
        # クラス内で定義されている仕様（_FIELDS_SPEC）のキーのみをループ処理し、
        # 外部からの不要なプロパティの不正な混入（インジェクション）を遮断します。
        for key in self._FIELDS_SPEC:
            # 下記で定義しているカスタム __setattr__ の書き換え制限（AttributeError）をバイパスして、
            # 初期化ステージ（コンストラクタ内）でのみ安全に値を固定するために、
            # 基底クラス（object）の原始的な __setattr__ を直接指名して値をバインドします。
            object.__setattr__(self, key, kwargs.get(key))

    def __setattr__(self, key, value):
        """インスタンス生成後における、プロパティ値のあらゆる動的変更（改ざん）を完全に遮断。

        Value Object（値オブジェクト）としての堅牢性を維持するため、初期化が完了した後に
        `config.max_execute_minutes = 10` のような再代入が行われた場合、例外をスローしてプロセスを守ります。

        Args:
            key (str): 変更が試みられたプロパティ名。
            value (any): 代入されようとした新しい値。

        Raises:
            AttributeError: インスタンスが不変であることを示す例外。
        """
        raise AttributeError("GlueJobMonitorConfig instances are immutable")

    def __repr__(self):
        """Python 3.6.8環境と完全な互換性を持った、デバッグ視認性の高い文字列表現フォーマットを動的生成。

        Returns:
            str: クラス名と保持している全プロパティのキー・値を明示した評価用文字列。
        """
        # メタデータの並び順を維持したまま、安全な文字列フォーマット表現へ整形
        return "GlueJobMonitorConfig({})".format(
            ", ".join("{}={!r}".format(k, getattr(self, k)) for k in self._FIELDS_SPEC)
        )
