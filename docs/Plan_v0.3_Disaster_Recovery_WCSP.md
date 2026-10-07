Plan: v0.3 Disaster Pavement Recovery Scheduling WCSP — 地震シナリオによる供給網リカバリースケジューリング（Gurgaon先行・4地域対応）

TL;DR: v0.1/v0.2 は Gurgaon（および3つの汎用性検証地域: Stuttgart, Taoyuan-Hsinchu, Nagoya-Toyota-Komaki）について FactoryCluster/LogisticsCluster と HighwaySegment のメタパスラベル（`H_NEIGHBOR(r)` / `H_CHAIN_ACCESS(d)`、`is_metapath_hard` フラグ）を静的に出力するところまでで止まっており、それを「いつ・どのセグメントから復旧するか」という時間軸の意思決定に接続する層は未実装（README の「Integration with `method_repair_lot_wcsp`」節は "join by spatial overlap" という設計意図の記述のみ）。v0.3 では、`docs/ScenarioOptimization_20261007.jpg` のコンセプト（対象地域全域で巨大地震 → 路面クラック/ポットホール＋土砂崩れによる通行不能 → Supply Chain Loss 最小化のため何をどの順で復旧するか → Recovery Benefit–distance のフロンティア）を、既存の v0.1/v0.2 出力（Gurgaon実測: `outputs/labels/factory_logistics_metapath_labels.csv` 122行、`segment_hard_constraint_flags.csv` 900 segment・34 `is_metapath_hard`、HIGHWAY_CONTIGUOUS グラフ 626 nodes/745 edges・5 connected components）を直接の入力として、新しい `src/disaster_recovery/` サブパッケージで実装する。**v0.2 の `RegionConfig` をそのまま再利用し**、まず Gurgaon で実装・検証した後、同一コード・同一デフォルトパラメータで Stuttgart・Taoyuan-Hsinchu・Nagoya-Toyota-Komaki の3地域にも適用できるようにする（`--region` 引数、`region.key` 別出力先）。地震被害は実データが無いため固定シードの合成シナリオ生成器で与え、Repair Lot 単位のタブーサーチにより `Σ_t SC_Loss(t)` と `Σ_{k,t} x_{k,t}·cost_k` を同時に小さくするスカラー目的 `J(x)=α1 f1+α2 f2+β_hard v_hard+β_cap v_cap` を最小化し、復旧スケジュール・SC_Loss 時系列・Recovery Benefit–distance フロンティア（コンセプトノートのスケッチに対応する階段状の曲線）を地域ごとの `outputs/disaster_recovery/`（Gurgaon）または `outputs/<region>/disaster_recovery/`（他3地域）に出力する。

Steps

Phase 0: 面積重み付けのための既存クラスタリング出力の後方互換拡張 (独立実行可能)
1. `src/clustering.py` の `build_clusters(region)` — 返り値 GeoDataFrame に `area_m2` 列（列挙順は既存列の後に追加のみ、既存列は一切変更しない）を追加する。`_load_points` が現状センターポイントしか保持していないため、`_load_points` 内で `ind.geometry.area` / `wh.geometry.area`（いずれも `region.crs_metric` 投影後、Polygon は実面積、Point ジオメトリ（将来 Point 単体の `building=warehouse` ノードが混在するケース向け）は 0 として扱う）を `member_area_m2` 列として保持したまま centroid 化し、`build_clusters` のクラスタ集約ループで `members["member_area_m2"].sum()` を `area_m2` として書き出す。実データ確認済み: Gurgaon の `industrial_polygons.geojson`（84件）・`warehouse_features.geojson`（17件）は全件 Polygon ジオメトリのため、v0.3 スコープでは 0 フォールバックは実質発生しない。
2. 回帰確認: `run_pipeline.py --region gurgaon` を再実行し、`outputs/labels/factory_logistics_metapath_labels.csv` が 122 行、`segment_hard_constraint_flags.csv` の `is_metapath_hard=True` が 34 件のまま変化しないこと（`area_m2` は `label_export.py` では未使用の列のため、既存2出力には影響しない）を確認する。

