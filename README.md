# LightTrace Server v0.2.0

这是 LightTrace 未来统一仓库中的服务端基础骨架。当前只深化 GPS 模块；Light、Questionnaire、Cross-modal QC、Admin integration 仅保留命名空间，不实现业务逻辑。

## 当前数据链

OwnTracks → HTTPS/Caddy → FastAPI → authentication → SQLite + append-only JSONL mirror → QC/export

## 仓库建议

```text
LightTrace/
├── server/                  # 本包：云端与本地均可运行的后端
│   ├── app/
│   │   ├── core/
│   │   └── modules/
│   │       ├── gps/         # v0.2 已实现
│   │       ├── light/       # reserved
│   │       ├── questionnaire/ # reserved
│   │       ├── qc/          # reserved
│   │       └── admin/       # reserved
│   ├── scripts/
│   └── data/                # 永远不提交 Git
├── docs/
├── docker-compose.yml
└── Caddyfile
```

因此 GPS 是主仓库中的一个模块，不是未来难以合并的独立小项目。

## v0.2 相比原始 40 行接收器新增

- OwnTracks HTTP Basic Auth：participant ID + individual secret
- FastAPI
- SQLite WAL 持久化
- 原始 payload 永久保存于 SQLite `raw_json`
- 额外 append-only 日级 JSONL mirror
- OwnTracks `_id` / payload hash 去重
- `recorded_at` / `created_at` / `received_at` 三时间轴
- location/status 等 message type 原样保存；location 单独标准化
- `/health` 公共无敏感坐标诊断
- Admin Bearer Token
- GPS status/QC
- CSV export
- Docker + Caddy HTTPS
- Windows 本地开发路径
- 阿里云香港测试开服操作路径
- Legacy JSONL importer

## 首先阅读

1. `docs/01_WINDOWS_LOCAL_DEVELOPMENT.md`
2. `docs/02_ALIBABA_CLOUD_ONE_STOP.md`

## 安全边界

- `.env`、participant secrets、SQLite、raw JSONL 都不得进入 Git。
- `/health` 不返回坐标和 participant ID。
- FastAPI 8000 端口不要对公网开放；公网只通过 Caddy 80/443。
- 当前是工程 prototype，不等于正式研究的数据治理方案。
