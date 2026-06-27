# AWS Glue Job 常駐監視システム - 単体テスト仕様書

## Ⅰ. テスト実行環境・前提条件

* ランタイム: Python 3.6.8 以上（またはお使いの Python 3.14.3）
* 必須ライブラリ: pytest
* 環境変数定義:
* ENV=dev または ENV=prod
  * LOG_LEVEL=INFO または LOG_LEVEL=DEBUG

------------------------------

## Ⅱ. テストケース一覧

### 【ケース1】 必須文字列引数の空文字侵入ブロック（src/utils.py）

#### 1. テスト目的

CLI から --aws-account "" や --queue-name " " などの空文字（トリム後空文字を含む）が渡された際、汎用動的パーサーが第2ステージで確実に検知し、オブジェクトを生成させずに適切なエラーを出して安全に終了することの検証。

#### 2. 前提条件・用意すべきもの

* テスト対象ファイル: src/utils.py, src/argument_models.py
* モック引数: ["--aws-account", " ", "--queue-name", "test-queue", "--job-list", "job-1"]

#### 3. テスト手順

   1. 環境変数 LOG_LEVEL=INFO を設定してテストスクリプトを起動。
   2. 上記の引数リストを parse_args_for(GlueJobMonitorConfig, args_list) に引き渡して実行。
   3. argparse.ArgumentParser.error に起因する SystemExit 例外をキャッチする。

#### 4. 想定結果

* SystemExit 例外が発生し、終了ステータスコードが 2（または非0）であること。
* オブジェクト（GlueJobMonitorConfig）は生成されないこと。

#### 5. 確認ポイント・説明

* logs/monitor.log に [ERROR] [utils] Validation failed: required parameter --aws-account is blank. というエラーログがプレースホルダー形式で正しく刻まれているか。
* parser.error の仕組みを通じて、Usage（使い方）が画面に提示されているか。

------------------------------

### 【ケース2】 任意整数引数の空文字フォールバック（src/utils.py）

#### 1. テスト目的

オプションの整数引数（--loop-interval-seconds "" など）にシェルから空文字やスペースのみが指定された際、エラーにせずデータクラス側の初期デフォルト整数値（30）へ自動補完・キャストされることの検証。

#### 2. 前提条件・用意すべきもの

* テスト対象ファイル: src/utils.py, src/argument_models.py
* モック引数: ["--aws-account", "123456789012", "--queue-name", "test-queue", "--job-list", "job-1", "--loop-interval-seconds", " "]

#### 3. テスト手順

   1. 引数リストを parse_args_for に引き渡して実行する。
   2. 返却された config オブジェクトを取得する。

#### 4. 想定結果

* 例外を出さずに正常終了する。
* 取得されたオブジェクトのプロパティ config.loop_interval_seconds の値が 30 であり、かつ型が int であること。

#### 5. 確認ポイント・説明

* パース完了後の第2ステージにおいて、meta["actual_type"] is int の条件を通過し、文字列の "" から meta["default_value"]（整数の30）への差し替えが正常に行われているか。

------------------------------

### 【ケース3】 オプション引数の不正文字列に対するパースエラー（src/utils.py）

#### 1. テスト目的

オプションの整数パラメータに対して、空文字ではなく "aaa" や "ddd" などの数値変換不可能な不正文字列が渡された際、デフォルト値に逃げずに厳格にパースエラー（cannot parse）として処理を中断することの検証。

#### 2. 前提条件・用意すべきもの

* テスト対象ファイル: src/utils.py
* モック引数: ["--aws-account", "123456789012", "--queue-name", "test-queue", "--job-list", "job-1", "--fetch-attempts", "ddd"]

#### 3. テスト手順

   1. 引数リストを parse_args_for に引き渡して実行。
   2. SystemExit 例外の発生を確認する。

#### 4. 想定結果

* SystemExit 例外が発生すること。
* エラーメッセージに cannot parse 'ddd' as an integer value. が含まれていること。

#### 5. 確認ポイント・説明