Phase 1: Region出力先の拡張 ＋ 合成災害シナリオ生成 (depends on Phase 0 の `clusters.geojson` 再生成、ただし独立並行で実装可)
3. `src/regions.py` (depends on なし, 独立実行可能) — `RegionConfig` に `@property disaster_recovery_dir` を追加: `is_default=True`（gurgaon）なら `config.OUTPUTS_DIR / "disaster_recovery"`、それ以外は `config.OUTPUTS_DIR / self.key / "disaster_recovery"`（既存の `labels_dir`/`figures_dir` と同じ後方互換パターン）。`ensure_dirs(region)` にこのディレクトリ（と `labels/schedule/metrics/figures` サブフォルダ）の作成も追加する。
4. `src/disaster_recovery/__init__.py` (新規) / `src/disaster_recovery/config.py` (新規) — 4地域共通のデフォルト地震シナリオパラメータを集約（region非依存、v0.2 の「同一閾値をまず4地域に転移する」方針を踏襲）: `SCENARIO_SEED=42`, `BLOCK_PROB=0.04`（土砂崩れ等による通行不能の発生確率／segment）, `CRACK_PROB=0.08`（路面クラック・ポットホールの発生確率／segment、非遮断）, `HORIZON_DAYS=14`（T）, `DAILY_CREW_CAPACITY=4`（C_t、全日程で一定値を既定とし、`dict[int,int]` で日ごと上書き可能にする）, `LOSS_DISTANCE_SLACK_FACTOR=1.5`, `AREA_UNIT_DIVISOR=1e6`（m²→km² 正規化用）, `FALLBACK_MEMBER_AREA_KM2=0.01`, `ALPHA_SC_LOSS=1.0`, `ALPHA_COST=1.0`, `BETA_HARD=100.0`, `BETA_CAP=20.0`, `TABU_TENURE=15`, `MAX_ITERATIONS=2000`, `NO_IMPROVE_LIMIT=300`。出力先は定数ではなく `region.disaster_recovery_dir`（3の `RegionConfig` 拡張）を都度参照する。
5. `src/disaster_recovery/scenario.py` (新規, depends on 3) — `generate_segment_damage(region, seed=config.SCENARIO_SEED) -> gpd.GeoDataFrame`:
   - `segments, corridor_geoms, graph_u = highway_segments.build_segments_and_corridors(region)`（ディスク上の `data/processed/<region>/highway_segments.geojson` は Gurgaon 以外では永続化されていないため、既存の `metapath.run_all` と同じくメモリ上で再構築する）と `region.labels_dir / "segment_hard_constraint_flags.csv"`（`segment_id, corridor_id, highway, length, metapath_priority, is_metapath_hard`）を `segment_id` で結合。
   - `numpy.random.default_rng(seed)` で各 segment に独立に `landslide_block ~ Bernoulli(BLOCK_PROB)`、`crack_pothole ~ Bernoulli(CRACK_PROB)`（両方成立したら `landslide_block` を優先し `damage_type="landslide_block"` とする）を割り当て、`damage_type ∈ {"none","crack_pothole","landslide_block"}` 列を作る。
   - `initial_open (y_{s,0})`: `damage_type=="landslide_block"` なら 0、それ以外（`"none"` / `"crack_pothole"`）は 1（クラック・ポットホールは通行不能にはしない、という v0.3 スコープの単純化）。
   - `disaster_hard`: `initial_open==0` の segment にのみ 1（＝地震直後に通行不能＝必ず復旧対象、ユーザー定義の `Hard_k^disaster`の segment-level 版）。
   - 出力: `region.data_processed_dir / "disaster_scenario_t0.geojson"`（上記列＋geometry）、`region.disaster_recovery_dir / "labels" / "disaster_segment_damage.csv"`（geometry 抜き）。
   - モジュール docstring に「実際の地震動・斜面崩壊危険度データは本バージョンでは未使用。固定シード合成シナリオであり、再現可能だが地震工学的な妥当性の検証はしていない」ことを明記する（README の limitations と同じトーン）。

