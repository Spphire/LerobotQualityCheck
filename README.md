# LerobotQualityCheckPlatform

一个面向 LeRobot v2 风格数据集的轻量人工质检平台，用来快速筛出明显错误的 episode。

当前默认数据集：

```text
/mnt/nm_dataset/dataset/giftbox_0628_1912episodes
```

## 功能概览

- 自动扫描 LeRobot 数据集的 `meta/episodes.jsonl`、`data/chunk-*` 和 `videos/chunk-*`。
- 左侧 episode 列表支持状态筛选、模糊搜索和分页。
- 右侧主区域展示：
  - 左右手 3D 轨迹，可拖动视角。
  - 左右手局部坐标轴姿态。
  - 当前视频时间点对应的 3D 高亮点和短尾迹。
  - 左右手夹爪曲线，固定 y 轴范围 `0-0.1`。
  - 左腕、头部、右腕三路视频速播，同步进度，默认 `10x` 循环播放。
- 点击夹爪曲线可打开对应时间点的腕部视频弹窗；弹窗默认暂停、`1x` 播放。
- 多用户并行质检：
  - episode 状态是全局结果，一个用户标完后所有用户可见。
  - 每条标注记录保留 `user` / `annotator`。
  - 正在查看的 episode 会显示其他用户占用锁。
  - 列表和当前 episode 状态约每 2 秒同步一次。
- 导出全局标注结果为 JSONL 或 CSV，可用于按 `episode_index` / `status` / `annotator` 筛选数据。

## 快捷键

方向键是全局快捷键，不会控制下拉框、搜索框或进度条：

| 按键 | 行为 |
|---|---|
| `←` | 状态向左切换，最多停在 `拒绝` |
| `→` | 状态向右切换，最多停在 `接收` |
| `↑` | 切到当前筛选列表中的上一个可用 episode |
| `↓` | 切到当前筛选列表中的下一个可用 episode |
| `R` | 标为拒绝 |
| `P` | 标为待审 |
| `A` | 标为接收 |
| `Space` | 播放 / 暂停三路速播视频 |
| `Esc` | 关闭腕部视频弹窗 |

上下切换只在当前状态筛选、搜索词和页码对应的列表中移动，并跳过其他用户锁住的 episode。

## 启动

在服务器上：

```bash
cd /mnt/LerobotQualityCheckPlatform
HOST=0.0.0.0 PORT=18080 ./run.sh
```

指定数据集：

```bash
cd /mnt/LerobotQualityCheckPlatform
DATASET_PATH=/mnt/nm_dataset/dataset/giftbox_0628_1912episodes \
HOST=0.0.0.0 PORT=18080 ./run.sh
```

启用访问 token：

```bash
cd /mnt/LerobotQualityCheckPlatform
LQCP_TOKEN='replace-with-a-secret' HOST=0.0.0.0 PORT=18080 ./run.sh
```

然后打开：

```text
http://<server-ip>:18080
```

可通过 URL 参数指定用户、数据集或 token：

```text
http://<server-ip>:18080/?user=chenwendi
http://<server-ip>:18080/?dataset=/mnt/path/to/dataset
http://<server-ip>:18080/?token=replace-with-a-secret
```

## 原始数据路径配置

平台会根据 episode UUID 到原始数据目录中读取：

```text
<raw_root>/<episode_uuid>/preprocessed/metadata.json
```

这个元数据用于解析采集人 `collector`，支持拒绝动画、`/rank` 采集人拒绝率排行和采集人复盘列表。它不影响 LeRobot 数据集本身的读取路径。

推荐使用 `LQCP_RAW_EPISODE_ROOTS` 配置一个或多个原始数据根目录，多个路径用英文逗号或分号分隔，服务会按顺序查找：

```bash
cd /mnt/LerobotQualityCheckPlatform
LQCP_RAW_EPISODE_ROOTS=/mnt/nm_data/data/nedf3,/mnt/nm_data/data/midtrain \
HOST=0.0.0.0 PORT=18080 ./run.sh
```

如果以后从 `nedf3` 切到新的原始数据根，只需要改这个环境变量并重启服务：

```bash
LQCP_RAW_EPISODE_ROOTS=/mnt/nm_data/data/nedf4,/mnt/nm_data/data/midtrain \
HOST=0.0.0.0 PORT=18080 ./run.sh
```

兼容旧配置：

```bash
LQCP_RAW_NEDF_ROOT=/mnt/nm_data/data/nedf
LQCP_RAW_MIDTRAIN_ROOT=/mnt/nm_data/data/midtrain
```

当 `LQCP_RAW_EPISODE_ROOTS` 已设置时，旧的 `LQCP_RAW_NEDF_ROOT` / `LQCP_RAW_MIDTRAIN_ROOT` 会被忽略。

服务会把当前 raw roots 签名写入 collector 缓存。以后调整 `LQCP_RAW_EPISODE_ROOTS` 并重启后，旧路径下的 collector 缓存会自动重新拉取，不需要手动清理 `labels.db`。

