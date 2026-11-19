"""面级加工特征识别的图神经网络。

实现选择说明（写进论文要能自圆其说）：

1. **不用 PyG**。PyG 对 torch/CUDA 版本有硬绑定，装错一次整条链就崩，
   而论文要求「第三人能在 2 小时内跑通」。这里的三层消息传递用
   index_add 手写，总共不到 60 行，行为完全可见。

2. **用 GraphSAGE 而不是 GCN**。GCN 是 transductive 的，学到的节点表示
   绑死在训练图的编号上；GraphSAGE 学的是聚合函数，能直接推没见过的零件。
   对我们更关键的是第二点：GraphSAGE 每层都显式保留节点自身特征，
   而 GCN 的对称归一化会把自身信息稀释进邻域均值 —— 少数类
   （比如 slanted_through_step）恰恰靠局部几何区分，一稀释就错。

3. **边特征参与消息**。凸凹性和二面角是判别特征类型的主要依据，
   丢掉它们等于把最重要的信息降级成拓扑结构。
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

NUM_CLASSES = 25
NODE_DIM = 18
EDGE_DIM = 4


class SAGELayer(nn.Module):
    """单层：h' = ReLU(W_self·h + mean_{u∈N(v)} W_msg·[h_u ‖ e_uv])"""

    def __init__(self, in_dim, out_dim, edge_dim=EDGE_DIM, dropout=0.2):
        super().__init__()
        self.self_lin = nn.Linear(in_dim, out_dim)
        self.msg_lin = nn.Linear(in_dim + edge_dim, out_dim)
        self.norm = nn.LayerNorm(out_dim)
        self.dropout = dropout

    def forward(self, x, edge_index, edge_attr):
        # 消融时边被清空，退化为逐面 MLP；不能让它抛 IndexError
        if edge_index.numel() == 0:
            return F.dropout(F.relu(self.norm(self.self_lin(x))), self.dropout, training=self.training)

        source, target = edge_index[0], edge_index[1]
        message = self.msg_lin(torch.cat([x[source], edge_attr], dim=-1))

        agg = torch.zeros(
            x.shape[0], message.shape[1], dtype=message.dtype, device=message.device
        )
        agg.index_add_(0, target, message)
        degree = torch.zeros(x.shape[0], dtype=message.dtype, device=message.device)
        degree.index_add_(0, target, torch.ones_like(target, dtype=message.dtype))
        agg = agg / degree.clamp(min=1.0).unsqueeze(-1)

        out = self.self_lin(x) + agg
        return F.dropout(F.relu(self.norm(out)), self.dropout, training=self.training)


class FeatureNet(nn.Module):
    def __init__(self, hidden=128, layers=3, num_classes=NUM_CLASSES, dropout=0.2):
        super().__init__()
        dims = [NODE_DIM] + [hidden] * layers
        self.layers = nn.ModuleList(
            SAGELayer(dims[i], dims[i + 1], dropout=dropout) for i in range(layers)
        )
        self.head = nn.Sequential(
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, num_classes),
        )

    def forward(self, batch):
        x = batch["x"]
        edge_index = batch["edge_index"]
        edge_attr = batch["edge_attr"]
        for layer in self.layers:
            x = layer(x, edge_index, edge_attr)
        return self.head(x)


def class_weights(counts, num_classes=NUM_CLASSES, beta=0.999):
    """按有效样本数加权（Cui et al., 2019）。

    直接用 1/freq 会让极少数类（O-ring 只有几十个面）权重爆炸，
    有效样本数 (1-β^n)/(1-β) 在 n 很大时饱和，比 1/n 稳。
    """
    weights = []
    for index in range(num_classes):
        count = counts.get(index, 0)
        if count == 0:
            weights.append(0.0)
            continue
        effective = (1.0 - beta ** count) / (1.0 - beta) if count > 1 else 1.0
        weights.append(1.0 / effective)
    tensor = torch.tensor(weights, dtype=torch.float32)
    # 归一化到均值 1，避免整体学习率被权重尺度带偏
    active = tensor > 0
    if active.any():
        tensor = tensor / tensor[active].mean()
    return tensor