Phase 2: Repair Lot 構築 (depends on Phase 1)
5. `src/disaster_recovery/repair_lots.py` (新規, depends on 4) — `build_repair_lots(scenario_gdf, region) -> gpd.GeoDataFrame`（実装では、トリガー segment の `(u,v)` から直接隣接グラフを構築するため `graph_u` 引数は不要と判明し、シグネチャから省いた）:
   - トリガー集合 `Utrigger = {segment : damage_type != "none"}`（Repair Lot Skyline WCSP の Definition 4 の crack/pothole trigger units に相当するが、本リポジトリのスキーマは連続 chainage ではなく離散 OSM way edge なので、chainage gap `g` の代わりに `highway_segments.build_segments_and_corridors` が返す `graph_u`（HIGHWAY_CONTIGUOUS, ノード共有グラフ）上での隣接性を使う）。
   - `graph_u` の中で `Utrigger` に属する segment の `(u, v)` エッジのみを残した部分グラフを作り、その連結成分 1 つを 1 Repair Lot `Lk` とする（corridor_id の跨ぎは許容——実データで `corridor_id` が 489 件・900 segment中、OSM `ref`/`name` が無い edge は `segment_{i}` という singleton フォールバック corridor になっており、corridor_id 単位でのバンドリングは物理的に隣接する被災区間を過剰分割してしまうため、corridor ではなく graph adjacency を分割単位に採用する——Decisions 参照）。
   - 各 Lk に: `lot_id`, `segment_ids`（;区切り）, `n_segments`, `length_total_m`（Σ length）, `primary_corridor_id`（構成 segment の最頻 corridor_id）, `hard_disaster`（いずれかの segment が `disaster_hard==1`）, `hard_metapath`（いずれかの segment が `is_metapath_hard==True`）, `hard = hard_disaster or hard_metapath`, `cost_k = length_total_m`（f2 の距離／コスト換算は 1 m = 1 cost unit、Decisions参照）, `impact_weight_k`（後述 Phase 3 の pair 重みから計算、Phase 5 の初期解ヒューリスティックに使用）。
   - 出力: `region.disaster_recovery_dir / "labels" / "disaster_repair_lots.csv"`、同ディレクトリの `disaster_repair_lots.geojson`（member segment geometry の union）。

Phase 3: Supply Chain Loss 評価器 (depends on Phase 0, 独立に Phase 1/2 と並行実装可、solve 時に全て必要)
6. `src/disaster_recovery/weights.py` (新規, depends on Phase 0) — `compute_cluster_weights(region) -> pd.DataFrame[cluster_id, name, node_type, area_m2, weight_km2]`: `region.data_processed_dir / "clusters.geojson"` の `area_m2` 列を読み、`weight_km2 = max(area_m2, FALLBACK_MEMBER_AREA_KM2*AREA_UNIT_DIVISOR) / AREA_UNIT_DIVISOR` として `w_i`（factory）・`v_j`（logistics）を返す。
7. `src/disaster_recovery/loss.py` (新規, depends on 6) — `SCLossEvaluator` クラス:
   - `__init__(region)`: `region.labels_dir / "factory_logistics_metapath_labels.csv"` を読み、`priority != "None"` の行を候補ペアとする。各ペアの `factory_name`/`logistics_name` を `clusters.geojson` の centroid 経由で `graph_u` の最近傍ノードにスナップ（`metapath._nearest_graph_node` のロジックを再利用）。intact な `graph_u` 上で `nx.has_path` を調べ、**到達不能なペアは本バージョンのスコープ外として除外**（Gurgaon実測: 626 nodes/745 edges のグラフで 5 connected components、122 ペア中 2 ペアのみが地震と無関係に恒久的に到達不能——Decisions参照。他3地域は region ごとに独立に同じロジックで判定し、固定値をハードコードしない）。残りのペアそれぞれについて intact 最短距離 `base_km` を `nx.shortest_path_length` で前計算し、許容閾値 `allowed_km = max(region.chain_distance_threshold_km, base_km * LOSS_DISTANCE_SLACK_FACTOR)` を固定する。
   - `compute_sc_loss(blocked_lot_ids: frozenset[str]) -> float`: `blocked_lot_ids` に含まれる Repair Lot が持つ segment の `(u,v)` エッジを `graph_u` のコピーから削除した部分グラフ上で、120 候補ペアそれぞれについて `nx.has_path` → 到達不能なら即 `Loss_ij = w_i*v_j`、到達可能なら `nx.shortest_path_length`（weight="length"）と `allowed_km` を比較し、超過していれば同様に `Loss_ij = w_i*v_j`、それ以外は 0。`SC_Loss = Σ Loss_ij` を返す。
   - 性能対策（Decisions参照）: `functools.lru_cache` 相当の `dict[frozenset[str], float]` メモをインスタンス内に持ち、同一 `blocked_lot_ids` の再評価を避ける（タブーサーチの近傍操作は通常 1～2 lot だけ変化するため、日ごとの blocked set の重複が多い）。