* 前回のバグ（型展開 _extract_item_type の再帰処理ミス）が完全に修正され、Optional[int] が正確に int と見なされて try...except ValueError のバリデーショントラップで捕獲されているか。

------------------------------

### 【ケース4】 構成仕様（_FIELDS_SPEC）のセキュリティ・デバッグレベル制御（src/monitor.py）

#### 1. テスト目的

monitor.py 起動時の構成データ型解析ログが、本番のノイズ削減とアカウントID等の機密情報隠蔽のため、INFO ではなく DEBUG レベルの時にのみ出力されることの検証。

#### 2. 前提条件・用意すべきもの

* テスト対象ファイル: src/monitor.py, src/logger_config.py
* パターン4-1: 環境変数 LOG_LEVEL=INFO
* パターン4-2: 環境変数 LOG_LEVEL=DEBUG

#### 3. テスト手順

   1. パターン4-1 でスクリプトを起動し、ログファイル（logs/monitor.log）の中身をスキャンする。
   2. パターン4-2 でスクリプトを起動し、同様にログファイルの中身をスキャンする。

#### 4. 想定結果

* パターン4-1 (INFO): === [CONFIG DATA PROPERTIES AND TYPE ANALYSIS] === のブロックがログに出力されていないこと。
* パターン4-2 (DEBUG): 同ブロックおよび各プロパティの型、値の詳細がログに出力されていること。

#### 5. 確認ポイント・説明

* monitor.py 側で該当のループ出力に logger.debug が正しく選定されているか。
* logger_config.py 側の os.environ.get("LOG_LEVEL") による動的レベル変更が、 Root Logger に正しく波及しているか。

------------------------------

### 【ケース5】 AWS CLI サブプロセスのハードタイムアウトとゾンビ防止（src/aws_clients.py）

#### 1. テスト目的

ネットワーク断線等で AWS CLI（生のサブプロセス）が永久に応答ハングアップした際、30秒で強制終了（kill）され、かつOSに死体（ゾンビ）を残さずにエラー終了することの検証。

#### 2. 前提条件・用意すべきもの

* テスト対象ファイル: src/aws_clients.py
* モック処理: subprocess.Popen をパッチ（Mock）し、communicate が呼び出された時に subprocess.TimeoutExpired を意図的に発生させるように設定。

#### 3. テスト手順

   1. SQSClient().receive_messages("mock_url") を呼び出す。
   2. RuntimeError が発生することを確認する。

#### 4. 想定結果

* 呼び出しから即座に（または設定タイムアウト後に） RuntimeError("AWS CLI command timed out after 30 seconds.") が送出されること。

#### 5. 確認ポイント・説明

* except subprocess.TimeoutExpired: ブロックの内部で、process.kill() が確実に実行されているか。
* kill の直後に2回目の process.communicate() が実行され、OSからプロセスの終了ステータスが正常に回収（ゾンビ化防止）されているか。

------------------------------

### 【ケース6】 コスト最適化ロングポーリングの連鎖ブレイク（src/monitor_base.py）

#### 1. テスト目的

wait_seconds=20 のロングポーリング実行時、キューにメッセージが0件（空）であった場合、無駄な連続リクエスト（fetch_attempts）を発生させずに即座にループを抜け、インターバル待機へ移行することの検証。

#### 2. 前提条件・用意すべきもの

* テスト対象ファイル: src/monitor_base.py
* モック処理: SQSClient.receive_messages が空配列 [] を返すようにスタブ（Mock）化。
* 構成仕様: fetch_attempts=3 に設定。

#### 3. テスト手順

   1. SQSMonitorEngine._bulk_fetch_messages() を実行する。
   2. receive_messages が呼び出される「回数」を追跡する。

#### 4. 想定結果

* receive_messages の呼び出し回数が、上限の3回ではなく、1回だけで終了すること（break の正常動作）。

#### 5. 確認ポイント・説明

