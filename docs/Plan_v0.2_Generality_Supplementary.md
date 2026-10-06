Plan: v0.2 Highway-Neighbor Metapath Labeling — Region化による汎用性検証 & Supplementary Materials

TL;DR: v0.1 (MVP) はグルガオン（インド, NH48）1地域の bbox・CRS・named seed places を `src/config.py` にハードコードした単一地域パイプラインであり、方法論論文 README でも「汎用性は未検証」と明記している（Future Work 3番目）。v0.2 では bbox/CRS/クラスタリングパラメータを `RegionConfig` として抽象化し、グルガオンの出力・再現性を一切変更せずに、ドイツ（Stuttgart–Ludwigsburg Core Belt）・台湾（Taoyuan–Zhongli–Hsinchu Corridor Core）・日本（Nagoya–Toyota–Komaki Core Belt）の3工業都市（グルガオンの実績bbox 約30km×43kmとほぼ同一スケールの約30〜39km級に設定。当初案の100km四方・次いで45〜56km級でも、ドイツ・台湾・日本いずれも工業用地ポリゴンのOSMタグ密度が高くOverpassクエリが現実的な時間で返らないことが実行時に判明し、段階的に縮小した）へ named seed places なし（純粋 DBSCAN 自動検出）で同一パイプラインを適用し、4地域の統計量を比較する。得られた汎用性の教訓を、方法論論文（`paper_highway_metapath/1_Methodology/v5_paper_to_arXiv/`）の巻末に新設する Supplementary Materials セクションにまとめる。

Steps

Phase 1: Region抽象化のリファクタリング (独立実行可能)
1. `src/regions.py` (新規) — `RegionConfig` dataclass を定義:
   - フィールド: `key, display_name, bbox(west,south,east,north), crs_metric, seed_places(dict, default={}), seed_match_tolerance_km, dbscan_eps_m, dbscan_min_samples, neighbor_radii_km, chain_distance_threshold_km, priority_high_chain_km, priority_high_neighbor_km, is_default(bool)`
   - パス用の `@property`: `data_raw_dir / data_processed_dir / figures_dir / labels_dir` — `is_default=True`（gurgaon）のときは既存のフラットパス（`data/raw/`, `outputs/labels/` 等、後方互換・論文の再現性記述を変更しない）、それ以外は `data/raw/<key>/` のようにサブディレクトリを切る
   - `REGIONS: dict[str, RegionConfig]` に4地域を登録: `gurgaon`（既存 `config.py` の値をそのまま移植、`is_default=True`、bbox実績 約30km×43km）、`stuttgart`（bbox=[9.05,48.70,9.35,49.00]、約33.4km×22.0km, crs_metric=EPSG:32632）、`taoyuan_hsinchu`（bbox=[121.00,24.70,121.30,25.05]、約39.0km×30.3km, crs_metric=EPSG:32651）、`nagoya_toyota_komaki`（bbox=[136.80,35.00,137.10,35.30]、約33.4km×27.3km, crs_metric=EPSG:32653）。新規3地域は `seed_places={}`（named seed snapping は行わない、Decisions参照）
   - `get_region(key: str) -> RegionConfig`、`ensure_dirs(region)` ヘルパーを実装
2. `src/osm_fetch.py` (depends on 1) — `fetch_highway_graph/fetch_industrial_polygons/fetch_warehouse_features/fetch_all` に `region: RegionConfig` 引数を追加し、`config.BBOX`/`config.DATA_RAW_DIR` 参照を `region.bbox`/`region.data_raw_dir` に置換。`__main__` ブロックは `region=regions.get_region("gurgaon")` をデフォルト指定
3. `src/clustering.py` (depends on 1, parallel with 2) — `_load_points/_dbscan_cluster/_seed_assignment/build_clusters` に `region` 引数を追加し、`config.DATA_RAW_DIR/CRS_METRIC/DBSCAN_EPS_M/DBSCAN_MIN_SAMPLES/SEED_PLACES/SEED_MATCH_TOLERANCE_KM` を `region.*` に置換。`seed_places` が空 dict の地域では `_seed_assignment` が空 dict を返し、全クラスタが `is_named_seed=False` の `FactoryCluster_auto_N`/`LogisticsCluster_auto_N` になることを確認（既存ロジックのままで対応可能、分岐追加不要）
4. `src/highway_segments.py` (depends on 2) — `build_segments_and_corridors` に `region` 引数を追加し、`config.CRS_METRIC` を `region.crs_metric` に置換
5. `src/metapath.py` (depends on 3, 4) — `_load_clusters/compute_near_highway/compute_h_neighbor/compute_h_chain_access/run_all` に `region` 引数を追加し、`config.NEIGHBOR_RADII_KM/CHAIN_DISTANCE_THRESHOLD_KM/CRS_METRIC/DATA_PROCESSED_DIR` を `region.*` に置換
6. `src/label_export.py` (depends on 5) — `build_pair_label_table/build_segment_flag_table/run_export` に `region` 引数を追加し、`config.PRIORITY_HIGH_CHAIN_KM/PRIORITY_HIGH_NEIGHBOR_KM/LABELS_DIR` を `region.*` に置換
7. `src/visualize.py` (depends on 6) — `run_visualization` に `region` 引数を追加し、`config.FIGURES_DIR` 等を `region.*` に置換。出力ファイル名を `<region.key>_highway_metapath_map.{png,html}` とする（gurgaonのみ既存ファイル名 `gurgaon_highway_metapath_map.*` を維持）
8. `run_pipeline.py` (depends on 2-7) — `argparse` に `--region {gurgaon,stuttgart,taoyuan_hsinchu,nagoya_toyota_komaki}`（default=`gurgaon`）を追加し、`main(region_key, force_refetch)` が `regions.get_region(region_key)` を解決して各ステップ関数に渡す。named seed が0件の地域では「Top named Industrial Park <-> Logistics District pairs」ログブロックをスキップし、代わりにクラスタサイズ上位5件をログ出力する分岐を追加

