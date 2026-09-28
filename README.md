# 港区 墓地距離マップ

東京都港区で「墓地から離れた住宅エリア」を探すための地図です。

**公開ページ：https://dkeikichi.github.io/tokyo-minato-cemetery-map/**
**Cloudflare公開ページhttps://tokyo-minato-cemetery-map.pages.dev/

`index.html` 1ファイルで動きます（Leaflet を cdnjs から読み込むため、インターネット接続が必要です）。

## できること

- **距離の色分け**：港区内の各地点から、いちばん近い墓地の敷地の端までの直線距離を色で表示します。赤は墓地に近い場所、青は希望の距離より離れている場所です。
- **希望距離のスライダー**：50〜500m の範囲で「墓地から何m以上離れたいか」を選ぶと、色分けと集計がすぐに変わります。
- **地点の確認**：地図をタップすると、その地点から最寄りの墓地までの距離、徒歩分数（80m＝1分）、最寄り駅を表示します。
- **ランキング**：条件を満たす土地の割合を町ごと・丁目ごと・駅ごと（徒歩10分圏）に並べます。行をタップすると地図がそのエリアに移動します。
- **寺院オプション**：地図に墓地の記載がない寺院も、半径30mを墓地として扱えます（境内墓地が未登録のことがあるため）。

割合は、公園・墓地・線路・工場や倉庫・大学・水面などを除いた「住宅向けの土地」で計算しています。

## データ

- [Overture Maps Foundation](https://overturemaps.org/) release `2026-09-23.1`（base / divisions / places / transportation）。多くは © OpenStreetMap contributors（ODbL）由来です。
- 墓地・霊園：OSM の `landuse=cemetery` と `amenity=grave_yard`。港区内に 136 か所あり、区境から700m以内にある周辺区の墓地も計算に含めています。
- 寺院墓地の名前は、70m以内にある寺院の名前から推定しています。

## 作り直す手順

```sh
pip install pyarrow shapely numpy scipy
python3 scripts/fetch_overture.py   # work/overture/*.parquet を取得（S3 から港区周辺だけ）
python3 scripts/build_data.py       # data/minato.json を作成
python3 scripts/build_html.py       # src/template.html + data から index.html を作成
```

距離の計算（8m メッシュでのユークリッド距離変換）と集計は、ブラウザ上で行います。

| パス | 内容 |
| --- | --- |
| `index.html` | 公開ページ（ビルド済み） |
| `src/template.html` | ページの元になるテンプレート |
| `data/minato.json` | 墓地・町丁目・駅・道路などの地図データ |
| `scripts/` | データ取得とビルドのスクリプト |

## 注意

距離は直線距離で、道のり・坂・建物の高さは考えていません。データに登録されていない墓地もあり得ます。物件を決める前に、必ず現地で確認してください。
