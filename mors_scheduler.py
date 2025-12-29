from common import *
from collections import defaultdict


class MORSScheduler:
    def __init__(self, node_ids: List[str]):
        self.node_resources: Dict[str, NodeResource] = self._init_node_resources(node_ids)
        # 任务配额：node_id → (high_priority_quota, low_priority_quota)
        self.task_quota: Dict[str, Tuple[int, int]] = defaultdict(lambda: (15, 10))  # 初始配额：高15，低10
        self.resource_competition_threshold = (0.4, 0.7)  # 资源竞争度阈值（低<0.4，高>0.7，论文4.6节）

    def _init_node_resources(self, node_ids: List[str]) -> Dict[str, NodeResource]:
        """初始化节点资源状态（FPGA利用率默认60%，带宽50%）"""
        resources = {}
        for node_id in node_ids:
            resources[node_id] = NodeResource(
                node_id=node_id,
                fpga_utilization=60.0,
                bandwidth_utilization=50.0,
                remaining_clb=1000  # 假设初始1000个CLB
            )
        return resources

    def sort_tasks_by_profit(self, tasks: List[GCTask]) -> List[GCTask]:
        """MORS任务排序：高收益优先（论文4.5节）"""
        # 收益值≥0.7为高优先级，优先调度
        high_priority = [t for t in tasks if t.calculate_profit() >= 0.7]
        low_priority = [t for t in tasks if t.calculate_profit() < 0.7]
        # 按收益值降序排序
        high_priority.sort(key=lambda x: x.calculate_profit(), reverse=True)
        low_priority.sort(key=lambda x: x.calculate_profit(), reverse=True)
        return high_priority + low_priority

    def select_best_node(self, task: GCTask) -> Optional[str]:
        """资源筛选：选择负载最低的可用节点（论文4.6节）"""
        # 筛选可用节点（满足硬件约束）
        available_nodes = [n for n in self.node_resources.values() if n.is_available()]
        if not available_nodes:
            return self._preempt_low_priority_resource(task)  # 无可用节点，抢占低优先级资源
        
        # 计算节点负载（FPGA利用率+带宽利用率，论文4.6节）
        available_nodes.sort(key=lambda x: x.fpga_utilization + x.bandwidth_utilization)
        best_node = available_nodes[0]
        # 更新节点资源（模拟任务分配后的负载增长）
        best_node.fpga_utilization += 5.0  # 假设任务占用5% FPGA
        best_node.bandwidth_utilization += 4.0  # 占用4%带宽
        best_node.remaining_clb -= 20  # 占用20个CLB
        # 计算资源竞争度（负载/100，论文4.6节）
        best_node.resource_competition = (best_node.fpga_utilization + best_node.bandwidth_utilization) / 200
        self._adjust_quota(best_node.node_id)  # 动态调整配额
        return best_node.node_id

    def _preempt_low_priority_resource(self, task: GCTask) -> Optional[str]:
        """抢占低优先级任务资源（仅抢占权重<0.5的任务，论文4.6节）"""
        for node_id, tasks in self.node_tasks.items():  # 需传入全局任务列表，此处简化
            low_priority_tasks = [t for t in tasks if t.priority_weight < 0.5]
            if low_priority_tasks:
                # 抢占第一个低优先级任务
                preempted_task = low_priority_tasks[0]
                preempted_task.status = GCTask.status.INTERRUPTED
                tasks.remove(preempted_task)
                print(f"[Preempt] Node {node_id}: Preempt low-priority task {preempted_task.task_id} for {task.task_id}")
                return node_id
        return None

    def _adjust_quota(self, node_id: str):
        """动态调整任务配额（论文4.6节）"""
        competition = self.node_resources[node_id].resource_competition
        high_quota, low_quota = self.task_quota[node_id]
        if competition > self.resource_competition_threshold[1]:
            # 竞争度高，降低低优先级配额20%
            new_low = int(low_quota * 0.8)
            self.task_quota[node_id] = (high_quota, new_low)
            print(f"[Quota Adjust] Node {node_id}: Low quota {low_quota}→{new_low} (competition={competition:.2f})")
        elif competition < self.resource_competition_threshold[0]:
            # 竞争度低，提高低优先级配额20%
            new_low = int(low_quota * 1.2)
            self.task_quota[node_id] = (high_quota, new_low)
            print(f"[Quota Adjust] Node {node_id}: Low quota {low_quota}→{new_low} (competition={competition:.2f})")

    def update_hardware_virtualization(self, node_id: str, load_level: str):
        """动态硬件虚拟化（负载高峰1:6，低谷1:4，论文4.7节）"""
        if load_level == "HIGH":
            virtual_ratio = 6  # 1个物理CLB虚拟为6个逻辑单元
        elif load_level == "LOW":
            virtual_ratio = 4
        else:
            virtual_ratio = 5  # 默认1:5
        # 计算虚拟后可用CLB（实际需FPGA硬件支持，此处模拟）
        physical_clb = 1000 - self.node_resources[node_id].remaining_clb
        virtual_clb = physical_clb * virtual_ratio
        print(f"[Virtualization] Node {node_id}: Ratio 1:{virtual_ratio}, Virtual CLB: {virtual_clb}")