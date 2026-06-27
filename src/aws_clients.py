# -*- coding: utf-8 -*-
"""AWS CLIコマンドのサブプロセス実行を制御するSQS専用クライアントモジュール。

このモジュールは、boto3などの高レベルSDKを直接使わず、システムのログインシェル環境を通じて
ネイティブの AWS CLI を実行することで、手動実行時と完全に同一の環境変数やIAMロールの
コンテキストを強制同期してSQSと安全に通信します。
"""

import json
import logging
import os
import subprocess

# %s プレースホルダー規約に準拠した、モジュール専用ロガーの取得
logger = logging.getLogger(__name__)


class SQSClient(object):
    """生のAWS CLIを subprocess 経由で安全にラップして制御するSQS専用通信クラス。

    Python 3.6.8環境と完全な後方互換性を有しています。
    """

    # 開発（ローカルテスト）環境時にAWS CLIへ追加注入する名前付き認証プロファイル名
    DEV_PROFILE = "local"

    def __init__(self):
        """環境変数 'ENV' の状態をインスペクションし、開発モードのフラグを設定します。"""
        env_val = os.environ.get("ENV", "prod")
        self.is_dev = env_val == "dev"

        # 起動時の環境識別をログに明示
        logger.info(
            "SQSClient initialized. Current execution environment active: %s", env_val
        )

    def _run_aws_cmd(self, cmd_list):
        """引数リストをシェルコマンドに変換し、/bin/bash -l -c を経由して安全に実行。

        【ゾンビ化・フリーズ防止対策】
        1. ネットワーク切断やAWS側の応答ハングによるプロセス永久フリーズを防ぐため、30秒のタイムアウトを設定。
        2. タイムアウト検知時は、OSレベルで `process.kill()` を発行して子プロセスを即座に強制終了（バグフリーズの防止）。
        3. kill 後に再度 `communicate()` を呼び出すことで、ゾンビプロセスの発生を防止し、残存バッファを完全に吸い出す。

        Args:
            cmd_list (list): 実行するコマンドとその引数が格納された文字列のリスト。

        Returns:
            dict: コマンドの標準出力（JSON形式）をデコードしたディクショナリ。出力が空なら空の辞書。

        Raises:
            RuntimeError: コマンドの戻り値が非0の場合、または実行が30秒を超えてタイムアウトした場合。
        """
        # リスト形式の引数を、シェルに渡すための1つのコマンド文字列に結合
        full_cmd_str = " ".join(cmd_list)

        # 実際にシェルへ引き渡される生の完全なコマンドラインを DEBUG レベルでダンプ
        logger.debug('Executing command: /bin/bash -l -c "%s"', full_cmd_str)

        # 環境変数（IAMロール等）を引き継ぐため、-l (login) と -c (command) オプションでログインシェルを起動
        process = subprocess.Popen(
            ["/bin/bash", "-l", "-c", full_cmd_str],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            # 【重要】最大30秒の応答待機制限を課してフリーズを防止
            stdout_output, stderr_output = process.communicate(timeout=30)

            # AWS CLI コマンドの終了ステータスが0以外（異常終了）の場合は即座に例外をスロー
            if process.returncode != 0:
                raise RuntimeError(
                    'AWS CLI command failed with code {}.\nCommand: /bin/bash -l -c "{}"\nError: {}'.format(
                        process.returncode,
                        full_cmd_str,
                        stderr_output.decode("utf-8").strip(),
                    )
                )

            # 標準出力をデコードし、前後の不要な空白や改行をクレンジング
            decoded_out = stdout_output.decode("utf-8").strip()

            # 削除の成功時など、出力ペイロード自体が空（None/空文字）の場合は空の辞書を返却
            if not decoded_out:
                return {}

            # 文字列を解析してPythonのディクショナリに変換
            return json.loads(decoded_out)

        except subprocess.TimeoutExpired:
            # 30秒の制限を超えた場合、プロセスを即座に殺して常駐メインループのハングを防止
            process.kill()

            # 【ゾンビ化防止の鉄則】死体をOSから回収し、リソースリークを防ぐために再度待機なしで呼び出す
            stdout_output, stderr_output = process.communicate()

            logger.error(
                "AWS CLI command execution reached hard timeout limit (30s). Process forcefully killed."
            )
            raise RuntimeError("AWS CLI command timed out after 30 seconds.")

    def _switch_to_dev(self, cmd):
        """開発環境（ENV=dev）が検知された場合のみ、認証プロファイルフラグをコマンド末尾にインジェクション。

        Args:
            cmd (list): 構築中のAWS CLI引数コマンドリスト（破壊的変更により末尾を拡張）。
        """
        if self.is_dev:
            # メモリ効率を考慮し、引数リストの末尾に --profile local を直接拡張
            cmd.extend(
                [
                    "--profile",
                    self.DEV_PROFILE,
                ]
            )
            logger.debug(
                "Local development mode config matched. Appended profiling flag: --profile %s",
                self.DEV_PROFILE,
            )

    def receive_messages(self, queue_url, max_messages=10, wait_seconds=20):
        """aws sqs receive-message を実行し、キューに到着している最新メッセージのリストを同期取得。

        Args:
            queue_url (str): ターゲットとなるAWS SQSの完全なQueue URL。
            max_messages (int, optional): 1回の取得でメモリに引き込む最大メッセージ数（上限10）。
            wait_seconds (int, optional): ロングポーリングの待機秒数（最大20秒）。

        Returns:
            list: 受信したSQSメッセージディクショナリのリスト。メッセージ不在、または失敗時は空のリスト。
        """
        # AWS CLI の引数構造を安全に組み立て
        cmd = [
            "/usr/local/bin/aws",
            "sqs",
            "receive-message",
            "--queue-url",
            "'{}'".format(
                queue_url
            ),  # シェルの変数展開や特殊文字によるパースエラーを防ぐため一律シングルクォートで包む
            "--max-number-of-messages",
            str(max_messages),
            "--wait-time-seconds",
            str(wait_seconds),
            "--output",
            "json",  # 戻り値の型変換（json.loads）を確実にするため、CLI出力フォーマットに json を明示
        ]

        # 開発フラグのインジェクションを試行
        self._switch_to_dev(cmd)

        try:
            res = self._run_aws_cmd(cmd)
            # メッセージが0件の時は 'Messages' キー自体が返らないため、安全に .get() でフォールバック
            return res.get("Messages", [])
        except Exception as e:
            # メッセージ受信の局所的失敗は、一時的なネットワークエラーの可能性を考慮し、システムを止めずにwarningで記録
            logger.warning("Failed to receive messages from SQS destination: %s", e)
            return []

    def delete_message_batch(self, queue_url, entries):
        """aws sqs delete-message-batch を実行し、処理が完了したメッセージ群をキューから完全削除。

        Args:
            queue_url (str): ターゲットとなるAWS SQSの完全なQueue URL。
            entries (list): 削除対象の 'Id' と 'ReceiptHandle' を含む辞書のリスト（最大10件）。
        """
        # 削除対象のメッセージが空（0件）の場合は、APIコールを発生させずにアーリーリターン
        if not entries:
            return

        # 複雑な入れ子JSON構造（バッチエントリ仕様）を引数に載せるため、一度文字列へシリアライズ
        entries_json = json.dumps(entries)

        cmd = [
            "/usr/local/bin/aws",
            "sqs",
            "delete-message-batch",
            "--queue-url",
            "'{}'".format(queue_url),
            "--entries",
            "'{}'".format(
                entries_json
            ),  # 複雑なJSON構文内のダブルクォートをシェルが誤認するのを防ぐため、外側をシングルクォートで保護
            "--output",
            "json",
        ]
        self._switch_to_dev(cmd)

        try:
            self._run_aws_cmd(cmd)
            logger.info(
                "SQS Message batch deleted successfully (Count: %s).", len(entries)
            )
        except Exception as e:
            # 削除の失敗は、メッセージが再びキューに見えてしまう「データの二重処理」の原因になるため、
            # トレースバックを含めて厳格に記録すべき重要障害として exception で記録
            logger.exception("Failed DeleteMessageBatch execution for entries: %s", e)

    def change_message_visibility_batch(self, queue_url, entries):
        """aws sqs change-message-visibility-batch を実行し、対象外メッセージを即座にキューへ解放。

        Args:
            queue_url (str): ターゲットとなるAWS SQSの完全なQueue URL。
            entries (list): 可視性を変更する（VisibilityTimeout=0）メッセージエントリのリスト（最大10件）。
        """
        # 変更対象のメッセージが空の場合は無駄なコマンド実行を遮断してアーリーリターン
        if not entries:
            return

        entries_json = json.dumps(entries)

        cmd = [
            "/usr/local/bin/aws",
            "sqs",
            "change-message-visibility-batch",
            "--queue-url",
            "'{}'".format(queue_url),
            "--entries",
            "'{}'".format(entries_json),
            "--output",
            "json",
        ]
        self._switch_to_dev(cmd)

        try:
            self._run_aws_cmd(cmd)
            logger.info(
                "Unmatched messages released back to queue (Count: %s).", len(entries)
            )
        except Exception as e:
            # 可視性タイムアウトの変更失敗は、メッセージが一定時間キューに眠ってしまい処理遅延を招く原因になるため、
            # 追跡調査用のトレースバック付き exception で記録
            logger.exception(
                "Failed ChangeMessageVisibilityBatch execution for entries: %s", e
            )
