# -*- coding: utf-8 -*-
"""结构化错误码体系：所有失败路径返回 {code, stage, message, next_action}。

code      —— 稳定机器错误码，Agent 据此判断错误类别
stage     —— 出错发生在哪个阶段
message   —— 脱敏人话说明
next_action —— Agent 下一步：STOP / QUERY / MANUAL_CHECK
"""
from dataclasses import dataclass, asdict
from typing import Optional

# next_action 枚举
NEXT_STOP = "STOP"
NEXT_QUERY = "QUERY"
NEXT_MANUAL = "MANUAL_CHECK"


@dataclass
class ErrorInfo:
    code: str
    stage: str
    message: str
    next_action: str = NEXT_STOP

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> Optional["ErrorInfo"]:
        if d is None:
            return None
        return cls(**d)


# ---- 错误码定义 ----

# 环境/契约/前置检查
ERR_CONTRACT_VERSION_MISMATCH = "CONTRACT_VERSION_MISMATCH"
ERR_ENVIRONMENT_MISMATCH = "ENVIRONMENT_MISMATCH"
ERR_EDGE_NOT_READY = "EDGE_NOT_READY"
ERR_NOT_LOGGED_IN = "NOT_LOGGED_IN"
ERR_EXECUTOR_BUSY = "EXECUTOR_BUSY"
ERR_FIELD_VALIDATION = "FIELD_VALIDATION"

# 幂等/执行记录
ERR_IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"  # 同幂等键已有执行记录（非错误，用于内部判断）
ERR_EXECUTION_NOT_FOUND = "EXECUTION_NOT_FOUND"
ERR_IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"

# 渠道操作
ERR_CHANNEL_QUERY_FAILED = "CHANNEL_QUERY_FAILED"
ERR_CHANNEL_CREATE_FAILED = "CHANNEL_CREATE_FAILED"
ERR_CHANNEL_VERIFY_FAILED = "CHANNEL_VERIFY_FAILED"

# 应用操作
ERR_TEMPLATE_NOT_FOUND = "TEMPLATE_NOT_FOUND"
ERR_SAVE_FAILED = "SAVE_FAILED"
ERR_PUBLISH_FAILED = "PUBLISH_FAILED"
ERR_GROUP_SET_FAILED = "GROUP_SET_FAILED"
ERR_FINAL_VERIFY_FAILED = "FINAL_VERIFY_FAILED"

# 恢复
ERR_STALE_RUNNING_EXECUTION = "STALE_RUNNING_EXECUTION"


def err(code: str, stage: str, message: str, next_action: str = NEXT_STOP) -> dict:
    """快捷构造错误字典。"""
    return {"code": code, "stage": stage, "message": message, "next_action": next_action}
