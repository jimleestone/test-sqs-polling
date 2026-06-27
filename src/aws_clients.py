# -*- coding: utf-8 -*-
import os
import json
import logging
import subprocess

# %s プレースホルダー規約に準拠した、モジュール専用ロガーの取得
logger = logging.getLogger(__name__)


class SQSClient(object):
    """AWS CLIコマンドを生のサブプロセス(ログインシェル)経由で実行し、

    SQSキューと安全に通信を行うための専用クライアントクラス。
    Python 3.6.8環境と完全な互換性を持たせています。
    """

    DEV_PROFILE = "local"

    def __init__(self):
        env_val = os.environ.get("ENV", "prod")
        self.is_dev = env_val == "dev"

    def _run_aws_cmd(self, cmd_list):
        """リスト形式の引数を結合し、/bin/bash -l -c を経由して実行します。

        :param cmd_list: コマンドと引数のリスト
        :return: コマンドの標準出力（JSONデコード後のディクショナリ）
        """
        # リスト形式の引数を、シェルに渡すための1つのコマンド文字列に結合
        full_cmd_str = " ".join(cmd_list)

        # デバッグ用：実際にシェルで実行される完全なコマンドを DEBUG レベルで出力
        logger.debug('Executing command: /bin/bash -l -c "%s"', full_cmd_str)

        # 手動実行時と同じ環境（iam-role等）を強制同期するため、ログインシェル経由で実行。
        process = subprocess.Popen(
            ["/bin/bash", "-l", "-c", full_cmd_str],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            # コマンドの実行完了を待ち、標準出力と標準エラー出力を取得
            # Python 3.6以降対応のタイムアウト制御）
            stdout_output, stderr_output = process.communicate(timeout=30)
            # コマンドがエラー（戻り値が0以外）で終了した場合は例外をスロー
            if process.returncode != 0:
                raise RuntimeError(
                    'AWS CLI command failed with code {}.\nCommand: /bin/bash -l -c "{}"\nError: {}'.format(
                        process.returncode,
                        full_cmd_str,
                        stderr_output.decode("utf-8").strip(),
                    )
                )

            # 出力結果をデコードし、前後の不要な空白や改行を削除
            decoded_out = stdout_output.decode("utf-8").strip()

            # 出力が空（例：削除コマンドなど成功時に何も返さない場合）は空の辞書を返す
            if not decoded_out:
                return {}

            # 取得した文字列をJSONオブジェクトに変換して返却
            return json.loads(decoded_out)
        except subprocess.TimeoutExpired:
            process.kill()  # ゾンビ化を防ぐためプロセスを強制終了
            stdout_output, stderr_output = process.communicate()
            raise RuntimeError("AWS CLI command timed out after 30 seconds.")

    def _switch_to_dev(self, cmd: list):
        if self.is_dev:
            cmd.extend(
                [
                    "--profile",
                    self.DEV_PROFILE,
                ]
            )

    def receive_messages(self, queue_url, max_messages=10, wait_seconds=20):
        """aws sqs receive-message を実行し、メッセージのリストを取得します。"""
        # aws sqs receive-message コマンドの引数を構築 (F-stringを.formatに修正)
        cmd = [
            "/usr/local/bin/aws",  # パスが通っていない環境を考慮し、絶対パスで指定
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
        self._switch_to_dev(cmd)
        try:
            res = self._run_aws_cmd(cmd)
            # メッセージが存在しない場合は 'Messages' キーがないため、安全に get() を使用
            return res.get("Messages", [])
        except Exception as e:
            # メッセージ受信に失敗しても、システム全体を停止させないよう警告ログを残して続行
            logger.warning("Failed to receive messages: %s", e)
            return []

    def delete_message_batch(self, queue_url, entries):
        """指定されたエントリのバッチメッセージをキューから完全に削除します。"""
        # 削除対象のエントリがない場合は何もせず終了
        if not entries:
            return

        # 必須の引数形式に合わせるため、エントリリストをJSON文字列に変換
        entries_json = json.dumps(entries)

        # aws sqs delete-message-batch コマンドの引数を構築
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
        self._switch_to_dev(cmd)
        try:
            self._run_aws_cmd(cmd)
            logger.info(
                "SQS Message batch deleted successfully (Count: %s).", len(entries)
            )
        except Exception as e:
            # 削除の失敗はデータ重複の原因になるため、エラー（ERROR）ログとして例外トレースを含めて記録
            logger.exception("Failed DeleteMessageBatch: %s", e)

    def change_message_visibility_batch(self, queue_url, entries):
        """一致しなかったメッセージの可視性タイムアウトを変更し、即座にキューへ返却します。"""
        # 変更対象のエントリがない場合は何もせず終了
        if not entries:
            return

        # 引数用にエントリリストをJSON文字列に変換
        entries_json = json.dumps(entries)

        # aws sqs change-message-visibility-batch コマンドの引数を構築
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
            # キューへの即時返却に失敗した場合の調査用ログ
            logger.exception("Failed ChangeMessageVisibilityBatch: %s", e)