Phase 2: 3地域での実行 (depends on Phase 1, 各地域は独立実行可能・並列可)
9. `.venv-himet\Scripts\python.exe run_pipeline.py --region stuttgart` を実行し、Overpass 経由で OSM データ取得→クラスタリング→メタパスラベリング→可視化まで完走させる
10. `.venv-himet\Scripts\python.exe run_pipeline.py --region taoyuan_hsinchu` を実行（9と並列可）
11. `.venv-himet\Scripts\python.exe run_pipeline.py --region nagoya_toyota_komaki` を実行（9, 10と並列可）
12. 各地域の `outputs/<region>/labels/*.csv` と `data/raw/<region>/*` を確認し、OSM取得に失敗する地域（Overpassタイムアウト・bbox分割要否）があれば `--force-refetch` 再試行、または bbox を上限100km以内で維持したまま軽微調整する

Phase 3: 比較分析 & 執筆 (depends on Phase 2)
13. `docs/region_comparison_v0_2.csv`（新規）— 4地域（gurgaon含む）について次の列を集計: `industrial_polygons, warehouse_features, highway_nodes, highway_edges, n_connected_components, factory_clusters, logistics_clusters, named_seed_match_rate, possible_pairs, labeled_pairs, priority_high, priority_medium, priority_none, segments_total, segments_high_pct, segments_medium_pct`
14. `paper_highway_metapath/1_Methodology/v5_paper_to_arXiv/supplementary.tex` (新規, depends on 13) — Supplementary Materials セクション。3地域それぞれの短いケーススタディ（bbox・取得統計・クラスタ数・ラベル分布・1地域あたり3〜5行の考察）+ 「Lessons on Generality」サブセクション（閾値の地域間差異、OSMタグ密度差、グラフ連結性の差、named seed無しでの実用可能性、bbox 100km制約の妥当性）
15. `main.tex` (depends on 14) — `\appendix` の後、`\input{appendix}` の次に `\input{supplementary}` を追加。Abstract/Conclusion/Future Work の「generalizing the method to other...corridors」の記述を「本論文のSupplementary Materialsで3地域への適用を実施済み」に更新
16. `paper_highway_metapath/README.md` (depends on 14, 15) — 「3. 数値実験のアウトライン」の下に「3.5 Supplementary: 他地域への適用」小節を追記し、region_comparison の要約を記載
17. リポジトリ `README.md` / `src/config.py` 冒頭docstring — `--region` オプションの説明を追記（後方互換: 引数省略時は従来通りgurgaon・既存パスで動作）

Relevant Files
- `src/config.py` — グルガオンの定数定義はそのまま保持（`regions.py` がこれを読み込んで `REGIONS["gurgaon"]` を構築、後方互換の柱）
- `src/regions.py` (新規) — `RegionConfig` dataclass + 4地域registry
- `src/osm_fetch.py`, `src/clustering.py`, `src/highway_segments.py`, `src/metapath.py`, `src/label_export.py`, `src/visualize.py` — `region` 引数化
- `run_pipeline.py` — `--region` CLI引数
- `data/raw/<region>/`, `data/processed/<region>/`, `outputs/<region>/{labels,figures}/` — 新規3地域の出力（gurgaonは既存フラットパス）
- `docs/region_comparison_v0_2.csv` (新規) — 4地域比較表
- `paper_highway_metapath/1_Methodology/v5_paper_to_arXiv/supplementary.tex` (新規), `main.tex`, `conclusion.tex` — Supplementary Materials セクション追加・Future Work更新

