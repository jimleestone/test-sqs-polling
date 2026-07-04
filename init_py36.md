# Steps to using Python 3.6.8

## link current project to a ubuntu docker container

cd /Users/kaguya/test-sqs-polling

docker run -d --name python36-dev \
  --platform linux/amd64 \
  -v "$(pwd)":/workspace \
  -w /workspace \
  ubuntu:20.04 \
  sleep infinity


## install Python 3.6.8 inside the container

### 1. 必要な古いライブラリ（libssl1.0など）をUbuntuに入れる
docker exec -it python36-dev apt-get update
docker exec -it python36-dev apt-get install -y libssl1.1 libsqlite3-0 libbz2-1.0

### 2. 公式のPythonイメージから、Python本体の入ったフォルダを丸ごと現在のコンテナへコピー

#### 2.1 一時的なコンテナを作成
CONTAINER_ID=$(docker create --platform linux/amd64 python:3.6.8-slim)

#### 2.2 コンテナからMacのローカル（/tmp）へPython一式をコピー
docker cp ${CONTAINER_ID}:/usr/local/ /tmp/python36_extracted/

#### 2.3 使い終わった一時コンテナを削除
docker rm ${CONTAINER_ID}

#### 2.4 本番用のUbuntuコンテナへファイルを送り込む
docker cp /tmp/python36_extracted/. python36-dev:/usr/local/

#### 2.5 pip利用するため、コンテナ内の libffi.so.7 から libffi.so.6 へのリンクを作成する
docker exec -it python36-dev ln -s /usr/lib/x86_64-linux-gnu/libffi.so.7 /usr/lib/x86_64-linux-gnu/libffi.so.6

#### 2.6 ライブラリの検索パスを更新する
docker exec -it python36-dev ldconfig

#### 2.7 コンテナが正常か最終確認する
docker exec -it python36-dev python3 --version

## Attach the container in VSCode
 