* メッセージが空の際、無駄に20秒×3回＝60秒もスレッドをロックさせず、1回（20秒のホールド）の時点で即座に諦めて、全体のAWSリクエスト課金を最適化するロジック（else: break）が正しく機能しているか。

------------------------------

### 【ケース7】 分散メッセージ順序逆転防止の時系列ガード（src/monitor_base.py）

#### 1. テスト目的

SQSの特性によって、古い時刻のイベント（12:01のRUNNING）が、新しい時刻の終端イベント（12:05のFAILED）よりも後に遅れて届いてパースされた際、過去の状態で最終判定が上書きされるバグ（誤検知）を確実に防いでいることの検証。

#### 2. 前提条件・用意すべきもの

* テスト対象ファイル: src/monitor_base.py, src/monitor.py
* モックメッセージ配列:

1. 先にパースされる要素: {"time": "2026-06-27T12:05:00Z", "detail": {"jobName": "job-1", "state": "FAILED"}}
   2. 後からパースされる要素: {"time": "2026-06-27T12:01:00Z", "detail": {"jobName": "job-1", "state": "RUNNING"}}

#### 3. テスト手順

   1. 上記の逆転メッセージ配列を SQSMonitorEngine.run のシミュレーションループに流し込む。
   2. 最終的な終了フラグ（should_terminate）および is_failed の状態を確認する。

#### 4. 想定結果

* 2番目の古いメッセージを読み込んでも、should_terminate は True のまま維持されること。
* 最終ステータスが RUNNING に上書きされず、FAILED（is_failed = True）として正しく終了判定が確定すること。

#### 5. 確認ポイント・説明

* メインループ内の if is_trigger and event_time > latest_trigger_time: という文字列ベースの時系列比較ガードが正確に働き、過去のイベントの処理を安全に無視できているか。

------------------------------

### 【ケース8】 終了直前の Graceful RELEASE 同期コミット（src/monitor_base.py）

#### 1. テスト目的

自分が終了（Terminate）するまさにその直前に、バルクで一緒に掴んでしまっていた「他人の（監視対象外の）メッセージ」を、可視性タイムアウト0（VisibilityTimeout=0）にして即座にキューへ解放（RELEASE）できていることの検証。

#### 2. 前提条件・用意すべきもの

* テスト対象ファイル: src/monitor_base.py
* モックメッセージ配列: 自分の終了メッセージ 1件 ＋ 他人（対象外）のメッセージ 1件。

#### 3. テスト手順

   1. メインループに上記2件を流し、終了シーケンスを発火させる。
   2. _process_in_chunks が呼び出される内訳を追跡する。

#### 4. 想定結果

* 自分のメッセージに対して action_type="DELETE" が1件実行されること。
* 他人のメッセージに対して action_type="RELEASE" が1件実行されること。
* その後、sys.exit(0) または sys.exit(1) へ流れること。

#### 5. 確認ポイント・説明

* 通常時はノイズ往復を防ぐために放置（ホールド）し、自分が死ぬ直前のタイミングに限って、他人のデータを一斉に解放するという高度な並行ハイブリッド設計が、if should_terminate: の直下のコードで完全にコミットされているか。

------------------------------

### 【ケース9】 停止命令（SIGTERM）のグレイスフルシャットダウン（src/monitor.py）

#### 1. テスト目的

Linux OS やコンテナ（ECS Fargate等）からシステム停止シグナル（SIGTERM）を受信した際、プロセスが即死せず、現在の周期のメッセージコミット（DELETE/RELEASE）をすべて完了させてから安全に終了することの検証。

#### 2. 前提条件・用意すべきもの

* テスト対象ファイル: src/monitor.py
* モック処理: os.kill(os.getpid(), signal.SIGTERM) を用いて、プログラム実行中に自発的にシグナルを発生させる。

#### 3. テスト手順

   1. プログラムを起動し、メインループの待機中に SIGTERM シグナルを送信する。
   2. ログファイル（logs/monitor.log）の軌跡を確認する。

#### 4. 想定結果