Phase 4: タブーサーチソルバー (depends on Phase 2, 3)
8. `src/disaster_recovery/tabu_solver.py` (新規, depends on 5, 7) — 解表現: `assignment: dict[lot_id, int | None]`（各 `hard==True` の Repair Lot を `1..T` のいずれかの日、または `None`＝horizon内未着手、に割り当てる。`hard==False`（crack-only かつ非 `is_metapath_hard`）の lot は v0.3 の `J(x)` に寄与しないため既定では探索対象から除外し、`disaster_repair_lots.csv` には記録するのみとする——Decisions参照）。
   - `y_{s,t}` は `assignment` から導出: `open(s,t) = initial_open(s) OR (lot containing s is assigned day <= t)`。
   - 初期解: `hard` ロットを `impact_weight_k`（= Σ 対象 segment が属する `metapath_pairs` の `w_i*v_j` 合計、`segment_hard_constraint_flags.csv` の `metapath_pairs` 列を再利用）降順・容量 `C_t` を守りながら貪欲に日へ詰める greedy construction。
   - 近傍操作: (a) ロットを別日へ移動, (b) 2ロットの日を交換, (c) 未割当ロットを空き日へ割当, (d) 割当済みロットを未割当に戻す（`hard` ロットも対象——`v_hard` ペナルティで強く抑制されるが構造的禁止はしない、ユーザー仕様の "タブーサーチでは...ハード制約違反にはペナルティ" に忠実）。
   - タブーリスト: 直近 `TABU_TENURE` 反復で適用した `(lot_id, from_day, to_day)` を禁止、ただし `J(x)` が既知最良を更新する場合は aspiration criterion で許可。
   - 停止条件: `MAX_ITERATIONS` または `NO_IMPROVE_LIMIT` 連続改善なし。
   - 目的関数 `J(x)`: `f1(x)=Σ_{t=1}^{T} SC_Loss(t)`（`SCLossEvaluator.compute_sc_loss` を日ごとに呼ぶ）, `f2(x)=Σ_{assigned k} cost_k`、両者を `f1_norm = f1 / (T * SC_Loss(all-hard-still-blocked))`, `f2_norm = f2 / Σ_all-hard cost_k` で [0,1] 近傍に正規化してから `α1,α2` を掛ける（スケールの異なる面積ベースの Loss と距離ベースの Cost を同列に重み付けできるようにする）。`v_hard(x)=Σ_{k∈H}[assignment[k] is None]`、`v_cap(x)=Σ_t max(0, (#assigned lots on day t) - C_t)`。
   - 反復ごとの `J(x)` を `outputs/disaster_recovery/metrics/tabu_convergence.csv` に記録。

