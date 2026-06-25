# -*- coding: utf-8 -*-
import json
import subprocess


class SQSClient:
    """AWS CLIを直接実行してAmazon SQSと通信を行うラッパークラス。

    boto3などのSDKが利用できない環境において、
    ログインシェル経由でAWS IAMロールや環境変数を同期しながら
    SQS操作を行うために使用します。
    """

    def _run_aws_cmd(self, cmd_list):
        """ログインシェルを経由して生のAWS CLIコマンドを実行し、結果をJSONでパースする。

        Args:
            cmd_list (list of str): 実行するコマンドとその引数のリスト。

        Returns:
            dict: AWS CLIから返却されたJSONデータをパースした辞書。
                  出力が空の場合は空の辞書を返します。

        Raises:
            RuntimeError: コマンドの実行ステータスが0以外（失敗）だった場合。
        """
        # リスト形式の引数を、シェルに渡すための1つのコマンド文字列に結合
        full_cmd_str = " ".join(cmd_list)

        # デバッグ用：実際にシェルで実行される完全なコマンドを出力
        # print(f'[DEBUG] Executing command: /bin/bash -l -c "{full_cmd_str}"')

        # 手動実行時と同じ環境（iam-role等）を強制同期するため、ログインシェル経由で実行。
        # -l (login) と -c (command) オプションを使用。
        process = subprocess.Popen(
            ["/bin/bash", "-l", "-c", full_cmd_str],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # コマンドの実行完了を待ち、標準出力と標準エラー出力を取得
        stdout_output, stderr_output = process.communicate()

        # コマンドがエラー（戻り値が0以外）で終了した場合は例外をスロー
        if process.returncode != 0:
            raise RuntimeError(
                f"AWS CLI command failed with code {process.returncode}.\n"
                f'Command: /bin/bash -l -c "{full_cmd_str}"\n'
                f"Error: {stderr_output.decode('utf-8').strip()}"
            )

        # 出力結果をデコードし、前後の不要な空白や改行を削除
        decoded_out = stdout_output.decode("utf-8").strip()

        # 出力が空（例：削除コマンドなど成功時に何も返さない場合）は空の辞書を返す
        if not decoded_out:
            return {}

        # 取得した文字列をJSONオブジェクトに変換して返却
        return json.loads(decoded_out)

    def receive_messages(self, queue_url, max_messages=10, wait_seconds=20):
        """対象のSQSキューからメッセージを受信する（ロングポーリング対応）。

        Args:
            queue_url (str): 対象のSQSキューのURL。
            max_messages (int, optional): 一度に取得する最大メッセージ数（1〜10）。デフォルトは10。
            wait_seconds (int, optional): ロングポーリングの待機時間（秒）。デフォルトは20。

        Returns:
            list of dict: 受信したメッセージオブジェクトのリスト。
                          失敗時、またはメッセージが空の場合は空リストを返します。
        """
        # aws sqs receive-message コマンドの引数を構築
        cmd = [
            "/usr/local/bin/aws",  # パスが通っていない環境を考慮し、絶対パスで指定
            "sqs",
            "receive-message",
            "--queue-url",
            f"'{queue_url}'",  # シェルでの変数展開やパースエラーを防ぐためシングルクォートで包む
            "--max-number-of-messages",
            str(max_messages),
            "--wait-time-seconds",
            str(wait_seconds),
            "--output",
            "json",  # 後続のパース処理を確実にするためJSON出力を明示
        ]
        try:
            res = self._run_aws_cmd(cmd)
            # メッセージが存在しない場合は 'Messages' キーがないため、安全に get() を使用
            return res.get("Messages", [])
        except Exception as e:
            # メッセージ受信に失敗しても、システム全体を停止させないよう警告ログを残して続行
            print(f"[WARN] Failed to receive messages: {e}")
            return []

    def delete_message_batch(self, queue_url, entries):
        """複数のSQSメッセージを一括で削除する。

        Args:
            queue_url (str): 対象のSQSキューのURL。
            entries (list of dict): 削除対象のメッセージ情報のリスト。
                                   （Id と ReceiptHandle を含む辞書のリスト）
        """
        # 削除対象のエントリがない場合は何もせず終了
        if not entries:
            return

        # 必須の引数形式に合わせるため、エントリリストをJSON文字列に変換
        entries_json = json.dumps(entries)

        # aws sqs delete-message-batch コマンドの引数を構築
        cmd = [
            "/usr/local/bin/aws",  # 絶対パスに変更
            "sqs",
            "delete-message-batch",
            "--queue-url",
            f"'{queue_url}'",
            "--entries",
            f"'{entries_json}'",  # 複雑なJSON構造を安全にシェルに渡すためシングルクォートで包む
            "--output",
            "json",  # 後続のパース処理を確実にするためJSON出力を明示
        ]
        try:
            self._run_aws_cmd(cmd)
            print(
                f"[INFO] SQS Message batch deleted successfully (Count: {len(entries)})."
            )
        except Exception as e:
            # 削除の失敗はデータ重複の原因になるため、エラー（ERROR）ログとして記録
            print(f"[ERROR] Failed DeleteMessageBatch: {e}")

    def change_message_visibility_batch(self, queue_url, entries):
        """複数のSQSメッセージの可視性タイムアウトを一括で変更（キューに戻す）する。

        Args:
            queue_url (str): 対象のSQSキューのURL。
            entries (list of dict): 変更対象のメッセージ情報のリスト。
                                   （Id, ReceiptHandle, VisibilityTimeout を含む辞書のリスト）
        """
        # 変更対象のエントリがない場合は何もせず終了
        if not entries:
            return

        # 引数用にエントリリストをJSON文字列に変換
        entries_json = json.dumps(entries)

        # aws sqs change-message-visibility-batch コマンドの引数を構築
        cmd = [
            "/usr/local/bin/aws",  # 絶対パスに変更
            "sqs",
            "change-message-visibility-batch",
            "--queue-url",
            f"'{queue_url}'",
            "--entries",
            f"'{entries_json}'",
            "--output",
            "json",  # 後続のパース処理を確実にするためJSON出力を明示
        ]
        try:
            self._run_aws_cmd(cmd)
            print(
                f"[INFO] Unmatched messages released back to queue (Count: {len(entries)})."
            )
        except Exception as e:
            # キューへの即時返却に失敗した場合の調査用ログ
            print(f"[ERROR] Failed ChangeMessageVisibilityBatch: {e}")
