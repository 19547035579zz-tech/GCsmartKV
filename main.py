from shard_gc_scheduler import ShardGCScheduler
from mors_scheduler import MORSScheduler
from mdp_validation import MDPValidationModel
from fpga_pipeline import FPGADynamicPipeline
from common import *
import random


def generate_ycsb_write_load(shard_num: int, blob_num_per_shard: int) -> List[BlobFile]:
    """生成YCSB写密集负载（94%写，对应论文5.7节Twitter cluster39）"""
    blobs = []
    for shard_id in range(shard_num):
        for _ in range(blob_num_per_shard):
            blob_id = f"Blob-{shard_id}-{int(time.time() * 1000)}-{random.randint(0, 1000)}"
            # 模拟写密集场景下的垃圾比率（30%-80%）
            garbage_ratio = random.uniform(0.3, 0.8)
            value_count = random.randint(1000, 5000)  # 每个Blob含1000-5000个Value
            valid_count = int(value_count * (1 - garbage_ratio))
            
            blob = BlobFile(
                blob_id=blob_id,
                shard_id=shard_id,
                value_count=value_count,
                garbage_ratio=garbage_ratio
            )
            blobs.append(blob)
    print(f"[Load Generation] Generated {len(blobs)} Blobs (YCSB Write-Intensive Load)")
    return blobs


def main():
    # 1. 初始化系统组件
    node_ids = ["Node-1", "Node-2", "Node-3", "Node-4", "Node-5"]  # 5节点集群（论文5.1节）
    shard_gc_scheduler = ShardGCScheduler(node_ids, shard_num=50)
    mors_scheduler = MORSScheduler(node_ids)
    mdp_model = MDPValidationModel()
    fpga_pipeline = FPGADynamicPipeline()

    # 2. MDP模型训练（价值迭代求解最优策略）
    mdp_model.value_iteration()

    # 3. 生成实验负载（YCSB写密集负载，论文5.7节）
    blobs = generate_ycsb_write_load(shard_num=50, blob_num_per_shard=10)  # 500个Blob

    # 4. 创建并调度GC任务
    for blob in blobs:
        # 4.1 创建GC任务（双射模型绑定分片）
        try:
            gc_task = shard_gc_scheduler.create_gc_task(blob)
        except ValueError:
            continue  # 分片已存在活跃任务，跳过
        
        # 4.2 MORS任务排序与资源筛选
        sorted_tasks = mors_scheduler.sort_tasks_by_profit([gc_task])
        target_node = mors_scheduler.select_best_node(sorted_tasks[0])
        if not target_node:
            print(f"[Task Scheduling] No available node for task {gc_task.task_id}, skip")
            continue
        print(f"[Task Scheduling] Assign task {gc_task.task_id} (shard {gc_task.shard_id}) to node {target_node}")

        # 4.3 模拟任务执行（FPGA处理+断点续跑）
        gc_task.status = GCTask.status.RUNNING
        # 模拟处理Blob：按1MB快照粒度（论文4.3节）
        total_size = blob.size
        processed_offset = 0
        while processed_offset < total_size:
            # 处理1MB数据
            chunk_size = min(SNAPSHOT_GRANULARITY, total_size - processed_offset)
            # 模拟生成Value数据（80B小Value，论文5.7节）
            value = struct.pack(">I", 80) + b"test_value_" * 10  # 4B长度+80B数据
            encoded_value, _ = fpga_pipeline.process_value(value, blob.blob_id)
            
            # 模拟随机中断（10%概率）
            if random.random() < 0.1:
                # 计算已处理数据校验和
                checksum = hashlib.md5(encoded_value).hexdigest()
                # 中断任务并保存快照
                shard_gc_scheduler.interrupt_task(gc_task, processed_offset, checksum, metadata_updated=False)
                print(f"[Task Interrupt] Task {gc_task.task_id} interrupted at offset {processed_offset}")
                # 恢复任务
                processed_offset = shard_gc_scheduler.resume_task(gc_task)
                print(f"[Task Resume] Task {gc_task.task_id} resumed from offset {processed_offset}")
            
            processed_offset += chunk_size

        # 4.4 元数据一致性验证（MDP最优策略）
        meta = Metadata(
            key=f"key-{blob.blob_id}",
            blob_id=blob.blob_id,
            offset=processed_offset,
            is_validated=False,
            meta_type=Metadata.meta_type.GC,
            delay_range=random.uniform(50, 100)  # 延迟50-100ms
        )
        # 获取MDP最优验证策略
        optimal_action = mdp_model.get_optimal_action(meta)
        print(f"[MDP Validation] Optimal action for meta {meta.key}: {optimal_action}")
        # 批量验证元数据（论文4.9节优化）
        validated_metas = mdp_model.batch_validate_metadata([meta])
        # 增量Raft同步元数据
        for vm in validated_metas:
            shard_gc_scheduler.add_raft_sync_metadata(vm)

        # 4.5 任务完成
        gc_task.status = GCTask.status.COMPLETED
        print(f"[Task Complete] Task {gc_task.task_id} completed, Blob {blob.blob_id} GC finished\n")

    # 5. 输出实验统计
    total_tasks = len([t for tasks in shard_gc_scheduler.node_tasks.values() for t in tasks])
    completed_tasks = len([t for tasks in shard_gc_scheduler.node_tasks.values() for t in tasks if t.status == GCTask.status.COMPLETED])
    interrupted_tasks = len([t for tasks in shard_gc_scheduler.node_tasks.values() for t in tasks if t.status == GCTask.status.INTERRUPTED])
    print(f"[Experiment Summary] Total Tasks: {total_tasks}, Completed: {completed_tasks}, Interrupted: {interrupted_tasks}")
    print(f"[Experiment Summary] Interrupt Rate: {interrupted_tasks/total_tasks*100:.2f}% (target ≤2.3%, 论文4.3节)")


if __name__ == "__main__":
    main()