Phase 5: Recovery Benefit フロンティアと可視化 (depends on Phase 4)
9. `src/disaster_recovery/frontier.py` (新規, depends on 8) — `sc_loss_timeseries(best_assignment) -> pd.DataFrame`（列: `day, sc_loss_no_repair, sc_loss_with_repair`、`no_repair` は t=0 の被災状態を T 日固定した場合の `SC_Loss(t)` で全日同一値）。`build_recovery_frontier(best_assignment, lots) -> pd.DataFrame`（best 解で実際に割り当てられた順序でロットを 1 件ずつ累積適用し、列: `step, lot_id, day_scheduled, cumulative_distance_m, recovery_benefit_cumulative = Σ_{t=1}^{T}(sc_loss_no_repair(t) - sc_loss_with_repair_up_to_this_step(t))`）。
10. `src/disaster_recovery/export.py` (新規, depends on 9) — `region.disaster_recovery_dir` 配下に図を3枚生成: `figures/sc_loss_timeseries.png`（x=day, no-repair/with-repair の2本の折れ線）、`figures/recovery_frontier.png`（x=cumulative_distance_m(km換算), y=recovery_benefit_cumulative、コンセプトノートの手書きスケッチと同じ階段状 `step` プロット）、`figures/tabu_convergence.png`（反復 vs J(x)）。CSV: `schedule/repair_schedule.csv`（`lot_id, day_scheduled, cost_k, hard_disaster, hard_metapath`）、`metrics/sc_loss_timeseries.csv`、`metrics/recovery_frontier.csv`。

Phase 6: オーケストレーションとドキュメント (depends on Phase 1-5)
11. `run_disaster_recovery.py` (新規, リポジトリ直下, depends on 4,5,7,8,9,10) — CLI: `--region`（`choices=sorted(regions.REGIONS)`, 既定 `gurgaon`、v0.2 の `run_pipeline.py --region` と同じ流儀）, `--seed`（既定 `disaster_recovery.config.SCENARIO_SEED`）, `--horizon-days`, `--daily-capacity`, `--max-iterations`。実行前に `region.data_raw_dir / "highway_graph.graphml"` / `region.labels_dir / "segment_hard_constraint_flags.csv"` / `region.data_processed_dir / "clusters.geojson"` の存在を確認し、無ければ「先に `run_pipeline.py --region <key>` を実行してください」というエラーメッセージを出す。Phase 1〜5 の関数を順に呼び、最終サマリ（hard lot 数、horizon 内に割当済みの hard lot 数、最終 `J(x)`、累積 Recovery Benefit、SC_Loss 削減率）を標準出力に表示する。4地域すべてを連続実行するための補助スクリプト `run_disaster_recovery_all_regions.py`（新規、4回 `main(region_key=...)` を呼ぶだけの薄いラッパー）も用意する。
12. `README.md` — 「## v0.3: Disaster Pavement Recovery Scheduling WCSP」節を新設し、コンセプト（地震シナリオ、SC_Loss、Recovery Benefit フロンティア）、実行コマンド（`--region` 切り替え例を含む）、出力一覧表、既存 Integration 節（`method_repair_lot_wcsp` との接続）との関係（v0.3 は `is_metapath_hard` をハード制約に組み込む「downstream consumer」の具体例そのものである旨）を記載し、Gurgaon の実行後の代表的な数値（hard lot 数、horizon内復旧率、SC_Loss 削減率）と、他3地域では `is_metapath_hard` が常に0件（v0.2既報）のため `hard = hard_disaster` のみで駆動される旨を追記する。