相关可调参数：

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `LQCP_RAW_EPISODE_ROOTS` | 空 | 推荐配置，逗号/分号分隔多个原始数据根路径 |
| `LQCP_RAW_NEDF_ROOT` | `/mnt/nm_data/data/nedf` | 旧版 NEDF 根路径 |
| `LQCP_RAW_MIDTRAIN_ROOT` | `/mnt/nm_data/data/midtrain` | 旧版 midtrain 根路径 |
| `LQCP_RAW_METADATA_TIMEOUT` | `3` | 单个 metadata 读取超时时间，单位秒 |
| `LQCP_COLLECTOR_CACHE_WORKERS` | `3` | 后台采集人缓存并发数 |
| `LQCP_COLLECTOR_CACHE_NEGATIVE_TTL` | `86400` | 未命中或缺少 collector 的缓存重试间隔，单位秒 |

可用健康接口确认服务当前使用的原始数据根路径：

```bash
curl -fsS http://127.0.0.1:18080/api/health
```

## 数据集格式

期望的数据集结构：

```text
dataset_root/
  meta/
    info.json
    episodes.jsonl
    tasks.jsonl
  data/
    chunk-000/
      episode_000000.parquet
  videos/
    chunk-000/
      observation.images.image/
        episode_000000.mp4
      observation.images.wrist_image_1/
        episode_000000.mp4
      observation.images.wrist_image_2/
        episode_000000.mp4
```

轨迹默认从 parquet 中读取：

- `observation.state`
- `observation.extra.left.raw_pose`
- `observation.extra.right.raw_pose`
- `observation.extra.ego.raw_pose`
- `observation.extra.left.hand_state`
- `observation.extra.right.hand_state`

四元数顺序按当前数据集元数据为：

```text
quat_w, quat_x, quat_y, quat_z
```

`raw_pose` 解析为：

```text
[x, y, z, qw, qx, qy, qz]
```

当前数据的绝对坐标中，`Y` 轴表示重力上下方向。

## 标注结果

标注结果保存在平台目录下，不会写回原数据集：

```text
/mnt/LerobotQualityCheckPlatform/qc_results/<dataset_id>/labels.json
/mnt/LerobotQualityCheckPlatform/qc_results/<dataset_id>/labels.jsonl
```

当前 schema：

```json
{
  "schema_version": 3,
  "dataset_path": "/mnt/nm_dataset/dataset/giftbox_0628_1912episodes",
  "dataset_id": "giftbox_0628_1912episodes-09034dca98e3",
  "updated_at": "2026-06-29T10:00:00+00:00",
  "labels": {
    "0": {
      "dataset_id": "giftbox_0628_1912episodes-09034dca98e3",
      "dataset_path": "/mnt/nm_dataset/dataset/giftbox_0628_1912episodes",
      "user": "chenwendi",
      "annotator": "chenwendi",
      "episode_index": 0,
      "episode_name": "episode_000000",
      "episode_uuid": "...",
      "status": "accept",
      "issues": [],
      "note": "",
      "updated_at": "2026-06-29T10:00:00+00:00"
    }
  },
  "labels_by_user": {
    "chenwendi": {
      "0": {
        "episode_index": 0,
        "status": "accept"
      }
    }
  }
}
```

说明：

- `labels` 是全局最终结果，一集只保留一条当前状态。
- `labels_by_user` 保留用户操作记录，用于统计“我的已标”。
- 状态取值为 `reject`、`pending`、`accept`。
- `labels.jsonl` 和导出 JSONL 都是一行一个 episode 的全局最终结果，适合脚本筛选。

## 导出

页面左侧提供：

- `导出 JSONL`
- `导出 CSV`

也可以直接访问接口：

```text
/api/export.jsonl
/api/export.csv
```

CSV / JSONL 中包含：

```text
dataset_id, dataset_path, user, annotator, episode_index, episode_name,
episode_uuid, status, issues, note, updated_at, length,
task_description, task_annotation
```

## 清空重标

清空前建议备份：

```bash
cd /mnt/LerobotQualityCheckPlatform/qc_results/<dataset_id>
ts=$(date +%Y%m%d_%H%M%S)
cp -a labels.json labels.json.bak_$ts
cp -a labels.jsonl labels.jsonl.bak_$ts
```

然后写入空标注文件或删除 `labels.json` / `labels.jsonl` 后重启服务。

## 开发说明

项目没有前端构建步骤，直接由 Python 标准库 HTTP server 提供静态文件和 API。

主要文件：

```text
server.py              后端 API、媒体服务、标注存储
run.sh                 Linux 启动脚本
web/index.html         页面结构
web/styles.css         样式
web/app.js             前端交互和可视化
web/vendor/plotly.min.js
```

本地语法检查：

```bash
python3 -m py_compile server.py
node --check web/app.js
```

## Git 同步

仓库地址：

```text
git@github.com:Spphire/LerobotQualityCheck.git
```
