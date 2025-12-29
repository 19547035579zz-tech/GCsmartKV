from common import *
import random
from collections import defaultdict


class ShardGCScheduler:
    def __init__(self, node_ids: List[str], shard_num: int = DEFAULT_SHARD_NUM):
        self.shard_num = shard_num
        # 分片-GC任务双射映射：shard_id → GCTask（论文4.2节核心）
        self.shard_gc_map: Dict[int, GCTask] = {}
        # 主备分片映射：shard_id → (primary_node_id, backup_node_id)
        self.shard_node_map: Dict[int, Tuple[str, str]] = self._init_shard_node_map(node_ids)
        # 节点任务列表：node_id → List[GCTask]
        self.node_tasks: Dict[str, List[GCTask]] = defaultdict(list)
        # 增量Raft同步缓存（仅同步元数据增量，论文4.4节）
        self.raft_sync_buffer: List[Metadata] = []
        self.raft_batch_threshold = 8  # 批量提交阈值（8个请求，论文4.4节）

    def _init_shard_node_map(self, node_ids: List[str]) -> Dict[int, Tuple[str, str]]:
        """初始化分片-节点映射（主备机制，论文4.2节）"""
        shard_map = {}
        for shard_id in range(self.shard_num):
            # 随机分配主备节点（实际部署需按负载均衡）
            primary = random.choice(node_ids)
            backup = random.choice([n for n in node_ids if n != primary])
            shard_map[shard_id] = (primary, backup)
        return shard_map

    def create_gc_task(self, blob: BlobFile) -> GCTask:
        """创建GC任务，绑定分片（双射模型：1分片→1任务）"""
        shard_id = blob.shard_id
        if shard_id in self.shard_gc_map and self.shard_gc_map[shard_id].status != GCTask.status.COMPLETED:
            raise ValueError(f"Shard {shard_id} already has an active GC task")
        
        # 生成任务ID（分片ID+时间戳，确保唯一性）
        task_id = f"GC-{shard_id}-{int(time.time() * 1000)}"
        primary_node, backup_node = self.shard_node_map[shard_id]
        
        # 计算任务优先级权重（论文4.5节：按垃圾比率分级）
        if blob.garbage_ratio >= 0.7:
            priority = 1.0
        elif 0.3 <= blob.garbage_ratio < 0.7:
            priority = 0.7
        else:
            priority = 0.4
        
        # 创建GC任务
        task = GCTask(
            task_id=task_id,
            shard_id=shard_id,
            primary_node_id=primary_node,
            backup_node_id=backup_node,
            target_blob=blob,
            status=GCTask.status.PENDING,
            priority_weight=priority,
            garbage_ratio=blob.garbage_ratio
        )
        
        # 绑定双射关系
        self.shard_gc_map[shard_id] = task
        self.node_tasks[primary_node].append(task)
        return task

    def interrupt_task(self, task: GCTask, processed_offset: int, valid_checksum: str, metadata_updated: bool):
        """中断任务并保存快照（论文4.3节断点续跑）"""
        task.save_snapshot(processed_offset, valid_checksum, metadata_updated)
        # 切换到备份节点（主备接管）
        backup_node = task.backup_node_id
        self.node_tasks[backup_node].append(task)
        self.node_tasks[task.primary_node_id].remove(task)

    def resume_task(self, task: GCTask) -> int:
        """恢复中断的任务（论文4.3节）"""
        return task.resume_from_snapshot()

    def add_raft_sync_metadata(self, meta: Metadata):
        """添加元数据到Raft同步缓存（增量同步，论文4.4节）"""
        self.raft_sync_buffer.append(meta)
        # 达到批量阈值，触发同步
        if len(self.raft_sync_buffer) >= self.raft_batch_threshold:
            self._batch_sync_raft()

    def _batch_sync_raft(self):
        """批量Raft同步（模拟，实际需集成Raft库如etcdraft）"""
        print(f"[Raft Sync] Batch sync {len(self.raft_sync_buffer)} metadata entries")
        # 模拟压缩（LZ4，压缩比5:1，论文4.4节）
        compressed_size = len(self.raft_sync_buffer) // 5
        print(f"[Raft Sync] Original size: {len(self.raft_sync_buffer)}, Compressed size: {compressed_size}")
        self.raft_sync_buffer.clear()