Relevant Files
- `src/clustering.py` — `build_clusters` に `area_m2` 列を追加（既存列・既存2出力CSVの値は不変、Phase 0）
- `src/regions.py` — `RegionConfig.disaster_recovery_dir` プロパティ追加、`ensure_dirs` 拡張（Phase 1）
- `src/config.py` — 変更なし（v0.3 パラメータは `src/disaster_recovery/config.py` に分離し混在させない、Decisions参照）
- `outputs/<region>/labels/segment_hard_constraint_flags.csv`, `factory_logistics_metapath_labels.csv`, `data/processed/<region>/clusters.geojson`, `data/raw/<region>/highway_graph.graphml` — v0.3 の入力（4地域とも v0.1/v0.2 パイプラインの既存出力を再利用、再生成不要）
- `src/disaster_recovery/` (新規パッケージ) — `config.py`, `scenario.py`, `repair_lots.py`, `weights.py`, `loss.py`, `tabu_solver.py`, `frontier.py`, `export.py`
- `run_disaster_recovery.py`, `run_disaster_recovery_all_regions.py` (新規) — CLIエントリポイント
- `outputs/disaster_recovery/{labels,schedule,metrics,figures}/`（gurgaon）、`outputs/<region>/disaster_recovery/{labels,schedule,metrics,figures}/`（他3地域） (新規出力先)
- `README.md` — v0.3 節の追記

Verification
- Phase 0 後、`.venv-himet\Scripts\python.exe run_pipeline.py --region gurgaon` を再実行し、`outputs/labels/factory_logistics_metapath_labels.csv` が 122 行・`segment_hard_constraint_flags.csv` の `is_metapath_hard=True` が 34 件のまま（回帰なし）であることを確認する。
- `.venv-himet\Scripts\python.exe run_disaster_recovery.py --region gurgaon` を既定パラメータで実行し、例外なく完走すること。続けて `--region stuttgart` / `--region taoyuan_hsinchu` / `--region nagoya_toyota_komaki`（または `run_disaster_recovery_all_regions.py`）を実行し、4地域とも例外なく完走することを確認する。
- 各地域の `<disaster_recovery_dir>/labels/disaster_repair_lots.csv` で `hard=True` の行数を確認し、`<disaster_recovery_dir>/schedule/repair_schedule.csv` に同じ `lot_id` が全件（horizon内で割当可能な場合、すなわち hard lot 数 ≤ `HORIZON_DAYS * DAILY_CREW_CAPACITY` の場合）含まれる＝最終解で `v_hard(x) == 0` であることを確認する（hard lot 数が容量を超える場合は、その不足分を最終サマリに明示し、`HORIZON_DAYS` か `DAILY_CREW_CAPACITY` を増やす必要があることをログに出す）。
- 他3地域では `hard_metapath` が常に False（`is_metapath_hard` が0件のため）であることをログで確認し、`hard = hard_disaster` のみで駆動されることが v0.2 の既報と整合することを確認する（想定通りの挙動であり不具合ではない）。
- `repair_schedule.csv` を日付で集計し、どの日も `DAILY_CREW_CAPACITY` を超えないこと（`v_cap(x) == 0`）を確認する。
- `metrics/sc_loss_timeseries.csv` で全日 `sc_loss_with_repair <= sc_loss_no_repair` であること、`metrics/recovery_frontier.csv` の `cumulative_distance_m` と `recovery_benefit_cumulative` が共に単調非減少であることを確認する。
- `metrics/tabu_convergence.csv` の最終 `J(x)` が初期解（反復0）の `J(x)` 以下であることを確認する。
- 3枚の図（`sc_loss_timeseries.png`, `recovery_frontier.png`, `tabu_convergence.png`）が生成され、`recovery_frontier.png` がコンセプトノートのスケッチと同様の「立ち上がりが急で後半は逓減する」階段状であることを目視確認する（4地域それぞれで）。

