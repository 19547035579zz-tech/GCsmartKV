import enum
import time
import hashlib
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

# 论文2.1节：Blob文件默认大小32MB
BLOB_DEFAULT_SIZE = 32 * 1024 * 1024  # 32MB
# 论文4.2节：默认分片数50
DEFAULT_SHARD_NUM = 50
# 论文4.3节：断点快照粒度1MB
SNAPSHOT_GRANULARITY = 1 * 1024 * 1024  # 1MB


@dataclass
class BlobFile:
    """模拟Blob文件（Value存储），对应论文2.1节价值区"""
    blob_id: str
    shard_id: int  # 绑定分片，双射模型核心
    size: int = BLOB_DEFAULT_SIZE
    is_valid: bool = True  # 是否为有效Blob（未被GC标记）
    value_count: int = 0  # 存储的Value数量
    garbage_ratio: float = 0.0  # 垃圾比率（无效Value占比）
    create_time: float = time.time()

    def calculate_garbage_ratio(self, valid_value_count: int):
        """计算垃圾比率，对应论文5.2节分片验证逻辑"""
        if self.value_count == 0:
            self.garbage_ratio = 0.0
        else:
            self.garbage_ratio = 1.0 - (valid_value_count / self.value_count)
        return self.garbage_ratio


@dataclass
class GCTaskSnapshot:
    """GC任务断点快照，对应论文4.3节断点续跑协议"""
    task_id: str
    shard_id: int
    blob_id: str
    processed_offset: int  # 已处理偏移量（快照粒度1MB）
    valid_checksum: str  # 有效数据校验和
    metadata_updated: bool  # 元数据是否已更新
    snapshot_time: float = time.time()


@dataclass
class GCTask:
    """GC任务，对应论文4.2节分片-GC双射模型"""
    task_id: str
    shard_id: int  # 绑定分片（双射核心：1分片→1任务）
    primary_node_id: str  # 主节点（主备机制）
    backup_node_id: str  # 备份节点
    target_blob: BlobFile
    status: enum.Enum = enum.Enum("TaskStatus", ["PENDING", "RUNNING", "INTERRUPTED", "COMPLETED"])
    current_snapshot: Optional[GCTaskSnapshot] = None  # 断点快照
    priority_weight: float = 0.0  # MORS算法：任务优先级权重（论文4.5节）
    garbage_ratio: float = 0.0  # 目标Blob垃圾比率

    def calculate_profit(self) -> float:
        """MORS算法：计算任务收益值=优先级权重×垃圾比率（论文4.5节）"""
        return self.priority_weight * self.garbage_ratio

    def save_snapshot(self, processed_offset: int, valid_checksum: str, metadata_updated: bool):
        """保存断点快照，对应论文4.3节"""
        self.current_snapshot = GCTaskSnapshot(
            task_id=self.task_id,
            shard_id=self.shard_id,
            blob_id=self.target_blob.blob_id,
            processed_offset=processed_offset,
            valid_checksum=valid_checksum,
            metadata_updated=metadata_updated
        )
        self.status = GCTask.status.INTERRUPTED

    def resume_from_snapshot(self) -> Optional[int]:
        """从断点恢复，返回已处理偏移量"""
        if not self.current_snapshot:
            return 0  # 无快照，从头开始
        self.status = GCTask.status.RUNNING
        return self.current_snapshot.processed_offset


@dataclass
class NodeResource:
    """节点资源状态，对应MORS算法资源筛选（论文4.6节）"""
    node_id: str
    fpga_utilization: float  # FPGA利用率（%）
    bandwidth_utilization: float  # 带宽利用率（%）
    remaining_clb: int  # 剩余FPGA逻辑单元（CLB）
    resource_competition: float = 0.0  # 资源竞争度（0-1，论文4.6节）

    def is_available(self) -> bool:
        """判断节点是否可用（硬件约束：FPGA≤90%，带宽≤88%，论文4.6节）"""
        return self.fpga_utilization <= 90.0 and self.bandwidth_utilization <= 88.0


@dataclass
class Metadata:
    """元数据（Key+索引），对应论文2.1节元数据区"""
    key: str
    blob_id: str  # 指向Value所在的Blob文件
    offset: int  # Value在Blob中的偏移量
    is_validated: bool  # 是否已验证（MDP延迟验证，论文4.8节）
    meta_type: enum.Enum = enum.Enum("MetaType", ["NORMAL", "GC"])  # 元数据类型
    delay_range: float = 0.0  # 延迟范围（ms，论文4.8节MDP状态）