Verification
- `pytest` 相当のユニットテストは本リポジトリに未整備のため、`run_pipeline.py --region gurgaon` を再実行し、`outputs/labels/factory_logistics_metapath_labels.csv` の行数（122）・`segment_hard_constraint_flags.csv` の High件数（34）が v0.1 の既報値と完全一致することを確認する（回帰防止）
- 新規3地域それぞれで `run_pipeline.py --region <key>` が例外なく完走し、`outputs/<key>/labels/factory_logistics_metapath_labels.csv` が1行以上生成されることを確認する
- 各地域の highway graph 連結成分数・クラスタ数・ラベル付与率を `docs/region_comparison_v0_2.csv` に記録し、4地域間で値が大きく異なる（=閾値の地域差が存在する）ことを定性的に確認する
- `pdflatex main.tex` (×2) を `v5_paper_to_arXiv/` で実行し、エラー0件・未解決参照0件で `main.pdf` がコンパイルできることを確認する

Decisions
- 新規3地域は named seed places を用意しない（自動クラスタリングのみ）: 汎用性検証の目的は「土地勘のある手動キュレーション無しで手法が機能するか」を見ることであり、Gurgaonのように施設名を事前にgeocodingする作業はスコープ外とする（ユーザー確認済み）
- Gurgaonの出力パス・ファイル名は一切変更しない: 既に arXiv 論文・`paper_highway_metapath/1_Methodology/RESULT/` にコピー済みの数値（122ペア, 34セグメント等）を破壊しないため、`is_default=True` 地域のみ既存フラットパスを維持する後方互換分岐を設ける
- CRS_METRIC は3地域ともUTM単一ゾーンに収まるものを選定（Stuttgart→EPSG:32632, Taoyuan-Hsinchu→EPSG:32651, Nagoya→EPSG:32653）: bboxがいずれも単一UTMゾーン内に収まることを事前確認済みで、Gurgaonと同じ「単一メートル法CRSへの投影」方式をそのまま踏襲できる
- DBSCAN閾値（eps=1.5km, min_samples=2）・NEIGHBOR_RADII・CHAIN_DISTANCE_THRESHOLD はまず3地域ともGurgaonと同一値を使う: 「同一パラメータでの転移可能性」自体が汎用性検証の主眼であり、地域ごとの再較正は一次実行では行わない（Supplementary本文で「再較正の要否」を考察事項として記述する）
- `src/osm_fetch.py` に環境変数 `HIMET_OVERPASS_URL` / `HIMET_OSM_TIMEOUT` を追加し、既定の公開Overpass API（`overpass-api.de`、DNSラウンドロビンで複数IPに解決され、本実行環境では一方のIPが到達不能で接続がハングする事例を確認）を上書き可能にする: デフォルト値は変更せず（再現性記述に影響しない）、実行時のみ `overpass.kumi.systems` 等のミラーとより長いタイムアウトを指定できるようにする
- bboxはグルガオン実績（約30km×43km）と同一スケールの約30〜39km級に設定（当初案の100km四方→45〜56km級→30km級、の二段階で縮小）: ドイツ（Stuttgart）・台湾（Taoyuan-Hsinchu）・日本（Nagoya-Toyota-Komaki）いずれも工業用地ポリゴン（`landuse=industrial`）のOSMタグ密度が非常に高く、45〜56km級でもOverpassクエリが現実的な時間で返らない／ゲートウェイエラーになることが実行時に判明したため、ユーザー指示によりGurgaonとほぼ同一の30km級（Stuttgart 33.4×22.0km, Taoyuan-Hsinchu 39.0×30.3km, Nagoya-Toyota-Komaki 33.4×27.3km）へ最終的に縮小する。これにより「工業用地ポリゴン密度が高いコア帯のみを比較する」実験設計にもなる

Further Considerations
- OSM取得が一部地域でタイムアウト/部分欠損する場合の扱い
  推奨: `--force-refetch` で1回再試行し、それでも失敗する場合は当該レイヤ（例: warehouse features）が0件のまま処理を続行し、Supplementary本文に「データカバレッジの限界」として明記する（Gurgaonの既存Discussion 6.3.1節と同じ扱い方針を踏襲）
- 新規3地域で highway graph が複数連結成分に分かれ H_CHAIN_ACCESS が広範囲で欠損する場合
  推奨: bboxを拡張せず（Gurgaonと同スケールの30km級を優先）、連結成分数をそのまま「地域固有の制約」として比較表・Supplementaryに記録する（Gurgaonの5連結成分という既存の限界記述と整合させる）
- 3地域の named facility 個別解説（Mercedes-Benz, TSMC, Toyota等）をSupplementary本文に含めるか
  推奨: 本文では施設名に触れず「bbox内の自動検出クラスタ数・最大クラスタの位置」のみ定量的に記述する（Decisionsの「named seed無し」方針と整合）。読者向けの文脈説明（なぜこの3地域を選んだか）は各ケーススタディの冒頭1文に留める
- v0.3以降でのnamed seed追加や閾値再較正の要否
  推奨: 今回のSupplementary実験で「同一閾値の転移可能性」の一次評価を済ませ、再較正が必要と判明した場合のみ次バージョンで対応する（Future Workに追記して今回はスコープ外のままにする）
