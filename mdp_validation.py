from common import *
import numpy as np


class MDPValidationModel:
    def __init__(self):
        # MDP四要素（论文4.8节）
        # 1. 状态空间：(meta_type: 0=NORMAL/1=GC, is_validated:0/1, delay_range:0=0-50ms/1=50-100ms/2=100-150ms)
        self.states = [(mt.value, iv, dr) for mt in Metadata.meta_type for iv in [0, 1] for dr in [0, 1, 2]]
        self.state_idx = {s: i for i, s in enumerate(self.states)}
        self.state_num = len(self.states)
        
        # 2. 动作空间：0=写前验证，1=读时验证，2=合并时验证（论文4.8节）
        self.actions = [0, 1, 2]
        self.action_names = ["Write-Before", "Read-Time", "Merge-Time"]
        
        # 3. 转移概率P(s'|s,a)：基于1e6次实验统计（论文4.8节，模拟值）
        self.transition_prob = self._init_transition_prob()
        
        # 4. 奖励函数R(s,a)：延迟权重0.6，破坏概率权重0.4（论文4.8节）
        self.rewards = self._init_rewards()
        
        # 价值函数与策略
        self.value = np.zeros(self.state_num)
        self.policy = np.zeros(self.state_num, dtype=int)
        self.discount_factor = 0.9  # 折扣因子

    def _init_transition_prob(self) -> np.ndarray:
        """初始化转移概率（模拟论文4.8节1e6次实验统计结果）"""
        P = np.zeros((self.state_num, len(self.actions), self.state_num))
        for s_idx, s in enumerate(self.states):
            mt, iv, dr = s
            for a in self.actions:
                # 简化逻辑：验证后状态变为“已验证”，延迟范围根据动作调整
                new_iv = 1  # 执行验证动作后，元数据变为已验证
                if a == 0:  # 写前验证：延迟升高（如0→1，1→2）
                    new_dr = min(dr + 1, 2)
                elif a == 1:  # 读时验证：延迟不变
                    new_dr = dr
                else:  # 合并时验证：延迟降低（如1→0，2→1）
                    new_dr = max(dr - 1, 0)
                new_s = (mt, new_iv, new_dr)
                new_s_idx = self.state_idx[new_s]
                P[s_idx, a, new_s_idx] = 1.0  # 确定性转移（模拟）
        return P

    def _init_rewards(self) -> np.ndarray:
        """初始化奖励函数（论文4.8节：延迟权重0.6，破坏概率权重0.4）"""
        R = np.zeros((self.state_num, len(self.actions)))
        # 延迟成本（ms）：写前验证4.0，读时验证1.0，合并时验证2.0（论文4.9节）
        delay_cost = {0: 4.0, 1: 1.0, 2: 2.0}
        # 一致性破坏概率（%）：写前验证0.0，读时验证0.1，合并时验证0.05（论文4.8节）
        error_prob = {0: 0.0, 1: 0.1, 2: 0.05}
        
        for s_idx, s in enumerate(self.states):
            mt, iv, dr = s
            for a in self.actions:
                # 奖励 = - (0.6*延迟成本 + 0.4*破坏概率)（负成本即奖励）
                reward = - (0.6 * delay_cost[a] + 0.4 * error_prob[a])
                R[s_idx, a] = reward
        return R

    def value_iteration(self, max_iter=50, threshold=1e-4):
        """价值迭代求解最优策略（论文4.8节，迭代50次收敛）"""
        for _ in range(max_iter):
            value_diff = 0.0
            for s_idx in range(self.state_num):
                old_value = self.value[s_idx]
                # 计算每个动作的期望价值
                action_values = []
                for a in self.actions:
                    exp_value = np.sum(self.transition_prob[s_idx, a] * (self.rewards[s_idx, a] + self.discount_factor * self.value))
                    action_values.append(exp_value)
                # 更新价值函数与策略
                self.value[s_idx] = max(action_values)
                self.policy[s_idx] = np.argmax(action_values)
                value_diff = max(value_diff, abs(old_value - self.value[s_idx]))
            if value_diff < threshold:
                print(f"[MDP] Value iteration converged at iter {_+1}")
                break

    def get_optimal_action(self, meta: Metadata) -> str:
        """根据元数据状态获取最优验证策略（论文4.8节）"""
        # 映射元数据到MDP状态
        mt = meta.meta_type.value
        iv = 1 if meta.is_validated else 0
        # 映射延迟范围（0:0-50ms，1:50-100ms，2:100-150ms）
        if meta.delay_range <= 50:
            dr = 0
        elif meta.delay_range <= 100:
            dr = 1
        else:
            dr = 2
        state = (mt, iv, dr)
        s_idx = self.state_idx[state]
        action = self.policy[s_idx]
        return self.action_names[action]

    def batch_validate_metadata(self, metas: List[Metadata]) -> List[Metadata]:
        """批量元数据验证优化（论文4.9节：1MB缓冲队列，按Key排序）"""
        # 按Key排序（顺序I/O占比提升至92%）
        sorted_metas = sorted(metas, key=lambda x: x.key)
        # 模拟验证（仅查最近1层SSTable，验证时间从0.5ms→0.4ms）
        for meta in sorted_metas:
            meta.is_validated = True
            meta.delay_range = 0.4  # 验证时间0.4ms
        print(f"[Batch Validation] Validated {len(sorted_metas)} metadata entries (sorted by Key)")
        return sorted_metas