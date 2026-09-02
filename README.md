# 139云应用平台自动配链接编排层

这是 FastAPI 编排层项目，负责解析和管理批次任务，并直接调用现有自动化执行端：

```text
F:\卓望\配链接项目\codex\app\executor
```

## 启动

```powershell
cd F:\卓望\配链接项目\codex
uv sync
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## 当前接口

- `GET /api/health`：编排层健康检查
- `GET /api/status`：检查浏览器、登录、执行锁状态
- `POST /api/login`：触发自动登录
- `POST /api/channels`：创建渠道
- `POST /api/apps`：同步创建应用
- `POST /api/apps/async`：提交异步创建应用任务
- `GET /api/jobs`：查看任务列表
- `GET /api/jobs/{job_id}`：查看任务状态
- `POST /api/jobs/{job_id}/cancel`：取消尚未执行的任务
- `PATCH /api/apps`：修改应用
- `POST /api/apps/locate`：定位历史应用
- `GET /api/fields`：读取可修改字段列表

## 说明

- 现有执行端是同步阻塞调用，API 层已放入线程池执行，避免阻塞 FastAPI 事件循环。
- 图片文件后续可放到 `data/files`，通过 `/files/...` 暴露给执行端上传。
- SQLite 默认位置是 `data/orchestrator.sqlite3`。

## 任务恢复

- 任务状态、请求参数、执行阶段、重试次数和结果保存在 data/orchestrator.sqlite3 的 jobs 表。
- 后端重启后，尚未开始的 QUEUED / WAITING 任务会按原顺序恢复排队。
- 重启时仍为 RUNNING 的任务不会自动重跑，而是标记为 BACKEND_RESTARTED 失败并放行后续任务，避免重复创建应用。
- 自动化运行中的阶段变化会持续同步到 SQLite，前端仍通过 /api/jobs/{job_id} 查询。