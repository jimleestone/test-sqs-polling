# -*- coding: utf-8 -*-
"""AWS CLIコマンドのサブプロセス実行を制御するSQS専用クライアントモジュール。

このモジュールは、boto3などの高レベルSDKを直接使わず、システムのログインシェル環境を通じて
ネイティブの AWS CLI を実行することで、手動実行時と完全に同一の環境変数やIAMロールの
コンテキストを強制同期してSQSと安全に通信します。
"""

import json
import logging
import subprocess

from argument_models import AppConfig, AppEnv

# %s プレースホルダー規約に準拠した、モジュール専用ロガーの取得
logger = logging.getLogger(__name__)


class SQSClient(object):
    """生のAWS CLIを subprocess 経由で安全にラップして制御するSQS専用通信クラス。

    Python 3.6.8環境と完全な後方互換性を有しています。
    """

    def __init__(self, app_config: AppConfig):
        """環境構成オブジェクトをインジェクションし、開発フラグの状態を同期。

        :param app_config: ロード済みの AppConfig インスタンス
        """
        self.app_config = app_config

        logger.info(
            "SQSClient wrapper successfully attached to active environment: %s",
            app_config.env,
        )

    def _run_aws_cmd(self, cmd_list):
        """引数リストをシェルコマンドに変換し、/bin/bash -l -c を経由して安全に実行。

        【自動環境スイッチ・タイムアウト・ゾンビ防止集約レイヤー】
        1. 開発環境（ENV=dev）が検知された場合、下層コマンド実行時に自動で --profile フラグを動的に注入。
        2. ネットワーク切断による永久ハングを防ぐため、30秒のタイムアウトを課してプロセスフリーズを完全防止。
        3. タイムアウト時は os.kill を実行した後に再度 communicate で死体をOSから安全に回収。

        Args:
            cmd_list (list): 実行するコマンドとその引数が格納された文字列のリスト。

        Returns:
            dict: コマンドの標準出力（JSON形式）をデコードしたディクショナリ。出力が空なら空の辞書。

        Raises:
            RuntimeError: コマンドの戻り値が非0の場合、または実行が30秒を超えてタイムアウトした場合。
        """

        active_cmd = list(cmd_list)
        if self.app_config.env == AppEnv.DEV.value:
            active_cmd.extend(
                [
                    "--profile",
                    self.app_config.aws.dev_profile,
                ]
            )
            logger.debug(
                "Local development mode active. Appended profile flag: --profile %s",
                self.app_config.aws.dev_profile,
            )

        # フラグが完全に統合・確定したリスト形式の引数を、シェル用の1つの文字列に結合
        full_cmd_str = " ".join(active_cmd)

        # 実際にシェルへ引き渡される生の完全なコマンドラインを DEBUG レベルでダンプ
        logger.debug('Executing command: /bin/bash -l -c "%s"', full_cmd_str)

        # 環境変数（IAMロール等）を引き継ぐため、-l (login) と -c (command) オプションでログインシェルを起動
        process = subprocess.Popen(
            ["/bin/bash", "-l", "-c", full_cmd_str],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            # 最大30秒の応答待機制限を課してハングを防止
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

            # 削除の成功時など、出力ペイロード自体が空の場合は空の辞書を返却
            if not decoded_out:
                return {}

            # 文字列を解析してPythonのディクショナリに変換
            return json.loads(decoded_out)

        except subprocess.TimeoutExpired:
            # 30秒の制限を超えた場合、プロセスを即座に殺して常駐メインループのハングを防止
            process.kill()

            # 死体をOSから回収し、リソースリークを防ぐために再度待機なしで呼び出す
            stdout_output, stderr_output = process.communicate()

            logger.error(
                "AWS CLI command execution reached hard timeout limit (30s). Process forcefully killed."
            )
            raise RuntimeError("AWS CLI command timed out after 30 seconds.")

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
            "'{}'".format(queue_url),
            "--max-number-of-messages",
            str(max_messages),
            "--wait-time-seconds",
            str(wait_seconds),
            "--output",
            "json",
        ]
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
            "'{}'".format(entries_json),
            "--output",
            "json",
        ]
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