* [WARN] [__main__] Received SIGTERM from OS. Initiating graceful shutdown sequence... というログが出力されること。
* monitor_base.py 側の except SystemExit: へ合流し、終了コード 0 で安全に終了すること。

#### 5. 確認ポイント・説明

* 規約に適合した def handle_sigterm(_, __):（または handle_sigterm(signum,_)）のインターフェースが正常にシグナルをインターセプトできているか。
* sys.exit(0) を通じて投げられた SystemExit 例外が、未コミットデータの救出フェーズへ安全に繋がっているか。

------------------------------

### 【ケース10】 OS仕様（mkdir）に基づくアトミック二重起動防止（kick.sh）

#### 1. テスト目的

同じ11時に、全く同じ最終ジョブをターゲットにする kick.sh が完全に同時に実行された（多重実行された）際、1の隙も突かせずに片方だけを確実に起動させ、もう片方をブロックすることの検証。

#### 2. 前提条件・用意すべきもの

* テスト対象ファイル: kick.sh
* 疑似並行コマンド: kick.sh & kick.sh & のように、バックグラウンド並列で同時に2本走らせる。

#### 3. テスト手順

   1. ターミナルから、同じターゲットを指定した kick.sh を超高速で連続（または同時）に実行する。
   2. 画面の標準エラー出力およびプロセスの生存状況（ps コマンド等）を確認する。

#### 4. 想定結果

* 1本目のスクリプトは正常に Python を起動し、同期待機状態（Code 0 または 1 待ち）に入る。
* 2本目のスクリプトは即座に [ERROR] Glue Job Monitor for target [...] is already running. Aborting. を出力し、終了コード 1 で瞬時に弾かれること。

#### 5. 確認ポイント・説明

* ファイルによるチェック（if [ -f file ]）の脆弱性を克服し、OSカーネルレベルで一瞬の排他ロックを保証する mkdir "$LOCK_DIR" のアトミック性が正しく機能しているか。
* 1本目の同期処理が正常または異常終了した際、最上部で登録した trap 'rm -rf "$LOCK_DIR"' EXIT の遺言状が発火し、ロックディレクトリが自動的かつクリーンに消去され、次回の11時起動の道を綺麗に開けているか（死体残りバグの解消検証）。

------------------------------

### 【ケース11】 同期実行時における Python 結果コードの完全捕捉（kick.sh）

#### 1. テスト目的

フォアグラウンド（同期実行）において、Python 側が正常終了（0）またはジョブ失敗（1）を返した際、シェル側がその成否（終了ステータス）を 100% 正確に捕捉して後続の運用判定に繋げられることの検証。

#### 2. 前提条件・用意すべきもの

* テスト対象ファイル: kick.sh, src/monitor.py
* パターン11-1 (成功時): Glueジョブがすべて正常終了するイベントをシミュレート（Pythonの戻り値 0）。
* パターン11-2 (失敗時): 途中のジョブが失敗するイベントをシミュレート（Pythonの戻り値 1）。

#### 3. テスト手順

   1. パターン11-1 を実行し、kick.sh 自体の最終戻り値を echo $? で確認する。
   2. パターン11-2 を実行し、同様に kick.sh の最終戻り値を echo $? で確認する。

#### 4. 想定結果

* パターン11-1 (成功): 画面に [SUCCESS] Glue Job Pipeline completed successfully. (Code: 0) と出力され、シェルの最終ステータスも 0 であること。
* パターン11-2 (失敗): 画面に [FAILURE] Glue Job Pipeline failed or timed out. (Code: 1) と出力され、シェルの最終ステータスも 1 であること。

#### 5. 確認ポイント・説明

* Python 実行直前の set +e の解除が効いており、Python がコード1を返した瞬間にシェルスクリプト自体が途中で不格好にクラッシュする現象が完全に防げているか。
* 直後の EXIT_CODE=$? によって、Python の生の結果コードが変数へ安全に吸い出され、最終クリーンアップ（trapによるロック解除）の後に正しいコードで exit できているか。