Decisions
- v0.3 は v0.2 の `RegionConfig` をそのまま再利用し、Gurgaon で先行実装・検証した後、同一コード・同一デフォルトパラメータで Stuttgart・Taoyuan-Hsinchu・Nagoya-Toyota-Komaki の3地域にも適用する: v0.2 で「同一閾値の転移可能性」自体が汎用性検証の主眼として既に評価済みであり、v0.3 もその方針を踏襲する。ただし他3地域は named seed places を持たず `is_metapath_hard` が常に0件（v0.2既報）のため、`hard_metapath` は常に False になり `hard = hard_disaster` のみで駆動される——これは本機能の欠陥ではなく、「メタパスラベルを持たない地域では災害ハード制約のみが働く」という v0.2 の限界がそのまま反映された、期待された挙動として扱う。
- 地震被害（`landslide_block`/`crack_pothole`）は固定シード（`SCENARIO_SEED=42`）の合成生成器で与える: Gurgaon 近傍の実際の地震動・斜面崩壊危険度データ（例: 地質調査や DEM ベースの landslide susceptibility map）は本リポジトリに存在せず、取得も v0.3 のスコープ外とする（Further Considerations参照）。再現性を優先し、`numpy.random.default_rng(seed)` で決定的に生成する。
- Repair Lot のバンドリング単位は `corridor_id` ではなく `graph_u`（HIGHWAY_CONTIGUOUS）上の隣接性を使う: 実データで 900 segment が 489 corridor に分かれており（OSM `ref`/`name` が無い edge は singleton フォールバック corridor）、corridor_id 単位でバンドリングすると物理的に隣接する被災区間が過剰に分割される。親論文（Repair Lot Skyline WCSP）の chainage gap `g` によるクラスタリング（Definition 4）を、本リポジトリの離散 OSM way edge スキーマに合わせてグラフ隣接性に置き換えた、という位置づけ。
- SC_Loss の対象ペアは「意図されたメタパス関係がある（`priority != "None"`）」122ペアのうち、intact グラフで到達可能な120ペアに限定する: 残り2ペアは地震と無関係にbboxグラフが5連結成分に分かれていることに起因する恒久的な到達不能であり（v0.2 Supplementary で既報の限界）、これを「災害によるLoss」として扱うのは誤りであるため除外する。
- 到達可能性の許容閾値は固定 40km（`region.chain_distance_threshold_km`）ではなく、各ペアの intact 最短距離 `base_km` の `LOSS_DISTANCE_SLACK_FACTOR=1.5` 倍との大きい方（`max(40, base_km*1.5)`）を使う: 122ペア中86ペアのみが40km以内で、残り34ペアは40km超だが同一bbox内で実在する経路を持つため（実測確認済み）、固定40kmだけでは「軽微な迂回」と「Lossに値する到達不能」を区別できない。
- v0.3 の `J(x)` は crack-only（`hard=False`）の Repair Lot を探索対象に含めない: ユーザー提示の目的関数仕様 (`f1`=SC_Loss, `f2`=施工距離/コスト, ハード制約違反ペナルティ) に crack 単独の便益項（親論文の `Benefit_j=wsoft*Rj` 相当）が含まれておらず、現行仕様のままでは crack-only ロットを計画に入れても `f1` は不変で `f2` だけ増えるため、最適解は常にそれらを除外する。探索空間から構造的に外すことで、タブーサーチの近傍サイズを hard ロット数程度に抑える（性能・可読性の両面で有利）。
- タブーサーチの目的関数は `f1`/`f2` を正規化してから重み付けする: `w_i, v_j`（面積ベース, km²）と `cost_k`（長さ, m）はスケールが大きく異なり、ユーザー仕様の `α1, α2` をそのまま等倍で使うと一方の項が常に支配的になるため、[0,1] 近傍に正規化した上で既定 `α1=α2=1.0`, `β_hard=100, β_cap=20`（`β_hard ≫ β_cap ≫ α1,α2` というユーザー仕様の大小関係を保持）とする。
- NSGA-II 等による真の多値 Pareto フロンティア探索は実装しない: ユーザー提示のコンセプトでも「あるいは、時刻ごとにLossを最小化する単一目的としてもよい」と明記されており、タブーサーチの scalarized `J(x)` と、採用順序から事後的に構築する skyline 風の Recovery Benefit–distance フロンティア（Phase 5）で、コンセプトノートの「Disaster Recovery Frontier」を再現するという MVP としての要求を満たせるため（Further Considerations に v0.4 以降の拡張として記載）。
- **【初回実装後の修正】Repair Lot 1件＝1日で完了という近似を廃止し、ロット長・損傷タイプ別の現実的な生産性（production rate）に基づく複数日所要日数モデルへ変更した**: 初回実装（`DAILY_CREW_CAPACITY` を「1日あたり処理可能なロット数」として扱うモデル）で Gurgaon/Stuttgart/Taoyuan-Hsinchu の3地域とも Recovery Benefit が厳密に同一の 92.9% に一致し、かつ `sc_loss_timeseries.png` がいずれも2日以内に全回復するという不自然な結果が得られた。実データ確認の結果、Gurgaonの最長ハードロット（L57, 長さ5,793m）が「1日で復旧完了」として記録されており、これが原因と判明した（ユーザー指摘により発覚）。β_hard=100 自体はハード制約を必ずT日以内に"着手"させる役割のみで「早く終わらせる」圧力は生まず、92.9%一致・2日回復という人工物の直接原因ではないと判断した。修正として、`src/disaster_recovery/repair_lots.py` に `duration_days = ceil(Σ_i length_i / rate(damage_type_i))`（`PRODUCTION_RATE_M_PER_DAY = {"landslide_block": 100 m/日, "crack_pothole": 500 m/日}`、ユーザー確認済みの値）を追加し、`tabu_solver.py` の解表現を「ロットkがt日に処理される（1日限定）」から「ロットkがt日に**着手**し `t + duration_days - 1` 日まで1クルーを占有する」という資源制約スケジューリング（RCPSP風、`daily_capacity` は「ロット数/日」ではなく「同時稼働クルー数」に意味を変更）へ再設計した。再実行後、Recovery Benefit は Gurgaon 95.4%・Stuttgart 81.7%（地域ごとに異なる値）となり、3地域一致の人工物が解消されたことを確認した。あわせて `HORIZON_DAYS` を 14日→60日（大規模地震後の高速道路網復旧は現実的に数週間〜数ヶ月を要するため）に変更した。

