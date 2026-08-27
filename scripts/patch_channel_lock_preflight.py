from pathlib import Path

p = Path(r"F:\卓望\配链接项目\codex\app\api\routes.py")
s = p.read_text(encoding="utf-8")
old = '''@router.post("/channels")
async def create_channel(payload: ChannelCreateRequest) -> dict:
    try:
        result = await run_in_threadpool(
            call_executor,
            "create_channel",
            channel_base_name=payload.channel_base_name,
            base_platform=payload.base_platform,
        )
    except Exception as exc:
        return executor_error(f"创建渠道调用异常: {exc}")

    save_operation("CREATE_CHANNEL", result, payload.batch_id)
    if result.get("success"):
        save_channel(result)
    return result
'''
new = '''@router.post("/channels")
async def create_channel(payload: ChannelCreateRequest) -> dict:
    lock = get_executor_lock_status()
    if lock.get("locked") and lock.get("stale_candidate"):
        release_executor_lock("创建渠道前发现执行端旧锁超过阈值，自动释放。")
    elif lock.get("locked"):
        return {
            "success": False,
            "message": f"执行端正在处理 {lock.get('holder')}，请等待当前任务结束后再创建渠道。当前阶段：{lock.get('stage') or '未知'}",
            "error_code": "EXECUTOR_BUSY",
            "error_stage": "LOCK",
            "next_action": "QUERY",
            "lock": lock,
        }

    try:
        result = await run_in_threadpool(
            call_executor,
            "create_channel",
            channel_base_name=payload.channel_base_name,
            base_platform=payload.base_platform,
        )
    except Exception as exc:
        return executor_error(f"创建渠道调用异常: {exc}")

    save_operation("CREATE_CHANNEL", result, payload.batch_id)
    if result.get("success"):
        save_channel(result)
    return result
'''
if old not in s:
    raise SystemExit("target block not found")
p.write_text(s.replace(old, new), encoding="utf-8")