Further Considerations
- 実際の地震動・斜面崩壊危険度データ（USGS ShakeMap 相当、インド地質調査所の landslide susceptibility map、IRC の地震ゾーニング等）の統合
  推奨: v0.3 は合成シナリオ生成器のままとし、`src/disaster_recovery/scenario.py` に `--damage-geojson` のような外部シナリオ差し替えフックを用意しておき、実データが入手できた時点で `generate_segment_damage` を差し替えるだけで済む構造にする。
- crack-only ロットを含めた親論文（Repair Lot Skyline WCSP）の Benefit/Cost 項（`wsoft*Rj + wrec*PatchCountj`）の再導入
  推奨: v0.4 でユーザーの目的関数仕様自体を拡張する形で対応し、v0.3 では SC_Loss / 施工距離のみのスコープを維持する。
- NSGA-II 等による SC_Loss と施工距離/コストの真の多目的 Pareto フロンティア探索（`pymoo` 等）
  推奨: v0.3 のタブーサーチ単一解＋事後フロンティアで最初のMVPを出し、ステークホルダーから「単一スケジュールでなく複数の運用点を比較したい」という要望が出た場合に v0.4 以降で追加する。
- 走行時間・迂回コストのモデル化（現状は到達可能性の二値＋距離閾値のみ）
  推奨: v0.3 スコープ外とし、既存の `H_CHAIN_ACCESS` 40km 閾値との整合を優先する（Decisions参照）。
- クルー容量 `DAILY_CREW_CAPACITY` の日次変動（立ち上がりの遅れ等の現実的なランプアップ）
  推奨: v0.3 は定数既定値 + 日ごと dict 上書きの仕組みだけ用意し、実運用パラメータの較正は行わない。
- 複数の被災シナリオ（モンテカルロ）での頑健性評価
  推奨: v0.3 は単一の固定シードシナリオのみとし（再現性最優先）、複数シード平均化は v0.4 以降の拡張候補とする。
